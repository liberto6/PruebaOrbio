"""Which action comes next and how a candidate turn advances through its phases."""

import re
import unicodedata

from pydantic import ValidationError

from backend.llm import Action, Interpretation
from backend.rules import (
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
from backend.storage import InvalidExtraction, create_application

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


class CoordinationError(Exception):
    """The saved state allows neither another question nor a close."""


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


def start_application(db_path) -> CandidateSnapshot:
    return create_application(db_path, greeting=GREETING)


def technical_error_text(language: str) -> str:
    return TECHNICAL_ERROR_TEXTS.get(language, TECHNICAL_ERROR_TEXTS["es"])
