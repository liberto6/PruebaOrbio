from datetime import datetime, timezone

import pytest

from backend import conversation
from backend.conversation import (
    GREETING,
    CoordinationError,
    guard_interpretation,
    handle_message,
    next_action,
    resume_turn,
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
from backend.storage import InvalidExtraction, init_db, load_application, load_turn

NOW = datetime(2026, 11, 5, 10, 30, tzinfo=timezone.utc)
NAMES = tuple(CandidateFields.model_fields)


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


class FakeLLM:
    """Scripted model. Entries are consumed in order; an exception entry is raised."""

    def __init__(self, interpretations=(), replies=(), summaries=()):
        self.interpretations = list(interpretations)
        self.replies = list(replies)
        self.summaries = list(summaries)
        self.calls = []
        self.replied = 0
        self.before_call = None  # Runs at the start of every call, given the call's name.

    def _next(self, script, default):
        entry = script.pop(0) if script else default
        if isinstance(entry, Exception):
            raise entry
        return entry

    def _start(self, name, context, action=None):
        self.calls.append((name, context, action))
        if self.before_call is not None:
            self.before_call(name)

    def interpret(self, context):
        self._start("interpret", context)
        return self._next(self.interpretations, interpretation())

    def write_reply(self, context, action):
        self._start("write_reply", context, action)
        self.replied += 1
        return self._next(self.replies, f"reply {self.replied}")

    def write_summary(self, context):
        self._start("write_summary", context)
        return self._next(self.summaries, "INTERNAL SUMMARY")

    @property
    def actions(self):
        return [action for name, _, action in self.calls if name == "write_reply"]

    def contexts(self, name):
        return [context for call, context, _ in self.calls if call == name]


def clock():
    return NOW


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "test.db"
    init_db(path)
    return path


@pytest.fixture
def app_id(db):
    return start_application(db).id


class Chat:
    """Sends numbered candidate messages through one scripted model."""

    def __init__(self, db, app_id, llm):
        self.db, self.app_id, self.llm = db, app_id, llm
        self.sent = 0

    def send(self, text="mensaje", *script):
        self.llm.interpretations.extend(script)
        self.sent += 1
        return handle_message(
            self.db, self.app_id, f"request-{self.sent}", text, self.llm, now=clock
        )

    def resume(self):
        return resume_turn(self.db, self.app_id, f"request-{self.sent}", self.llm, now=clock)

    def repeat(self, text="mensaje"):
        return handle_message(
            self.db, self.app_id, f"request-{self.sent}", text, self.llm, now=clock
        )


@pytest.fixture
def chat(db, app_id):
    return Chat(db, app_id, FakeLLM())


def texts(stored):
    return [(message.role, message.text) for message in stored.messages]


def test_qualified_path_with_early_information_and_a_correction(chat, db, app_id):
    chat.send("Soy Ana Ruiz, tengo carné y quiero Madrid", interpretation(NAME, LICENSE, MADRID))
    assert chat.llm.actions == [ASK("availability")]

    barcelona = resolved("work_locations", places(("ES", "Barcelona")))
    chat.send("Mejor Barcelona. Jornada completa", interpretation(barcelona, AVAILABILITY))
    assert chat.llm.actions[-1] == ASK("preferred_schedule")
    chat.send("Mañanas", interpretation(SCHEDULE))
    assert chat.llm.actions[-1] == ASK("delivery_experience")
    chat.send("Ninguna", interpretation(EXPERIENCE))
    assert chat.llm.actions[-1] == ASK("start_timing")
    stored = chat.send("No lo sé", interpretation(START))

    assert chat.llm.actions[-1] == CLOSE
    snapshot = stored.snapshot
    assert snapshot.outcome == "qualified"
    assert snapshot.outcome_reasons == ["screening_passed"]
    assert snapshot.closed_at is not None
    assert snapshot.summary == "INTERNAL SUMMARY"
    assert snapshot.fields.work_locations.normalized.identified[0].locality == "barcelona"
    assert all(getattr(snapshot.fields, name).clarifications_asked == 0 for name in NAMES)
    # Greeting + 5 candidate messages + 4 questions + the closing message.
    assert len(stored.messages) == 11
    assert stored.messages[-1].text == "reply 5"
    turn = load_turn(db, app_id, "request-5")
    assert (turn.phase, turn.reply_sequence) == ("completed", 11)


def test_no_license_closes_early_keeping_null_fields(chat):
    chat.send("Soy Ana Ruiz", interpretation(NAME))
    stored = chat.send("No tengo carné", interpretation(NO_LICENSE))

    assert chat.llm.actions[-1] == CLOSE
    assert stored.snapshot.outcome == "disqualified"
    assert stored.snapshot.outcome_reasons == ["no_driving_license"]
    assert stored.snapshot.summary == "INTERNAL SUMMARY"
    dumped = stored.snapshot.model_dump()["fields"]
    assert tuple(dumped) == NAMES
    assert dumped["work_locations"]["normalized"] is None
    assert dumped["work_locations"]["status"] == "missing"
    context = chat.llm.contexts("write_summary")[-1]
    assert (context.outcome, context.outcome_reasons) == ("disqualified", ["no_driving_license"])


@pytest.mark.parametrize(
    "answer, expected_action, outcome",
    [
        (resolved("driving_license", True), ASK("work_locations"), "incomplete"),
        (resolved("driving_license", False), CLOSE, "disqualified"),
    ],
)
def test_getting_a_license_needs_clarifying_and_then_goes_either_way(
    chat, answer, expected_action, outcome
):
    chat.send("Ana Ruiz", interpretation(NAME))
    stored = chat.send(
        "Me lo estoy sacando",
        interpretation(unresolved("driving_license", reason="license_in_progress")),
    )
    assert chat.llm.actions[-1] == CLARIFY("driving_license", 1)
    assert stored.snapshot.last_question_kind == "clarification"
    assert stored.snapshot.fields.driving_license.normalized is None

    stored = chat.send("Respuesta clara", interpretation(answer))
    assert chat.llm.actions[-1] == expected_action
    assert stored.snapshot.outcome == outcome
    assert stored.snapshot.fields.driving_license.clarifications_asked == 1


def test_repeated_ambiguity_reaches_review_after_exactly_two_clarifications(chat):
    doubt = interpretation(unresolved("availability", raw="depende"))
    chat.send("Todo", interpretation(NAME, LICENSE, MADRID))
    chat.send("depende", doubt)
    chat.send("depende", doubt)
    stored = chat.send("depende", doubt)

    assert chat.llm.actions == [
        ASK("availability"),
        CLARIFY("availability", 1),
        CLARIFY("availability", 2),
        ASK("preferred_schedule"),
    ]
    assert stored.snapshot.fields.availability.clarifications_asked == 2

    chat.send("Mañanas", interpretation(SCHEDULE))
    chat.send("Ninguna", interpretation(EXPERIENCE))
    stored = chat.send("No lo sé", interpretation(START))
    assert stored.snapshot.outcome == "pending_review"
    assert stored.snapshot.outcome_reasons == ["availability_unresolved"]
    assert stored.snapshot.fields.availability.clarifications_asked == 2


def test_stop_then_resume_keeps_the_budget_and_the_question_context(chat):
    chat.send("Ana Ruiz", interpretation(NAME))
    chat.send("ya veremos", interpretation(unresolved("driving_license")))
    assert chat.llm.actions[-1] == CLARIFY("driving_license", 1)

    stored = chat.send("Déjalo", interpretation(stop_intent="stop"))
    assert chat.llm.actions[-1] == STOP
    assert stored.snapshot.stop_requested is True
    assert stored.snapshot.outcome == "incomplete"
    assert stored.snapshot.summary is None
    assert stored.snapshot.last_question_field == "driving_license"
    assert stored.snapshot.last_question_kind == "clarification"

    stored = chat.send("Sigamos", interpretation(stop_intent="resume"))
    assert stored.snapshot.stop_requested is False
    assert chat.llm.actions[-1] == CLARIFY("driving_license", 2)
    assert stored.snapshot.fields.driving_license.clarifications_asked == 2


def test_unrelated_message_while_stopped_stays_stopped(chat):
    chat.send("Ana Ruiz", interpretation(NAME))
    chat.send("Déjalo", interpretation(stop_intent="stop"))
    stored = chat.send("¿Qué tiempo hace?", interpretation())

    assert chat.llm.actions[-2:] == [STOP, STOP]
    assert stored.snapshot.stop_requested is True
    assert stored.snapshot.closed_at is None


def test_stop_with_a_disqualifying_fact_closes_only_after_resuming(chat):
    chat.send("Déjalo, no tengo carné", interpretation(NO_LICENSE, stop_intent="stop"))
    assert chat.llm.actions[-1] == STOP
    stored = chat.send("Vale, sigamos", interpretation(stop_intent="resume"))
    assert chat.llm.actions[-1] == CLOSE
    assert stored.snapshot.outcome == "disqualified"


def test_job_questions_are_stored_without_affecting_eligibility(chat):
    question = "¿Cuánto se paga?"
    stored = chat.send(
        question, interpretation(NAME, LICENSE, MADRID, AVAILABILITY, questions=[question])
    )
    assert stored.snapshot.pending_questions == [question]
    assert chat.llm.actions[-1] == ASK("preferred_schedule")

    chat.send("Mañanas", interpretation(SCHEDULE))
    chat.send("Ninguna", interpretation(EXPERIENCE))
    stored = chat.send("No lo sé", interpretation(START))
    assert stored.snapshot.outcome == "qualified"
    assert stored.snapshot.pending_questions == [question]


def test_language_preference_is_kept_until_changed(chat):
    stored = chat.send("Hi, I am Ana Ruiz", interpretation(NAME, language="en"))
    assert stored.snapshot.language == "en"
    stored = chat.send("Yes", interpretation(LICENSE))
    assert stored.snapshot.language == "en"
    assert chat.llm.contexts("write_reply")[-1].language == "en"


def test_contexts_hold_saved_state_and_the_injected_clock(chat):
    chat.send("Soy Ana Ruiz", interpretation(NAME))

    interpret, reply = chat.llm.contexts("interpret")[0], chat.llm.contexts("write_reply")[0]
    assert texts(interpret)[-1] == ("candidate", "Soy Ana Ruiz")
    assert interpret.fields.full_name.status == "missing"
    assert (interpret.last_question_field, interpret.last_question_kind) == ("full_name", "initial")
    assert {(place.country, place.locality) for place in interpret.catalog} == set(COVERAGE_CATALOG)
    assert interpret.now == NOW
    assert reply.fields.full_name.status == "resolved"
    assert reply.now == NOW
    assert reply.outcome is None
