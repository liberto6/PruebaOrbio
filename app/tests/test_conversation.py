import sqlite3
from datetime import datetime, timezone

import pytest

from backend import conversation, storage
from backend.conversation import (
    GREETING,
    CoordinationError,
    TurnFailed,
    guard_interpretation,
    handle_message,
    next_action,
    resume_turn,
    start_application,
    technical_error_text,
)
from backend.llm import (
    Action,
    Interpretation,
    LLMFailure,
    PermanentLLMFailure,
    TransientLLMFailure,
    TurnContext,
)
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
from backend.storage import (
    ApplicationClosed,
    CannotClose,
    ClarificationRefused,
    InvalidExtraction,
    StaleRevision,
    StorageFailure,
    TurnConflict,
    TurnPending,
    UnknownApplication,
    UnknownTurn,
    WrongTurnPhase,
    close_application,
    init_db,
    load_application,
    load_turn,
    save_turn,
)

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
        # Slugged forms and the other realistic spellings a model may write.
        ("MX", "mexico_city", "ciudad_de_mexico", "covered"),
        ("MX", "ciudad-de-mexico", "ciudad_de_mexico", "covered"),
        ("MX", "ciudad_mexico", "ciudad_de_mexico", "covered"),
        ("MX", "cd_mexico", "ciudad_de_mexico", "covered"),
        ("MX", "CD MEXICO", "ciudad_de_mexico", "covered"),
        ("MX", "México DF", "ciudad_de_mexico", "covered"),
        ("MX", "distrito_federal", "ciudad_de_mexico", "covered"),
        ("ES", "madrid_capital", "madrid", "covered"),
        ("ES", "Madrid capital", "madrid", "covered"),
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


@pytest.mark.parametrize(
    "country, locality, kept_as",
    [
        ("XX", "madrid", "madrid (XX)"),
        ("XX", "Madrid", "Madrid (XX)"),
        ("VE", "valencia", "valencia (VE)"),
        ("US", "mexico_city", "mexico_city (US)"),
    ],
)
def test_catalog_place_name_in_a_non_catalog_country_is_a_doubt_not_a_rejection(
    country, locality, kept_as
):
    value = guard_interpretation(
        interpretation(resolved("work_locations", places((country, locality))))
    ).proposals[0].value
    assert value == places(unresolved=[kept_as])
    snapshot = snapshot_with([resolved("work_locations", value)])
    assert evaluate_coverage(snapshot.fields.work_locations) == "unresolved"


def test_a_doubtful_country_keeps_other_options_and_stops_a_rejection():
    value = guard_interpretation(
        interpretation(
            resolved(
                "work_locations",
                places(("ES", "bilbao"), ("XX", "madrid"), unresolved=["por el centro"]),
            )
        )
    ).proposals[0].value
    assert value == places(("ES", "bilbao"), unresolved=["por el centro", "madrid (XX)"])
    snapshot = snapshot_with([resolved("work_locations", value)])
    assert evaluate_coverage(snapshot.fields.work_locations) == "unresolved"


@pytest.mark.parametrize("place", [("FR", "paris"), ("ES", "guadalajara"), ("MX", "valencia")])
def test_clean_places_that_match_no_catalog_rule_stay_uncovered(place):
    value = guard_interpretation(
        interpretation(resolved("work_locations", places(place)))
    ).proposals[0].value
    assert value == places(place)


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
        self.last_text = text
        return handle_message(
            self.db, self.app_id, f"request-{self.sent}", text, self.llm, now=clock
        )

    def resume(self):
        return resume_turn(self.db, self.app_id, f"request-{self.sent}", self.llm, now=clock)

    def repeat(self, text=None):
        text = self.last_text if text is None else text
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


SECRET = "secret internal detail"
DOUBT_AND_QUESTION = interpretation(unresolved("driving_license"), questions=["¿Cuánto pagan?"])
NO_LICENSE_AND_QUESTION = interpretation(NO_LICENSE, questions=["¿Cuánto pagan?"])


def assert_failed(raises, phase, code):
    failure = raises.value
    assert (failure.phase, failure.code) == (phase, code)
    assert vars(failure) == {"phase": phase, "code": code}
    assert SECRET not in str(failure) and str(failure) == code


def history_counts(stored):
    roles = [message.role for message in stored.messages]
    return roles.count("candidate"), roles.count("assistant")


@pytest.fixture(params=["repeat", "resume"])
def retry(request, chat):
    return getattr(chat, request.param)


@pytest.mark.parametrize(
    "script, phase",
    [
        ("interpretations", "received"),
        ("replies", "interpreted"),
    ],
)
def test_llm_failure_on_a_normal_turn_then_retry_duplicates_nothing(
    chat, retry, db, app_id, script, phase
):
    chat.send("Ana Ruiz", interpretation(NAME))
    getattr(chat.llm, script).append(TransientLLMFailure(SECRET))
    chat.llm.interpretations.append(DOUBT_AND_QUESTION)

    with pytest.raises(TurnFailed) as raises:
        chat.send("mensaje")
    assert_failed(raises, phase, "llm_transient")

    stored = load_application(db, app_id)
    assert history_counts(stored) == (2, 2)
    assert stored.messages[-1].text == "mensaje"
    assert stored.snapshot.fields.full_name.status == "resolved"
    assert stored.snapshot.fields.driving_license.clarifications_asked == 0
    assert stored.snapshot.pending_questions == (
        [] if phase == "received" else ["¿Cuánto pagan?"]
    )

    stored = retry()
    assert history_counts(stored) == (2, 3)
    assert stored.snapshot.pending_questions == ["¿Cuánto pagan?"]
    assert stored.snapshot.fields.driving_license.clarifications_asked == 1
    assert load_turn(db, app_id, "request-2").phase == "completed"


@pytest.mark.parametrize("script", ["replies", "summaries"])
def test_llm_failure_while_closing_then_retry_closes_once(chat, retry, db, app_id, script):
    getattr(chat.llm, script).append(PermanentLLMFailure(SECRET))
    with pytest.raises(TurnFailed) as raises:
        chat.send("No tengo carné", NO_LICENSE_AND_QUESTION)
    assert_failed(raises, "interpreted", "llm_permanent")

    stored = load_application(db, app_id)
    assert history_counts(stored) == (1, 1)
    assert stored.snapshot.fields.driving_license.normalized is False
    assert stored.snapshot.closed_at is None
    assert stored.snapshot.outcome == "incomplete"
    assert stored.snapshot.summary is None

    stored = retry()
    assert history_counts(stored) == (1, 2)
    assert stored.snapshot.outcome == "disqualified"
    assert stored.snapshot.summary == "INTERNAL SUMMARY"
    assert stored.snapshot.pending_questions == ["¿Cuánto pagan?"]
    assert len(chat.llm.contexts("interpret")) == 1


@pytest.mark.parametrize(
    "failure, code",
    [
        (TransientLLMFailure(SECRET), "llm_transient"),
        (PermanentLLMFailure(SECRET), "llm_permanent"),
        (LLMFailure(SECRET), "llm_permanent"),
    ],
)
def test_llm_failures_map_to_stable_codes_and_are_never_retried_automatically(
    chat, failure, code
):
    chat.llm.interpretations.append(failure)
    with pytest.raises(TurnFailed) as raises:
        chat.send()
    assert_failed(raises, "received", code)
    assert len(chat.llm.calls) == 1


def test_retry_from_interpreted_sees_the_same_context_as_the_first_pass(chat):
    chat.llm.replies.append(TransientLLMFailure(SECRET))
    with pytest.raises(TurnFailed):
        chat.send("mensaje", DOUBT_AND_QUESTION)
    chat.resume()
    first, again = chat.llm.calls[1], chat.llm.calls[2]
    assert first == again


@pytest.mark.parametrize(
    "failing, phase",
    [
        ("receive_turn", None),
        ("load_turn", None),
        ("commit_interpretation", "received"),
        ("complete_turn", "interpreted"),
    ],
)
def test_storage_failure_is_retryable_and_hides_details(chat, monkeypatch, failing, phase):
    def fail(*args, **kwargs):
        raise StorageFailure(SECRET)

    chat.llm.interpretations.extend([interpretation(NAME), interpretation(NAME)])
    with monkeypatch.context() as patch:
        patch.setattr(conversation, failing, fail)
        with pytest.raises(TurnFailed) as raises:
            chat.send()
    assert_failed(raises, phase, "storage_failure")

    stored = chat.repeat()
    assert history_counts(stored) == (1, 2)
    assert stored.snapshot.fields.full_name.status == "resolved"


def test_storage_failure_while_closing_generates_again_and_closes_once(
    chat, db, app_id, monkeypatch
):
    def fail(*args, **kwargs):
        raise StorageFailure(SECRET)

    with monkeypatch.context() as patch:
        patch.setattr(conversation, "close_application", fail)
        with pytest.raises(TurnFailed) as raises:
            chat.send("No tengo carné", interpretation(NO_LICENSE))
    assert_failed(raises, "interpreted", "storage_failure")
    assert load_application(db, app_id).snapshot.summary is None

    stored = chat.repeat()
    assert history_counts(stored) == (1, 2)
    assert stored.snapshot.closed_at is not None


@pytest.mark.parametrize("message_text", ["No tengo carné", "Ana Ruiz"])
def test_lost_response_is_recovered_without_appending_or_calling_the_llm(
    chat, db, app_id, message_text
):
    script = NO_LICENSE if message_text == "No tengo carné" else NAME
    first = chat.send(message_text, interpretation(script))
    calls = len(chat.llm.calls)

    assert chat.repeat() == first
    assert chat.resume() == first
    assert load_application(db, app_id) == first
    assert len(chat.llm.calls) == calls


def test_a_completed_turn_returns_current_history_after_later_turns(chat):
    chat.send("Ana Ruiz", interpretation(NAME))
    latest = chat.send("Sí", interpretation(LICENSE))
    assert handle_message(chat.db, chat.app_id, "request-1", "Ana Ruiz", chat.llm) == latest


def test_malformed_extraction_preserves_previous_facts_and_commits_nothing(chat, db, app_id):
    chat.send("Soy Ana Ruiz y tengo carné", interpretation(NAME, LICENSE))
    malformed = interpretation(
        resolved("full_name", "Ana Gil"),
        resolved("driving_license", "yes"),
        questions=["¿Cuánto pagan?"],
        language="en",
        stop_intent="stop",
    )
    before = load_application(db, app_id).snapshot

    with pytest.raises(TurnFailed) as raises:
        chat.send("mensaje", malformed)
    assert_failed(raises, "received", "invalid_interpretation")

    stored = load_application(db, app_id)
    assert stored.messages[-1].text == "mensaje"
    assert stored.snapshot.fields == before.fields
    assert stored.snapshot.pending_questions == []
    assert stored.snapshot.language == "es"
    assert stored.snapshot.stop_requested is False
    assert load_turn(db, app_id, "request-2").phase == "received"

    chat.llm.interpretations.append(interpretation(resolved("full_name", "Ana Gil")))
    assert chat.resume().snapshot.fields.full_name.normalized == "Ana Gil"


def test_genuine_unresolved_correction_clears_the_previous_fact(chat, db, app_id):
    chat.send("Soy Ana Ruiz y tengo carné", interpretation(NAME, LICENSE))
    stored = chat.send(
        "Bueno, en realidad no sé si sigue vigente",
        interpretation(unresolved("driving_license", reason="license_retracted")),
    )
    license_state = stored.snapshot.fields.driving_license
    assert license_state.status == "unresolved"
    assert license_state.normalized is None
    assert license_state.clarifications_asked == 1
    assert chat.llm.actions[-1] == CLARIFY("driving_license", 1)


@pytest.mark.parametrize(
    "bad_output",
    [
        None,
        "texto",
        {"proposals": []},
        interpretation(NAME, resolved("full_name", "Ana Gil")),
        interpretation(questions=["   "]),
        interpretation(questions=["pregunta \ud800"]),
        interpretation(resolved("full_name", "Ana \ud800")),
    ],
    ids=["none", "text", "dict", "duplicate", "blank question", "surrogate question", "surrogate"],
)
def test_unusable_interpretations_are_technical_failures(chat, db, app_id, bad_output):
    with pytest.raises(TurnFailed) as raises:
        chat.send("mensaje", bad_output)
    assert_failed(raises, "received", "invalid_interpretation")

    stored = load_application(db, app_id)
    assert stored.snapshot.fields.full_name.status == "missing"
    assert stored.snapshot.pending_questions == []
    assert history_counts(stored) == (1, 1)


@pytest.mark.parametrize(
    "place", [("ES", "Bilbao"), ("España", "madrid"), ("ES", "Guadalajara"), ("Spain", "Madrid")]
)
def test_malformed_catalog_identifier_never_disqualifies(chat, db, app_id, place):
    chat.send("Ana Ruiz, con carné", interpretation(NAME, LICENSE))
    with pytest.raises(TurnFailed) as raises:
        chat.send("mensaje", interpretation(resolved("work_locations", places(place))))
    assert_failed(raises, "received", "invalid_interpretation")

    stored = load_application(db, app_id)
    assert stored.snapshot.closed_at is None
    assert stored.snapshot.outcome == "incomplete"
    assert stored.snapshot.fields.work_locations.status == "missing"

    chat.llm.interpretations.append(interpretation(MADRID))
    assert chat.resume().snapshot.closed_at is None


def test_catalog_alias_covers_and_does_not_disqualify(chat):
    chat.send("Ana Ruiz, con carné", interpretation(NAME, LICENSE))
    stored = chat.send(
        "Madrid", interpretation(resolved("work_locations", places(("ES", "Madrid"))))
    )
    assert stored.snapshot.closed_at is None
    assert stored.snapshot.fields.work_locations.normalized.identified[0].locality == "madrid"
    assert chat.llm.actions[-1] == ASK("availability")


def test_well_formed_uncovered_place_is_a_real_rejection(chat):
    chat.send("Ana Ruiz, con carné", interpretation(NAME, LICENSE))
    stored = chat.send("Bilbao", interpretation(BILBAO))
    assert stored.snapshot.outcome == "disqualified"
    assert stored.snapshot.outcome_reasons == ["location_not_covered"]


BAD_TEXTS = [None, 42, b"bytes", ["lista"], "", "   \n"]


@pytest.mark.parametrize("bad_text", BAD_TEXTS)
def test_bad_reply_text_saves_nothing_and_keeps_the_budget(
    chat, db, app_id, monkeypatch, bad_text
):
    def must_not_reach_storage(*args, **kwargs):
        raise AssertionError("checked before storage")

    chat.llm.replies.append(bad_text)
    with monkeypatch.context() as patch:
        patch.setattr(conversation, "complete_turn", must_not_reach_storage)
        with pytest.raises(TurnFailed) as raises:
            chat.send("mensaje", DOUBT_AND_QUESTION)
    assert_failed(raises, "interpreted", "invalid_reply")

    stored = load_application(db, app_id)
    assert history_counts(stored) == (1, 1)
    assert stored.snapshot.fields.driving_license.clarifications_asked == 0

    assert history_counts(chat.resume()) == (1, 2)


@pytest.mark.parametrize("script", ["replies", "summaries"])
@pytest.mark.parametrize("bad_text", BAD_TEXTS)
def test_bad_closing_text_does_not_close(chat, db, app_id, script, bad_text):
    getattr(chat.llm, script).append(bad_text)
    with pytest.raises(TurnFailed) as raises:
        chat.send("No tengo carné", interpretation(NO_LICENSE))
    assert_failed(raises, "interpreted", "invalid_reply")
    assert load_application(db, app_id).snapshot.closed_at is None

    assert chat.resume().snapshot.outcome == "disqualified"


def test_surrogate_in_a_request_id_still_completes_the_turn(db, app_id):
    stored = handle_message(db, app_id, "id\ud800", "hola", FakeLLM(), now=clock)
    assert history_counts(stored) == (1, 2)
    assert load_turn(db, app_id, "id\ud800").phase == "completed"


def test_surrogates_in_generated_text_are_saved_through_storage(chat, db, app_id):
    chat.llm.replies.append("hola \ud800")
    stored = chat.send("Ana \ud800", interpretation(NAME))
    assert stored.messages[-2].text == "Ana ?"
    assert stored.messages[-1].text == "hola ?"
    stored.model_dump_json()

    chat.llm.replies.append("adiós \ud800")
    chat.llm.summaries.append("resumen \ud800")
    stored = chat.send("No", interpretation(NO_LICENSE))
    assert stored.messages[-1].text == "adiós ?"
    assert stored.snapshot.summary == "resumen ?"
    assert load_application(db, app_id) == stored
    stored.model_dump_json()


def once(action):
    """A before_call hook that runs `action` the first time `name` is called."""
    done = set()

    def hook(name, target):
        if name == target and target not in done:
            done.add(target)
            action()

    return hook


def another_writer(db, app_id, **changes):
    """Changes the application as a concurrent writer would, moving its revision."""
    revision = load_application(db, app_id).snapshot.revision
    save_turn(db, app_id, revision, **changes)


def test_refused_clarification_is_rederived_and_its_text_discarded(chat, monkeypatch):
    real = conversation.complete_turn
    refused = []

    def refuse_once(*args, **kwargs):
        if not refused:
            refused.append(True)
            raise ClarificationRefused("driving_license", "clarification_limit_reached")
        return real(*args, **kwargs)

    monkeypatch.setattr(conversation, "complete_turn", refuse_once)
    chat.llm.replies.append("texto descartado")
    stored = chat.send("mensaje", interpretation(NAME, unresolved("driving_license")))

    assert chat.llm.actions == [CLARIFY("driving_license", 1)] * 2
    assert stored.messages[-1].text == "reply 2"
    assert history_counts(stored) == (1, 2)
    assert stored.snapshot.fields.driving_license.clarifications_asked == 1


@pytest.mark.parametrize(
    "patched, refusal",
    [
        ("complete_turn", ClarificationRefused("full_name", "clarification_limit_reached")),
        ("complete_turn", WrongTurnPhase("interpreted")),
        ("complete_turn", StaleRevision("application")),
        ("close_application", CannotClose("not_closeable")),
    ],
    ids=["refused", "wrong phase", "stale", "cannot close"],
)
def test_state_that_keeps_changing_ends_in_a_retryable_failure(
    chat, db, app_id, monkeypatch, patched, refusal
):
    def refuse(*args, **kwargs):
        raise refusal

    monkeypatch.setattr(conversation, patched, refuse)
    script = interpretation(NO_LICENSE) if patched == "close_application" else interpretation(NAME)
    with pytest.raises(TurnFailed) as raises:
        chat.send("mensaje", script)
    assert_failed(raises, "interpreted", "state_changed")
    assert 1 < len(chat.llm.actions) <= 4
    assert history_counts(load_application(db, app_id)) == (1, 1)


def test_close_refused_after_a_state_change_rederives_the_action(chat, db, app_id, monkeypatch):
    real = conversation.close_application

    def refuse_after_change(*args, **kwargs):
        monkeypatch.setattr(conversation, "close_application", real)
        raise CannotClose("stop_requested")

    monkeypatch.setattr(conversation, "close_application", refuse_after_change)
    stored = chat.send("No tengo carné", interpretation(NO_LICENSE))
    assert history_counts(stored) == (1, 2)
    assert stored.snapshot.closed_at is not None


def test_stale_revision_discards_the_generated_reply(chat, db, app_id):
    chat.llm.replies.append("texto descartado")
    hook = once(lambda: another_writer(db, app_id, proposals=[LICENSE]))
    chat.llm.before_call = lambda name: hook(name, "write_reply")

    stored = chat.send("Ana Ruiz", interpretation(NAME))

    assert "texto descartado" not in [message.text for message in stored.messages]
    assert chat.llm.actions == [ASK("driving_license"), ASK("work_locations")]
    assert stored.messages[-1].text == "reply 2"
    assert stored.snapshot.last_question_field == "work_locations"
    assert history_counts(stored) == (1, 2)


def test_stale_revision_during_interpretation_interprets_again(chat, db, app_id):
    hook = once(lambda: another_writer(db, app_id, proposals=[LICENSE]))
    chat.llm.before_call = lambda name: hook(name, "interpret")
    chat.llm.interpretations.extend(
        [interpretation(questions=["descartada"]), interpretation(NAME, questions=["¿Cuánto?"])]
    )

    stored = chat.send("Ana Ruiz")

    assert stored.snapshot.pending_questions == ["¿Cuánto?"]
    assert stored.snapshot.fields.driving_license.normalized is True
    assert len(chat.llm.contexts("interpret")) == 2
    assert chat.llm.contexts("interpret")[1].fields.driving_license.normalized is True


def test_state_change_while_closing_discards_the_closing_texts(chat, db, app_id):
    hook = once(lambda: another_writer(db, app_id, stop_requested=True))
    chat.llm.before_call = lambda name: hook(name, "write_summary")

    stored = chat.send("No tengo carné", interpretation(NO_LICENSE))

    assert chat.llm.actions == [CLOSE, STOP]
    assert stored.snapshot.closed_at is None
    assert stored.snapshot.summary is None
    assert stored.messages[-1].text == "reply 2"
    assert history_counts(stored) == (1, 2)


@pytest.mark.parametrize(
    "call, losing_text",
    [("interpret", None), ("write_reply", "reply 1"), ("write_summary", "reply 1")],
)
def test_concurrent_retry_wins_and_the_slower_one_returns_its_committed_work(
    db, app_id, call, losing_text
):
    script = interpretation(NO_LICENSE, questions=["¿Cuánto pagan?"])
    llm = FakeLLM(interpretations=[script], summaries=["resumen perdedor"])
    winner = FakeLLM(interpretations=[script], replies=["texto ganador"], summaries=["resumen"])
    hook = once(lambda: resume_turn(db, app_id, "request", winner, now=clock))
    llm.before_call = lambda name: hook(name, call)

    stored = handle_message(db, app_id, "request", "No tengo carné", llm, now=clock)

    assert stored.messages[-1].text == "texto ganador"
    assert stored.snapshot.summary == "resumen"
    assert stored.snapshot.pending_questions == ["¿Cuánto pagan?"]
    assert history_counts(stored) == (1, 2)
    assert load_application(db, app_id) == stored


def test_a_turn_completed_between_the_two_reads_returns_its_committed_history(
    db, app_id, monkeypatch
):
    real = conversation.load_application
    with pytest.raises(TurnFailed):
        handle_message(
            db, app_id, "request", "hola", FakeLLM([TransientLLMFailure(SECRET)]), now=clock
        )

    def read_then_another_request_finishes(*args):
        stored = real(*args)
        monkeypatch.setattr(conversation, "load_application", real)
        resume_turn(db, app_id, "request", FakeLLM(replies=["texto ganador"]), now=clock)
        return stored

    monkeypatch.setattr(conversation, "load_application", read_then_another_request_finishes)
    stored = resume_turn(db, app_id, "request", FakeLLM(), now=clock)

    assert stored.messages[-1].text == "texto ganador"
    assert load_application(db, app_id) == stored


def test_turn_pending_names_the_turn_to_resume(chat, db, app_id):
    chat.llm.interpretations.append(TransientLLMFailure(SECRET))
    with pytest.raises(TurnFailed):
        chat.send("primero")
    calls = len(chat.llm.calls)

    with pytest.raises(TurnPending) as raises:
        handle_message(db, app_id, "another", "segundo", chat.llm, now=clock)
    assert raises.value.request_id == "request-1"
    assert len(chat.llm.calls) == calls
    assert history_counts(load_application(db, app_id)) == (1, 1)

    resume_turn(db, app_id, raises.value.request_id, chat.llm, now=clock)
    stored = handle_message(db, app_id, "another", "segundo", chat.llm, now=clock)
    assert history_counts(stored) == (2, 3)


@pytest.mark.parametrize("finished", [True, False])
def test_turn_conflict_reuses_no_request_id(chat, db, app_id, finished):
    if not finished:
        chat.llm.interpretations.append(TransientLLMFailure(SECRET))
    try:
        chat.send("original")
    except TurnFailed:
        pass
    calls = len(chat.llm.calls)
    before = load_application(db, app_id)

    with pytest.raises(TurnConflict):
        chat.repeat("distinto")
    assert len(chat.llm.calls) == calls
    assert load_application(db, app_id) == before


def test_repeated_close_never_closes_twice(chat, db, app_id):
    closed = chat.send("No tengo carné", interpretation(NO_LICENSE))
    calls = len(chat.llm.calls)

    assert chat.repeat() == closed
    with pytest.raises(ApplicationClosed):
        chat.send("Sí que lo tengo", interpretation(LICENSE))
    assert load_application(db, app_id) == closed
    assert len(chat.llm.calls) == calls
    with pytest.raises(WrongTurnPhase):
        close_application(db, app_id, closed.snapshot.revision, "otro", "otro", request_id="request-1")


def test_unknown_ids_propagate(db, app_id):
    llm = FakeLLM()
    with pytest.raises(UnknownApplication):
        handle_message(db, "nobody", "request", "hola", llm, now=clock)
    with pytest.raises(UnknownApplication):
        resume_turn(db, "nobody", "request", llm, now=clock)
    with pytest.raises(UnknownTurn):
        resume_turn(db, app_id, "nothing", llm, now=clock)
    assert llm.calls == []


def test_coordination_error_is_a_retryable_failure_that_invents_nothing(
    chat, db, app_id, monkeypatch
):
    not_closeable = Evaluation(outcome="incomplete", outcome_reasons=[], closeable=False)
    monkeypatch.setattr(conversation, "evaluate", lambda fields: not_closeable)
    with pytest.raises(TurnFailed) as raises:
        chat.send("todo", interpretation(*ALL_RESOLVED))
    assert_failed(raises, "interpreted", "coordination_error")
    assert load_application(db, app_id).snapshot.closed_at is None
    assert chat.llm.actions == []


def test_summary_stays_out_of_every_context_even_after_a_failed_close(chat, monkeypatch):
    def fail(*args, **kwargs):
        raise StorageFailure(SECRET)

    with monkeypatch.context() as patch:
        patch.setattr(conversation, "close_application", fail)
        with pytest.raises(TurnFailed):
            chat.send("No tengo carné", interpretation(NO_LICENSE))
    stored = chat.resume()

    assert stored.snapshot.summary == "INTERNAL SUMMARY"
    assert "summary" not in TurnContext.model_fields
    replies = chat.llm.contexts("write_reply")
    assert len(replies) == 2
    for _, context, _ in chat.llm.calls:
        assert "INTERNAL SUMMARY" not in context.model_dump_json()


def probe_write_transaction(db):
    connection = sqlite3.connect(db, timeout=0.05, isolation_level=None)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("ROLLBACK")
    finally:
        connection.close()


def test_the_probe_detects_an_open_transaction(db):
    with storage._transaction(db):
        with pytest.raises(sqlite3.OperationalError):
            probe_write_transaction(db)


def test_no_llm_call_happens_inside_a_transaction(chat, db):
    chat.llm.before_call = lambda name: probe_write_transaction(db)

    chat.send("Ana Ruiz", interpretation(NAME))
    stored = chat.send("No tengo carné", interpretation(NO_LICENSE))

    assert stored.snapshot.closed_at is not None
    assert {name for name, _, _ in chat.llm.calls} == {"interpret", "write_reply", "write_summary"}


def test_the_default_clock_gives_an_aware_datetime(db, app_id):
    llm = FakeLLM()
    handle_message(db, app_id, "request", "hola", llm)
    assert llm.contexts("interpret")[0].now.tzinfo is not None
