from datetime import datetime, timezone

import pytest

from backend import conversation
from backend.conversation import (
    GREETING,
    CoordinationError,
    guard_interpretation,
    next_action,
    start_application,
    technical_error_text,
)
from backend.llm import Action, Interpretation
from backend.rules import (
    COVERAGE_CATALOG,
    CandidateFields,
    Evaluation,
    FieldError,
    Proposal,
    apply_proposals,
    evaluate_coverage,
    new_snapshot,
    record_clarifications,
)
from backend.storage import InvalidExtraction, init_db, load_application

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


def interpretation(*proposals, questions=(), language=None, stop_intent=None):
    return Interpretation(
        proposals=list(proposals),
        job_questions=list(questions),
        language=language,
        stop_intent=stop_intent,
    )


def guarded_location(country, locality):
    result = guard_interpretation(
        interpretation(resolved("work_locations", places((country, locality))))
    )
    return result.proposals[0].value["identified"][0]


@pytest.mark.parametrize(
    "country, locality, expected, coverage",
    [
        ("ES", "madrid", "madrid", "covered"),
        ("ES", "Madrid", "madrid", "covered"),
        ("ES", "  MÁDRID\n", "madrid", "covered"),
        ("ES", "Sevilla", "sevilla", "covered"),
        ("ES", "Seville", "sevilla", "covered"),
        ("ES", "Barcelona", "barcelona", "covered"),
        ("ES", "Valencia", "valencia", "covered"),
        ("MX", "Ciudad de México", "ciudad_de_mexico", "covered"),
        ("MX", "ciudad  de mexico", "ciudad_de_mexico", "covered"),
        ("MX", "CDMX", "ciudad_de_mexico", "covered"),
        ("MX", "Mexico City", "ciudad_de_mexico", "covered"),
        ("MX", "ciudad_de_mexico", "ciudad_de_mexico", "covered"),
        ("MX", "Guadalajara", "guadalajara", "covered"),
        ("MX", "Monterrey", "monterrey", "covered"),
        ("MX", "Puebla", "puebla", "covered"),
        # Well-formed places outside the catalog stay legitimate and not covered.
        ("ES", "bilbao", "bilbao", "not_covered"),
        ("ES", "guadalajara", "guadalajara", "not_covered"),
        ("MX", "valencia", "valencia", "not_covered"),
    ],
)
def test_catalog_guard_normalizes_known_spellings_per_country(
    country, locality, expected, coverage
):
    place = guarded_location(country, locality)
    assert place == {"country": country, "locality": expected}
    snapshot = snapshot_with([resolved("work_locations", places((country, expected)))])
    assert evaluate_coverage(snapshot.fields.work_locations) == coverage


@pytest.mark.parametrize(
    "country, locality",
    [
        ("España", "madrid"),
        ("es", "madrid"),
        ("ES\n", "madrid"),
        ("ESP", "madrid"),
        ("ES", "Bilbao"),
        ("ES", "bilbao\n"),
        ("ES", "san sebastián"),
        ("ES", "Guadalajara"),  # Aliases are per country: Guadalajara alias belongs to MX.
        ("MX", "Madrid"),
        ("ES", "Mexico City"),
        ("ES", "Madriz"),  # No fuzzy matching.
    ],
)
def test_catalog_guard_rejects_malformed_identifiers_as_technical_failures(country, locality):
    with pytest.raises(InvalidExtraction):
        guarded_location(country, locality)


def test_catalog_guard_checks_every_location_and_keeps_unresolved_options():
    mixed = resolved(
        "work_locations", places(("ES", "Madrid"), ("ES", "bilbao"), unresolved=["por el centro"])
    )
    assert guard_interpretation(interpretation(mixed)).proposals[0].value == places(
        ("ES", "madrid"), ("ES", "bilbao"), unresolved=["por el centro"]
    )
    with pytest.raises(InvalidExtraction):
        guard_interpretation(
            interpretation(resolved("work_locations", places(("ES", "madrid"), ("ES", "Bilbao"))))
        )


def test_catalog_aliases_only_point_at_catalog_entries():
    aliases = conversation.LOCALITY_ALIASES
    for country, spellings in aliases.items():
        for target in spellings.values():
            assert (country, target) in COVERAGE_CATALOG
    assert {(c, t) for c, s in aliases.items() for t in s.values()} == set(COVERAGE_CATALOG)


@pytest.mark.parametrize(
    "bad_value",
    ["Madrid", {"identified": [{"country": "ES"}], "unresolved": []}, {"identified": []}, None],
)
def test_guard_rejects_resolved_locations_with_a_bad_shape(bad_value):
    bad = Proposal(field="work_locations", status="resolved", value=bad_value, raw="x", reason=None)
    with pytest.raises(InvalidExtraction):
        guard_interpretation(interpretation(bad))


@pytest.mark.parametrize(
    "not_an_interpretation",
    [None, "texto", {"proposals": []}, [], Action(kind="close")],
)
def test_guard_rejects_anything_that_is_not_an_interpretation(not_an_interpretation):
    with pytest.raises(InvalidExtraction):
        guard_interpretation(not_an_interpretation)


def test_guard_rejects_duplicate_proposals_for_a_field():
    with pytest.raises(InvalidExtraction) as caught:
        guard_interpretation(interpretation(NAME, LICENSE, resolved("full_name", "Ana Gil")))
    assert caught.value.errors == [FieldError(field="full_name", error="duplicate_proposal")]


def test_guard_passes_everything_else_through_unchanged():
    original = interpretation(
        NAME,
        unresolved("work_locations", raw="Madrid o algo así"),
        questions=["¿Cuánto se paga?"],
        language="en",
        stop_intent="resume",
    )
    assert guard_interpretation(original) == original


def test_start_application_stores_the_fixed_greeting_once(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    snapshot = start_application(db)
    stored = load_application(db, snapshot.id)
    assert [(m.role, m.text) for m in stored.messages] == [("assistant", GREETING)]
    assert stored.snapshot.last_question_field == "full_name"
    assert stored.snapshot.last_question_kind == "initial"
    assert stored.snapshot.fields.full_name.clarifications_asked == 0
    assert len(load_application(db, snapshot.id).messages) == 1


def test_greeting_identifies_the_virtual_assistant_and_asks_the_full_name():
    assert "asistente virtual de Grupo Sazón" in GREETING
    assert GREETING.endswith("¿Cuál es tu nombre completo?")


def test_technical_error_text_follows_the_language():
    spanish, english = technical_error_text("es"), technical_error_text("en")
    assert "problema técnico" in spanish
    assert "technical problem" in english
