import unicodedata
from datetime import date, datetime
from typing import Annotated, Any, Generic, Literal, TypeVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

T = TypeVar("T")

NonBlank = Annotated[str, StringConstraints(pattern=r"\S")]
Duration = Annotated[float, Field(ge=0, allow_inf_nan=False)]
Outcome = Literal["incomplete", "qualified", "disqualified", "pending_review"]
FieldName = Literal[
    "full_name",
    "driving_license",
    "work_locations",
    "availability",
    "preferred_schedule",
    "delivery_experience",
    "start_timing",
]
Language = Literal["es", "en"]
QuestionKind = Literal["initial", "clarification"]
MAX_CLARIFICATIONS = 2


def _is_iso_date(text: str) -> bool:
    try:
        return date.fromisoformat(text).isoformat() == text
    except ValueError:
        return False


class StrictModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class Location(StrictModel):
    country: NonBlank
    locality: NonBlank


class WorkLocations(StrictModel):
    identified: list[Location]
    unresolved: list[NonBlank]

    @model_validator(mode="after")
    def has_at_least_one_location(self):
        if not self.identified and not self.unresolved:
            raise ValueError("work locations need an identified or unresolved option")
        return self


class OptionsWithNotes(StrictModel, Generic[T]):
    options: list[T]
    notes: NonBlank | None

    @model_validator(mode="after")
    def has_some_information(self):
        if not self.options and self.notes is None:
            raise ValueError("empty options need notes that keep the restriction")
        return self


Availability = OptionsWithNotes[Literal["full_time", "part_time", "weekends"]]
PreferredSchedule = OptionsWithNotes[Literal["morning", "afternoon", "evening", "flexible"]]


class StartTiming(StrictModel):
    kind: Literal["date", "period", "condition", "unknown"]
    value: NonBlank | None

    @model_validator(mode="after")
    def value_matches_kind(self):
        if self.kind == "unknown":
            if self.value is not None:
                raise ValueError("unknown start timing has a null value")
        elif self.value is None:
            raise ValueError(f"{self.kind} start timing needs a value")
        elif self.kind == "date" and not _is_iso_date(self.value):
            raise ValueError("date start timing needs an ISO date (YYYY-MM-DD)")
        return self


class DeliveryExperience(StrictModel):
    years: Duration
    platforms: list[NonBlank]


class ExperienceProposal(StrictModel):
    amount: Duration
    unit: Literal["years", "months"]
    platforms: list[NonBlank]


class FieldState(StrictModel, Generic[T]):
    raw: NonBlank | None
    normalized: T | None
    status: Literal["missing", "resolved", "unresolved"]
    reason: NonBlank | None
    clarifications_asked: int = Field(ge=0, le=MAX_CLARIFICATIONS)

    @model_validator(mode="after")
    def status_matches_values(self):
        if self.status == "missing":
            if self.raw is not None or self.normalized is not None or self.reason is not None:
                raise ValueError("a missing field has null raw, normalized and reason")
        elif self.status == "unresolved":
            if self.normalized is not None or self.raw is None or self.reason is None:
                raise ValueError("an unresolved field has raw, a reason and null normalized")
        elif self.normalized is None or self.raw is None:
            raise ValueError("a resolved field has raw and a normalized value")
        return self


class CandidateFields(StrictModel):
    full_name: FieldState[NonBlank]
    driving_license: FieldState[bool]
    work_locations: FieldState[WorkLocations]
    availability: FieldState[Availability]
    preferred_schedule: FieldState[PreferredSchedule]
    delivery_experience: FieldState[DeliveryExperience]
    start_timing: FieldState[StartTiming]


class CandidateSnapshot(StrictModel):
    id: str
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None
    outcome: Outcome
    outcome_reasons: list[str]
    fields: CandidateFields
    pending_questions: list[str]
    summary: str | None
    recruiter_decision: Literal["unreviewed", "advance", "rejected"]
    revision: int
    stop_requested: bool
    # Defaults let snapshots stored before these keys existed keep loading.
    language: Language = "es"
    last_question_field: FieldName | None = None
    last_question_kind: QuestionKind | None = None
    reminder_sent: bool = False


class Proposal(StrictModel):
    field: FieldName
    status: Literal["resolved", "unresolved"]
    value: Any
    raw: NonBlank | None
    reason: NonBlank | None


class FieldError(StrictModel):
    field: str | None
    error: str


class ApplyResult(StrictModel):
    fields: CandidateFields
    errors: list[FieldError]


MISSING_FIELD = {
    "raw": None,
    "normalized": None,
    "status": "missing",
    "reason": None,
    "clarifications_asked": 0,
}


def new_snapshot(snapshot_id: str, now: datetime) -> CandidateSnapshot:
    return CandidateSnapshot(
        id=snapshot_id,
        created_at=now,
        updated_at=now,
        closed_at=None,
        outcome="incomplete",
        outcome_reasons=[],
        fields=CandidateFields(
            **{name: MISSING_FIELD for name in CandidateFields.model_fields}
        ),
        pending_questions=[],
        summary=None,
        recruiter_decision="unreviewed",
        revision=0,
        stop_requested=False,
    )


def apply_proposals(fields: CandidateFields, proposals: list) -> ApplyResult:
    updated = fields
    errors = []
    for submitted in proposals:
        try:
            proposal = Proposal.model_validate(submitted)
            current = getattr(updated, proposal.field)
            new_state = _state_from_proposal(proposal, current)
        except ValidationError as exc:
            errors.append(FieldError(field=_submitted_field(submitted), error=_describe(exc)))
        else:
            updated = updated.model_copy(update={proposal.field: new_state})
    return ApplyResult(fields=updated, errors=errors)


def _state_from_proposal(proposal: Proposal, current: FieldState) -> FieldState:
    normalized = proposal.value
    status = proposal.status
    reason = proposal.reason
    if proposal.field == "delivery_experience" and status == "resolved":
        experience = ExperienceProposal.model_validate(proposal.value)
        problem = _experience_problem(experience)
        if problem:
            normalized, status, reason = None, "unresolved", problem
        else:
            normalized = DeliveryExperience(
                years=_to_years(experience), platforms=experience.platforms
            )
    # type(current) is the field's FieldState[...], so it validates the value's type.
    return type(current)(
        raw=proposal.raw,
        normalized=normalized,
        status=status,
        reason=reason,
        clarifications_asked=current.clarifications_asked,
    )


def _experience_problem(experience: ExperienceProposal) -> str | None:
    if experience.amount == 0 and experience.platforms:
        return "experience_zero_with_platforms"
    if experience.amount > 0 and not experience.platforms:
        return "experience_without_platforms"
    return None


def _to_years(experience: ExperienceProposal) -> float:
    if experience.unit == "months":
        return experience.amount / 12
    return experience.amount


def record_clarifications(fields: CandidateFields, field_names: list[str]) -> ApplyResult:
    updated = fields
    errors = []
    for name in field_names:
        if not isinstance(name, str):
            errors.append(FieldError(field=None, error="unknown_field"))
            continue
        if name not in CandidateFields.model_fields:
            errors.append(FieldError(field=name, error="unknown_field"))
            continue
        current = getattr(updated, name)
        if current.clarifications_asked >= MAX_CLARIFICATIONS:
            errors.append(FieldError(field=name, error="clarification_limit_reached"))
            continue
        counted = current.model_copy(
            update={"clarifications_asked": current.clarifications_asked + 1}
        )
        updated = updated.model_copy(update={name: counted})
    return ApplyResult(fields=updated, errors=errors)


def _submitted_field(submitted: Any) -> str | None:
    if isinstance(submitted, dict) and isinstance(submitted.get("field"), str):
        return submitted["field"]
    return None


def _describe(exc: ValidationError) -> str:
    parts = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"])
        parts.append(f"{location}: {error['msg']}" if location else error["msg"])
    return "; ".join(parts)


COVERAGE_CATALOG = frozenset(
    {
        ("ES", "madrid"),
        ("ES", "barcelona"),
        ("ES", "valencia"),
        ("ES", "sevilla"),
        ("MX", "ciudad_de_mexico"),
        ("MX", "guadalajara"),
        ("MX", "monterrey"),
        ("MX", "puebla"),
    }
)


def folded(text: str) -> str:
    """A place spelling without case, accents or separators; this is not fuzzy matching."""
    decomposed = unicodedata.normalize("NFKD", text)
    without_accents = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(without_accents.casefold().replace("_", " ").replace("-", " ").split())


class CatalogError(Exception):
    pass


class Evaluation(StrictModel):
    outcome: Outcome
    outcome_reasons: list[str]
    closeable: bool


def evaluate_coverage(work_locations: FieldState, catalog=COVERAGE_CATALOG) -> str:
    if not catalog:
        raise CatalogError("the coverage catalog is missing or empty")
    locations = work_locations.normalized
    if locations is None:
        return "unresolved"
    if any((place.country, place.locality) in catalog for place in locations.identified):
        return "covered"
    if locations.unresolved:
        return "unresolved"
    return "not_covered"


def evaluate(fields: CandidateFields, catalog=COVERAGE_CATALOG) -> Evaluation:
    coverage = evaluate_coverage(fields.work_locations, catalog)

    failures = []
    if fields.driving_license.normalized is False:
        failures.append("no_driving_license")
    if coverage == "not_covered":
        failures.append("location_not_covered")
    if failures:
        return Evaluation(outcome="disqualified", outcome_reasons=failures, closeable=True)

    missing = _fields_with_status(fields, "missing")
    if missing:
        reasons = [f"{name}_missing" for name in missing]
        return Evaluation(outcome="incomplete", outcome_reasons=reasons, closeable=False)

    reasons = [f"{name}_unresolved" for name in _fields_with_status(fields, "unresolved")]
    if coverage == "unresolved" and fields.work_locations.status == "resolved":
        reasons.append("coverage_unresolved")
    if reasons:
        return Evaluation(outcome="pending_review", outcome_reasons=reasons, closeable=True)

    # Here every field is resolved, the license is true and coverage is confirmed.
    return Evaluation(outcome="qualified", outcome_reasons=["screening_passed"], closeable=True)


def _fields_with_status(fields: CandidateFields, status: str) -> list[str]:
    return [name for name in CandidateFields.model_fields if getattr(fields, name).status == status]
