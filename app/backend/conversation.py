"""Which action comes next and how a candidate turn advances through its phases."""

from backend.llm import Action
from backend.rules import (
    MAX_CLARIFICATIONS,
    CandidateFields,
    CandidateSnapshot,
    evaluate,
    evaluate_coverage,
)

# E2 only enforces the general limit, so the shorter start timing limit lives here.
CLARIFICATION_BUDGET = {"start_timing": 1}


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
