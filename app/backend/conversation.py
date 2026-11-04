"""Which action comes next and how a candidate turn advances through its phases."""

import re
import unicodedata
from datetime import datetime, timezone

from pydantic import ValidationError

from backend.llm import (
    Action,
    Interpretation,
    LLMFailure,
    ScreeningLLM,
    TransientLLMFailure,
    TurnContext,
)
from backend.rules import (
    COVERAGE_CATALOG,
    MAX_CLARIFICATIONS,
    CandidateFields,
    CandidateSnapshot,
    FieldError,
    Location,
    Proposal,
    WorkLocations,
    evaluate,
    evaluate_coverage,
)
from backend.storage import (
    CannotClose,
    ClarificationRefused,
    InvalidExtraction,
    InvalidReply,
    StaleRevision,
    StorageFailure,
    StoredApplication,
    WrongTurnPhase,
    close_application,
    commit_interpretation,
    complete_turn,
    create_application,
    load_application,
    load_turn,
    receive_turn,
)

GREETING = (
    "Hola, soy el asistente virtual de Grupo Sazón. Voy a hacerte unas preguntas breves "
    "para tu candidatura al puesto de reparto. ¿Cuál es tu nombre completo?"
)
TECHNICAL_ERROR_TEXTS = {
    "es": "Ha habido un problema técnico. Por favor, inténtalo de nuevo.",
    "en": "There was a technical problem. Please try again.",
}

# E2 only enforces the general limit, so the shorter start timing limit lives here.
CLARIFICATION_BUDGET = {"start_timing": 1}

# Known spellings of catalog places, per country: Guadalajara in Spain is another place.
# Keys are folded by _folded. Anything not listed here must already be a clean identifier.
LOCALITY_ALIASES = {
    "ES": {
        "madrid": "madrid",
        "barcelona": "barcelona",
        "valencia": "valencia",
        "sevilla": "sevilla",
        "seville": "sevilla",
    },
    "MX": {
        "ciudad de mexico": "ciudad_de_mexico",
        "cdmx": "ciudad_de_mexico",
        "mexico city": "ciudad_de_mexico",
        "guadalajara": "guadalajara",
        "monterrey": "monterrey",
        "puebla": "puebla",
    },
}
COUNTRY_ID = re.compile(r"[A-Z]{2}")
LOCALITY_ID = re.compile(r"[a-z0-9_]+")

STOP_REQUESTED = {"stop": True, "resume": False, None: None}
QUESTION_KINDS = {"ask": "initial", "clarify": "clarification"}
MAX_RELOADS = 3  # Times one call may discard its work because the state moved.


class CoordinationError(Exception):
    """The saved state allows neither another question nor a close."""


class TurnFailed(Exception):
    """Technical failure, retryable from the persisted phase. Holds no internal detail."""

    def __init__(self, phase: str | None, code: str):
        super().__init__(code)
        self.phase = phase  # None when no turn could be read or saved.
        self.code = code


def next_action(snapshot: CandidateSnapshot) -> Action:
    """What to do next, decided only from saved state: no I/O and no model."""
    if snapshot.stop_requested:
        return Action(kind="acknowledge_stop")

    fields = snapshot.fields
    evaluation = evaluate(fields)
    if evaluation.outcome == "disqualified":
        return Action(kind="close")

    # `closeable` alone is not enough: pending_review is closeable with budget left.
    for name in CandidateFields.model_fields:
        state = getattr(fields, name)
        if state.status == "missing":
            return Action(kind="ask", field=name)
        budget = CLARIFICATION_BUDGET.get(name, MAX_CLARIFICATIONS)
        if _is_in_doubt(fields, name) and state.clarifications_asked < budget:
            return Action(kind="clarify", field=name, attempt=state.clarifications_asked + 1)

    if evaluation.closeable:
        return Action(kind="close")
    raise CoordinationError(evaluation.outcome)


def _is_in_doubt(fields: CandidateFields, name: str) -> bool:
    if getattr(fields, name).status == "unresolved":
        return True
    # Locations can be resolved while their coverage is still open.
    return name == "work_locations" and evaluate_coverage(fields.work_locations) == "unresolved"


def guard_interpretation(interpretation) -> Interpretation:
    """A usable interpretation with catalog spellings normalized, or InvalidExtraction.

    E2 matches places exactly, so a badly written identifier would count as an uncovered
    place and reject a valid candidate. A failure here is technical, never evidence.
    """
    if not isinstance(interpretation, Interpretation):
        raise InvalidExtraction([FieldError(field=None, error="not_an_interpretation")])
    seen = set()
    proposals = []
    for proposal in interpretation.proposals:
        if proposal.field in seen:
            raise InvalidExtraction([FieldError(field=proposal.field, error="duplicate_proposal")])
        seen.add(proposal.field)
        if proposal.field == "work_locations" and proposal.status == "resolved":
            proposal = _with_canonical_places(proposal)
        proposals.append(proposal)
    return interpretation.model_copy(update={"proposals": proposals})


def _with_canonical_places(proposal: Proposal) -> Proposal:
    try:
        locations = WorkLocations.model_validate(proposal.value)
    except ValidationError as exc:
        malformed = FieldError(field="work_locations", error="malformed_locations")
        raise InvalidExtraction([malformed]) from exc
    identified = [_canonical_place(place) for place in locations.identified]
    value = locations.model_copy(update={"identified": identified}).model_dump()
    return proposal.model_copy(update={"value": value})


def _canonical_place(place: Location) -> Location:
    aliases = LOCALITY_ALIASES.get(place.country, {})
    locality = aliases.get(_folded(place.locality), place.locality)
    if not (COUNTRY_ID.fullmatch(place.country) and LOCALITY_ID.fullmatch(locality)):
        raise InvalidExtraction([FieldError(field="work_locations", error="malformed_catalog_id")])
    return place.model_copy(update={"locality": locality})


def _folded(text: str) -> str:
    """Ignores only case, accents and repeated whitespace; this is not fuzzy matching."""
    decomposed = unicodedata.normalize("NFKD", text)
    without_accents = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(without_accents.casefold().split())


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def handle_message(
    db_path, application_id: str, request_id: str, text: str, llm: ScreeningLLM, now=_utc_now
) -> StoredApplication:
    """Saves the candidate message once and advances its turn as far as it can go."""
    try:
        receive_turn(db_path, application_id, request_id, text)
    except StorageFailure as exc:
        raise TurnFailed(None, "storage_failure") from exc
    return _drive(db_path, application_id, request_id, llm, now)


def resume_turn(
    db_path, application_id: str, request_id: str, llm: ScreeningLLM, now=_utc_now
) -> StoredApplication:
    """Continues a saved turn from its persisted phase."""
    return _drive(db_path, application_id, request_id, llm, now)


def _drive(db_path, application_id, request_id, llm, now) -> StoredApplication:
    phase = None
    reloads = 0
    try:
        while reloads <= MAX_RELOADS:
            # The turn goes first: a completed turn then always reads its committed history.
            phase = load_turn(db_path, application_id, request_id).phase
            stored = load_application(db_path, application_id)
            if phase == "completed":
                return stored
            step = _interpret if phase == "received" else _finish
            try:
                step(db_path, application_id, request_id, stored, llm, now)
            except (StaleRevision, WrongTurnPhase, ClarificationRefused, CannotClose):
                # The state moved under this work: discard it and decide again.
                reloads += 1
    except TransientLLMFailure as exc:
        raise TurnFailed(phase, "llm_transient") from exc
    except LLMFailure as exc:
        raise TurnFailed(phase, "llm_permanent") from exc
    except InvalidExtraction as exc:
        raise TurnFailed(phase, "invalid_interpretation") from exc
    except InvalidReply as exc:
        raise TurnFailed(phase, "invalid_reply") from exc
    except StorageFailure as exc:
        raise TurnFailed(phase, "storage_failure") from exc
    except CoordinationError as exc:
        raise TurnFailed(phase, "coordination_error") from exc
    raise TurnFailed(phase, "state_changed")


def _interpret(db_path, application_id, request_id, stored, llm, now) -> None:
    interpretation = guard_interpretation(llm.interpret(_context(stored, now)))
    commit_interpretation(
        db_path,
        application_id,
        request_id,
        stored.snapshot.revision,
        proposals=interpretation.proposals,
        pending_questions=interpretation.job_questions,
        language=interpretation.language,
        stop_requested=STOP_REQUESTED[interpretation.stop_intent],
    )


def _finish(db_path, application_id, request_id, stored, llm, now) -> None:
    snapshot = stored.snapshot
    action = next_action(snapshot)
    if action.kind == "close":
        evaluation = evaluate(snapshot.fields)
        context = _context(stored, now).model_copy(
            update={
                "outcome": evaluation.outcome,
                "outcome_reasons": evaluation.outcome_reasons,
            }
        )
        final_message = _checked_text(llm.write_reply(context, action))
        summary = _checked_text(llm.write_summary(context))
        close_application(
            db_path,
            application_id,
            snapshot.revision,
            final_message,
            summary,
            request_id=request_id,
        )
        return
    reply = _checked_text(llm.write_reply(_context(stored, now), action))
    complete_turn(
        db_path,
        application_id,
        request_id,
        snapshot.revision,
        reply=reply,
        question_field=action.field,
        question_kind=QUESTION_KINDS.get(action.kind),
    )


def _checked_text(text) -> str:
    if not isinstance(text, str) or not text.strip():
        raise InvalidReply("unusable_text")
    return text


def _context(stored: StoredApplication, now) -> TurnContext:
    """Saved state only, never the summary: a retry has nothing else in memory."""
    snapshot = stored.snapshot
    return TurnContext(
        messages=stored.messages,
        fields=snapshot.fields,
        pending_questions=snapshot.pending_questions,
        language=snapshot.language,
        last_question_field=snapshot.last_question_field,
        last_question_kind=snapshot.last_question_kind,
        catalog=[
            Location(country=country, locality=locality)
            for country, locality in sorted(COVERAGE_CATALOG)
        ],
        now=now(),
    )


def start_application(db_path) -> CandidateSnapshot:
    return create_application(db_path, greeting=GREETING)


def technical_error_text(language: str) -> str:
    return TECHNICAL_ERROR_TEXTS.get(language, TECHNICAL_ERROR_TEXTS["es"])
