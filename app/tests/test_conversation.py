from datetime import datetime, timezone

import pytest

from backend import conversation
from backend.conversation import CoordinationError, next_action
from backend.llm import Action
from backend.rules import (
    CandidateFields,
    Evaluation,
    Proposal,
    apply_proposals,
    new_snapshot,
    record_clarifications,
)

NOW = datetime(2026, 11, 5, 10, 30, tzinfo=timezone.utc)


def resolved(field, value, raw="evidence"):
    return Proposal(field=field, status="resolved", value=value, raw=raw, reason=None)


def unresolved(field, raw="evidence", reason="ambiguous"):
    return Proposal(field=field, status="unresolved", value=None, raw=raw, reason=reason)


def places(*identified, unresolved=()):
    return {
        "identified": [{"country": c, "locality": l} for c, l in identified],
        "unresolved": list(unresolved),
    }


NAME = resolved("full_name", "Ana Ruiz")
LICENSE = resolved("driving_license", True)
NO_LICENSE = resolved("driving_license", False)
MADRID = resolved("work_locations", places(("ES", "madrid")))
AVAILABILITY = resolved("availability", {"options": ["full_time"], "notes": None})
SCHEDULE = resolved("preferred_schedule", {"options": ["morning"], "notes": None})
EXPERIENCE = resolved("delivery_experience", {"amount": 0, "unit": "years", "platforms": []})
START = resolved("start_timing", {"kind": "unknown", "value": None})
ALL_RESOLVED = [NAME, LICENSE, MADRID, AVAILABILITY, SCHEDULE, EXPERIENCE, START]


def with_fields(*proposals):
    """All resolved, with these proposals replacing the ones for the same field."""
    replaced = {proposal.field for proposal in proposals}
    return [p for p in ALL_RESOLVED if p.field not in replaced] + list(proposals)


def snapshot_with(proposals=(), asked=(), stop=False):
    snapshot = new_snapshot("application", NOW)
    fields = apply_proposals(snapshot.fields, list(proposals))
    assert fields.errors == []
    counted = record_clarifications(fields.fields, list(asked))
    assert counted.errors == []
    return snapshot.model_copy(update={"fields": counted.fields, "stop_requested": stop})


ASK = lambda field: Action(kind="ask", field=field)  # noqa: E731
CLARIFY = lambda field, attempt: Action(kind="clarify", field=field, attempt=attempt)  # noqa: E731
CLOSE = Action(kind="close")
STOP = Action(kind="acknowledge_stop")
UNRESOLVED_COVERAGE = resolved("work_locations", places(unresolved=["por el centro"]))
BILBAO = resolved("work_locations", places(("ES", "bilbao")))

NEXT_ACTION_CASES = {
    # Rule 1: a stop request goes first, even over a confirmed rejection.
    "stop on a new application": ([], [], True, STOP),
    "stop beats disqualified": ([NO_LICENSE], [], True, STOP),
    "stop beats a finished walk": (ALL_RESOLVED, [], True, STOP),
    # Rule 2: a confirmed rejection closes, with or without doubts remaining.
    "no license with everything else missing": ([NO_LICENSE], [], False, CLOSE),
    "no license with doubts": (
        [unresolved("full_name"), NO_LICENSE],
        [],
        False,
        CLOSE,
    ),
    "uncovered with doubts and budget": (
        [unresolved("driving_license"), BILBAO],
        [],
        False,
        CLOSE,
    ),
    # Rule 3: the walk asks missing fields in order and skips the satisfied ones.
    "new application asks the name": ([], [], False, ASK("full_name")),
    "first missing in order": ([NAME], [], False, ASK("driving_license")),
    "early information is not asked again": (
        [NAME, LICENSE, AVAILABILITY, SCHEDULE],
        [],
        False,
        ASK("work_locations"),
    ),
    "gap in the middle": (
        [NAME, LICENSE, MADRID, AVAILABILITY, EXPERIENCE, START],
        [],
        False,
        ASK("preferred_schedule"),
    ),
    # Rule 3: an unresolved field is clarified before later missing fields.
    "unresolved before later missing": (
        [NAME, unresolved("driving_license")],
        [],
        False,
        CLARIFY("driving_license", 1),
    ),
    "earlier missing before later unresolved": (
        [unresolved("driving_license")],
        [],
        False,
        ASK("full_name"),
    ),
    "second clarification offers options": (
        [NAME, unresolved("driving_license")],
        ["driving_license"],
        False,
        CLARIFY("driving_license", 2),
    ),
    # Trap: this state is closeable as pending_review, but budget remains.
    "pending review with budget clarifies": (
        with_fields(unresolved("availability")),
        [],
        False,
        CLARIFY("availability", 1),
    ),
    # Rule 3: resolved locations whose coverage is still open.
    "unresolved coverage clarifies": (
        with_fields(UNRESOLVED_COVERAGE),
        [],
        False,
        CLARIFY("work_locations", 1),
    ),
    "unresolved coverage at the limit moves on": (
        [NAME, LICENSE, UNRESOLVED_COVERAGE],
        ["work_locations", "work_locations"],
        False,
        ASK("availability"),
    ),
    "uncovered and unclear option is not a rejection": (
        with_fields(
            resolved("work_locations", places(("ES", "bilbao"), unresolved=["por el centro"]))
        ),
        [],
        False,
        CLARIFY("work_locations", 1),
    ),
    "one covered option needs no clarification": (
        with_fields(
            resolved("work_locations", places(("ES", "madrid"), unresolved=["por el centro"]))
        ),
        [],
        False,
        CLOSE,
    ),
    "unresolved work location": (
        [NAME, LICENSE, unresolved("work_locations")],
        ["work_locations"],
        False,
        CLARIFY("work_locations", 2),
    ),
    # Rule 3: at the limit the field stays for review and the walk continues.
    "limit reached moves to the next missing": (
        [NAME, unresolved("driving_license")],
        ["driving_license", "driving_license"],
        False,
        ASK("work_locations"),
    ),
    "limit reached moves to the next unresolved": (
        [NAME, unresolved("driving_license"), MADRID, unresolved("availability")],
        ["driving_license", "driving_license"],
        False,
        CLARIFY("availability", 1),
    ),
    # Rule 4: start timing only gets one clarification.
    "start timing first clarification": (
        with_fields(unresolved("start_timing")),
        [],
        False,
        CLARIFY("start_timing", 1),
    ),
    "start timing limit is one": (
        with_fields(unresolved("start_timing")),
        ["start_timing"],
        False,
        CLOSE,
    ),
    "other field still has a second clarification": (
        with_fields(unresolved("delivery_experience")),
        ["delivery_experience"],
        False,
        CLARIFY("delivery_experience", 2),
    ),
    # Rule 5: nothing left to ask.
    "all resolved closes": (ALL_RESOLVED, [], False, CLOSE),
    "doubts out of budget close": (
        with_fields(unresolved("full_name"), unresolved("start_timing")),
        ["full_name", "full_name", "start_timing"],
        False,
        CLOSE,
    ),
    "unresolved coverage out of budget closes": (
        with_fields(UNRESOLVED_COVERAGE),
        ["work_locations", "work_locations"],
        False,
        CLOSE,
    ),
    "a covered correction needs no budget": (
        ALL_RESOLVED,
        ["work_locations", "work_locations"],
        False,
        CLOSE,
    ),
}


@pytest.mark.parametrize(
    "proposals, asked, stop, expected", NEXT_ACTION_CASES.values(), ids=NEXT_ACTION_CASES.keys()
)
def test_next_action(proposals, asked, stop, expected):
    assert next_action(snapshot_with(proposals, asked, stop)) == expected


def test_next_action_walks_every_field_in_candidate_fields_order():
    proposals = []
    for proposal, name in zip(ALL_RESOLVED, CandidateFields.model_fields):
        assert next_action(snapshot_with(proposals)) == ASK(name)
        proposals.append(proposal)
    assert next_action(snapshot_with(proposals)) == CLOSE


def test_next_action_never_invents_a_close_for_an_unexpected_state(monkeypatch):
    not_closeable = Evaluation(outcome="incomplete", outcome_reasons=[], closeable=False)
    monkeypatch.setattr(conversation, "evaluate", lambda fields: not_closeable)
    with pytest.raises(CoordinationError):
        next_action(snapshot_with(ALL_RESOLVED))
