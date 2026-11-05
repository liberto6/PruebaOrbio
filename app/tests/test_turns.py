import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

import pytest

from backend import storage
from backend.rules import ApplyResult, CandidateSnapshot
from backend.storage import (
    ApplicationClosed,
    CannotClose,
    ClarificationRefused,
    InvalidExtraction,
    InvalidReply,
    StaleRevision,
    StorageFailure,
    TurnConflict,
    TurnPending,
    UnknownApplication,
    UnknownTurn,
    WrongTurnPhase,
    close_application,
    commit_interpretation,
    complete_turn,
    create_application,
    init_db,
    load_application,
    load_application_with_turn,
    load_turn,
    load_unfinished_turn,
    receive_turn,
    reminder_due_at,
    save_turn,
    send_reminder,
    set_recruiter_decision,
)

E2_SCHEMA = """
CREATE TABLE applications (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    closed_at TEXT,
    outcome TEXT NOT NULL,
    recruiter_decision TEXT NOT NULL,
    snapshot TEXT NOT NULL
);
CREATE TABLE messages (
    application_id TEXT NOT NULL REFERENCES applications (id),
    sequence INTEGER NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('candidate', 'assistant')),
    text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (application_id, sequence)
);
"""
NEW_SNAPSHOT_KEYS = ("language", "last_question_field", "last_question_kind", "reminder_sent")


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "test.db"
    init_db(path)
    return path


def execute(db, sql, *parameters):
    connection = sqlite3.connect(db)
    try:
        with connection:
            return connection.execute(sql, parameters).fetchall()
    finally:
        connection.close()


def e2_snapshot_json(db_with_current_schema):
    """Snapshot JSON exactly as E2 wrote it: without any of the E3 keys."""
    created = create_application(db_with_current_schema)
    data = json.loads(created.model_dump_json())
    for key in NEW_SNAPSHOT_KEYS:
        del data[key]
    return created.id, json.dumps(data)


@pytest.fixture
def e2_db(tmp_path, db):
    """A database file with the E2 schema and one stored E2 application."""
    application_id, snapshot = e2_snapshot_json(db)
    path = tmp_path / "e2.db"
    connection = sqlite3.connect(path)
    connection.executescript(E2_SCHEMA)
    with connection:
        connection.execute(
            "INSERT INTO applications VALUES (?, 'c', 'u', NULL, 'incomplete', 'unreviewed', ?)",
            (application_id, snapshot),
        )
        connection.execute(
            "INSERT INTO messages VALUES (?, 1, 'candidate', 'hola', 'now')", (application_id,)
        )
    connection.close()
    return path, application_id


def prop(field, value, raw="candidate text", status="resolved", reason=None):
    return {"field": field, "status": status, "value": value, "raw": raw, "reason": reason}


def unresolved(field):
    return prop(field, None, raw="no sé", status="unresolved", reason="ambiguous")


LICENSE_TRUE = prop("driving_license", True)
LICENSE_FALSE = prop("driving_license", False)
ALL_RESOLVED = [
    prop("full_name", "Ana García"),
    LICENSE_TRUE,
    prop(
        "work_locations",
        {"identified": [{"country": "ES", "locality": "madrid"}], "unresolved": []},
    ),
    prop("availability", {"options": ["full_time"], "notes": None}),
    prop("preferred_schedule", {"options": ["morning"], "notes": None}),
    prop("delivery_experience", {"amount": 18, "unit": "months", "platforms": ["Glovo"]}),
    prop("start_timing", {"kind": "unknown", "value": None}),
]


def test_new_snapshot_has_safe_metadata_defaults(db):
    snapshot = create_application(db)

    assert snapshot.language == "es"
    assert snapshot.last_question_field is None
    assert snapshot.last_question_kind is None
    assert snapshot.reminder_sent is False


@pytest.mark.parametrize(
    "bad",
    [
        {"language": "fr"},
        {"last_question_field": "salary"},
        {"last_question_kind": "reminder"},
        {"reminder_sent": 1},
    ],
)
def test_snapshot_metadata_is_validated_when_loading(db, bad):
    data = json.loads(create_application(db).model_dump_json()) | bad

    with pytest.raises(ValueError):
        CandidateSnapshot.model_validate_json(json.dumps(data))


def test_e2_database_and_json_without_new_keys_load_and_keep_working(e2_db):
    path, application_id = e2_db

    init_db(path)
    stored = load_application(path, application_id)

    assert stored.snapshot.language == "es"
    assert stored.snapshot.reminder_sent is False
    assert [message.text for message in stored.messages] == ["hola"]
    assert save_turn(path, application_id, 0, candidate_message="sigo").snapshot.revision == 1
    assert json.loads(execute(path, "SELECT snapshot FROM applications")[0][0])["language"] == "es"


def everything(db, application_id):
    """All durable state of an application, to prove a failure changed nothing."""
    return (
        load_application(db, application_id),
        execute(db, "SELECT * FROM turns WHERE application_id = ?", application_id),
    )


def fail_on(db, operation, table):
    """A real failure inside the transaction, at one specific write."""
    execute(
        db,
        f"CREATE TRIGGER forced_failure BEFORE {operation} ON {table}"
        " BEGIN SELECT RAISE(ABORT, 'forced failure'); END",
    )


def stop_failing(db):
    execute(db, "DROP TRIGGER forced_failure")


def message_texts(db, application_id):
    return [message.text for message in load_application(db, application_id).messages]


def test_init_db_gives_an_e2_database_the_turns_table_without_touching_data(e2_db):
    path, application_id = e2_db

    init_db(path)
    init_db(path)

    received = receive_turn(path, application_id, "r1", "Soy Ana")
    assert received.created is True
    assert received.turn.candidate_sequence == 2
    assert message_texts(path, application_id) == ["hola", "Soy Ana"]


def test_create_without_greeting_stays_empty_at_revision_zero(db):
    created = create_application(db)

    stored = load_application(db, created.id)
    assert stored.messages == []
    assert stored.snapshot == created
    assert created.revision == 0
    assert created.last_question_field is None


def test_create_with_greeting_stores_it_as_first_message_with_question_context(db):
    created = create_application(db, greeting="Hola, ¿cómo te llamas? \ud83d")

    stored = load_application(db, created.id)
    assert created.revision == 1
    assert stored.snapshot == created
    assert created.last_question_field == "full_name"
    assert created.last_question_kind == "initial"
    assert created.fields.full_name.clarifications_asked == 0
    assert [(m.sequence, m.role, m.text) for m in stored.messages] == [
        (1, "assistant", "Hola, ¿cómo te llamas? ?")
    ]


@pytest.mark.parametrize("greeting", ["", "   ", 3])
def test_create_with_an_invalid_greeting_is_refused(db, greeting):
    with pytest.raises(InvalidReply):
        create_application(db, greeting=greeting)

    assert execute(db, "SELECT COUNT(*) FROM applications") == [(0,)]


def test_failed_greeting_creates_no_application(db):
    fail_on(db, "INSERT", "messages")

    with pytest.raises(StorageFailure):
        create_application(db, greeting="Hola")

    assert execute(db, "SELECT COUNT(*) FROM applications") == [(0,)]


def test_receive_saves_message_and_turn_once_and_bumps_the_revision(db):
    application = create_application(db, greeting="Hola")

    received = receive_turn(db, application.id, "r1", "Soy Ana")

    assert received.created is True
    assert received.turn.model_dump() == {
        "request_id": "r1",
        "phase": "received",
        "candidate_sequence": 2,
        "reply_sequence": None,
    }
    assert received.application == load_application(db, application.id)
    assert received.application.snapshot.revision == 2
    assert [(m.sequence, m.role, m.text) for m in received.application.messages] == [
        (1, "assistant", "Hola"),
        (2, "candidate", "Soy Ana"),
    ]
    assert load_turn(db, application.id, "r1") == received.turn
    assert load_unfinished_turn(db, application.id) == received.turn


def test_duplicate_request_with_the_same_text_returns_recorded_state_and_writes_nothing(db):
    application = create_application(db)
    first = receive_turn(db, application.id, "r1", "Soy Ana")
    before = everything(db, application.id)

    again = receive_turn(db, application.id, "r1", "Soy Ana")

    assert again.created is False
    assert again.turn == first.turn
    assert again.application == first.application
    assert everything(db, application.id) == before


def test_duplicate_request_with_different_text_is_a_conflict(db):
    application = create_application(db)
    receive_turn(db, application.id, "r1", "Soy Ana")
    before = everything(db, application.id)

    with pytest.raises(TurnConflict):
        receive_turn(db, application.id, "r1", "Soy Eva")

    assert everything(db, application.id) == before


def test_retry_with_the_same_surrogate_text_is_the_same_request(db):
    application = create_application(db)
    receive_turn(db, application.id, "r1", "hola \ud83d")
    before = everything(db, application.id)

    again = receive_turn(db, application.id, "r1", "hola \ud83d")

    assert again.created is False
    assert message_texts(db, application.id) == ["hola ?"]
    assert everything(db, application.id) == before
    with pytest.raises(TurnConflict):
        receive_turn(db, application.id, "r1", "hola \ud83e!")


def test_surrogate_in_the_request_id_is_stored_sanitized_and_still_retries(db):
    application = create_application(db)

    first = receive_turn(db, application.id, "r\ud83d", "hola")
    again = receive_turn(db, application.id, "r\ud83d", "hola")

    assert first.turn.request_id == "r?"
    assert again.created is False
    assert load_turn(db, application.id, "r\ud83d") == first.turn


def test_new_request_while_another_is_unfinished_names_the_pending_one(db):
    application = create_application(db)
    receive_turn(db, application.id, "r1", "Soy Ana")
    before = everything(db, application.id)

    with pytest.raises(TurnPending) as refused:
        receive_turn(db, application.id, "r2", "Otra cosa")

    assert refused.value.request_id == "r1"
    assert everything(db, application.id) == before


def test_second_unfinished_turn_is_refused_by_the_database_itself(db):
    application = create_application(db)
    receive_turn(db, application.id, "r1", "Soy Ana")

    with pytest.raises(sqlite3.IntegrityError):
        execute(
            db,
            "INSERT INTO turns (application_id, request_id, candidate_sequence, phase,"
            " created_at, updated_at) VALUES (?, 'r2', 9, 'interpreted', 'now', 'now')",
            application.id,
        )


def test_database_index_still_refuses_when_the_python_check_is_bypassed(db, monkeypatch):
    application = create_application(db)
    receive_turn(db, application.id, "r1", "Soy Ana")
    before = everything(db, application.id)
    monkeypatch.setattr("backend.storage._unfinished_turn", lambda *_: None)

    with pytest.raises(StorageFailure):
        receive_turn(db, application.id, "r2", "Otra cosa")

    monkeypatch.undo()
    assert everything(db, application.id) == before


def test_turn_phase_is_constrained_by_the_database(db):
    application = create_application(db)

    with pytest.raises(sqlite3.IntegrityError):
        execute(
            db,
            "INSERT INTO turns (application_id, request_id, candidate_sequence, phase,"
            " created_at, updated_at) VALUES (?, 'r1', 1, 'closed', 'now', 'now')",
            application.id,
        )


def test_turn_of_an_unknown_application_is_refused_by_the_foreign_key(db):
    connection = sqlite3.connect(db)
    connection.execute("PRAGMA foreign_keys = ON")
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO turns (application_id, request_id, candidate_sequence, phase,"
            " created_at, updated_at) VALUES ('nope', 'r1', 1, 'received', 'now', 'now')"
        )
    connection.close()


def test_unknown_application_and_turn_are_refused_on_reads_and_receive(db):
    application = create_application(db)

    with pytest.raises(UnknownApplication):
        receive_turn(db, "nope", "r1", "hola")
    with pytest.raises(UnknownApplication):
        load_turn(db, "nope", "r1")
    with pytest.raises(UnknownApplication):
        load_unfinished_turn(db, "nope")
    with pytest.raises(UnknownTurn):
        load_turn(db, application.id, "nope")
    assert load_unfinished_turn(db, application.id) is None


@pytest.mark.parametrize("arguments", [("r1", 3), (3, "hola"), (None, "hola"), ("r1", None)])
def test_receive_refuses_non_string_arguments_without_writing(db, arguments):
    application = create_application(db)

    with pytest.raises(TypeError):
        receive_turn(db, application.id, *arguments)

    assert load_application(db, application.id).snapshot == application


def test_stopped_application_still_accepts_messages(db):
    application = create_application(db)
    save_turn(db, application.id, 0, stop_requested=True)

    received = receive_turn(db, application.id, "r1", "Sigo aquí")

    assert received.created is True
    assert received.application.snapshot.stop_requested is True


def test_new_request_on_a_closed_application_is_refused(db):
    application = create_application(db)
    save_turn(db, application.id, 0, proposals=[LICENSE_FALSE])
    close_application(db, application.id, 1, "Gracias.", "Sin carnet")
    before = everything(db, application.id)

    with pytest.raises(ApplicationClosed):
        receive_turn(db, application.id, "r1", "¿Sigues ahí?")

    assert everything(db, application.id) == before


@pytest.mark.parametrize(
    ("operation", "table"),
    [("INSERT", "messages"), ("INSERT", "turns"), ("UPDATE", "applications")],
)
def test_failed_receive_rolls_everything_back_and_can_be_retried(db, operation, table):
    application = create_application(db)
    before = everything(db, application.id)
    fail_on(db, operation, table)

    with pytest.raises(StorageFailure):
        receive_turn(db, application.id, "r1", "Soy Ana")

    assert everything(db, application.id) == before
    stop_failing(db)
    assert receive_turn(db, application.id, "r1", "Soy Ana").created is True


def test_recruiter_decision_does_not_bump_the_revision_during_a_turn(db):
    application = create_application(db)
    received = receive_turn(db, application.id, "r1", "Soy Ana")

    updated = set_recruiter_decision(db, application.id, "advance")

    assert updated.revision == received.application.snapshot.revision


def received(db, greeting=None):
    """An application with one turn in phase received, and its current revision."""
    application = create_application(db, greeting=greeting)
    turn = receive_turn(db, application.id, "r1", "Soy Ana")
    return application.id, turn.application.snapshot.revision


def interpreted(db, proposals=(), **changes):
    """An application with one turn in phase interpreted, and its current revision."""
    application_id, revision = received(db)
    snapshot = commit_interpretation(
        db, application_id, "r1", revision, proposals=proposals, **changes
    )
    return application_id, snapshot.revision


def test_commit_interpretation_applies_everything_and_advances_the_turn(db):
    application_id, revision = received(db)

    snapshot = commit_interpretation(
        db,
        application_id,
        "r1",
        revision,
        proposals=[prop("full_name", "Ana García"), unresolved("start_timing")],
        pending_questions=["¿Cuánto se paga?"],
        language="en",
        stop_requested=True,
    )

    stored = load_application(db, application_id)
    assert stored.snapshot == snapshot
    assert snapshot.revision == revision + 1
    assert snapshot.fields.full_name.normalized == "Ana García"
    assert snapshot.fields.start_timing.status == "unresolved"
    assert snapshot.pending_questions == ["¿Cuánto se paga?"]
    assert snapshot.language == "en"
    assert snapshot.stop_requested is True
    assert snapshot.outcome == "incomplete"
    assert load_turn(db, application_id, "r1").phase == "interpreted"
    assert message_texts(db, application_id) == ["Soy Ana"]


def test_commit_interpretation_with_none_keeps_language_and_stop(db):
    application_id, revision = interpreted(db, language="en", stop_requested=True)
    revision = complete_turn(db, application_id, "r1", revision, reply="De acuerdo.").revision
    receive_turn(db, application_id, "r2", "Sigo")

    snapshot = commit_interpretation(db, application_id, "r2", revision + 1, proposals=[])

    assert snapshot.language == "en"
    assert snapshot.stop_requested is True


@pytest.mark.parametrize(
    ("extraction", "field"),
    [
        ({"proposals": [prop("full_name", "Ana"), prop("driving_license", "yes")]}, "driving_license"),
        ({"proposals": [None]}, None),
        ({"proposals": "abc"}, None),
        ({"proposals": 3}, None),
        ({"proposals": {}}, None),
        ({"proposals": [prop("full_name", "Ana", raw="hola \ud83d")]}, "full_name"),
        ({"proposals": [prop("full_name", "hola \ud83d")]}, "full_name"),
        ({"proposals": [prop("salary\ud83d", "x")]}, "salary?"),
        ({"pending_questions": ["¿Cuánto?", ""]}, "pending_questions"),
        ({"pending_questions": ["hola \ud83d"]}, "pending_questions"),
        ({"pending_questions": "abc"}, "pending_questions"),
        ({"language": "fr"}, "language"),
        ({"language": ["es"]}, "language"),
        ({"stop_requested": "false"}, "stop_requested"),
        ({"stop_requested": 1}, "stop_requested"),
    ],
)
def test_any_invalid_part_of_an_extraction_writes_nothing(db, extraction, field):
    application_id, revision = received(db)
    before = everything(db, application_id)
    arguments = {"proposals": [prop("full_name", "Ana")], **extraction}

    with pytest.raises(InvalidExtraction) as refused:
        commit_interpretation(db, application_id, "r1", revision, **arguments)

    assert field in [error.field for error in refused.value.errors]
    assert all(error.model_dump_json() for error in refused.value.errors)
    assert everything(db, application_id) == before


def test_extraction_that_cannot_be_serialized_is_invalid_and_writes_nothing(db, monkeypatch):
    application_id, revision = received(db)
    before = everything(db, application_id)

    def unserializable(fields, proposals):
        state = fields.full_name.model_copy(update={"raw": "hola \ud83d"})
        return ApplyResult(fields=fields, errors=[]).model_copy(
            update={"fields": fields.model_copy(update={"full_name": state})}
        )

    monkeypatch.setattr("backend.storage.apply_proposals", unserializable)

    with pytest.raises(InvalidExtraction):
        commit_interpretation(db, application_id, "r1", revision, proposals=[])

    assert everything(db, application_id) == before


def test_save_turn_keeps_its_per_field_errors(db):
    application = create_application(db)

    result = save_turn(
        db, application.id, 0, candidate_message="hola", proposals=[prop("driving_license", "yes")]
    )

    assert [error.field for error in result.errors] == ["driving_license"]
    assert result.snapshot.revision == 1


def test_retried_commit_interpretation_does_not_reapply_anything(db):
    application_id, revision = received(db)
    arguments = {
        "proposals": [prop("full_name", "Ana García")],
        "pending_questions": ["¿Cuánto se paga?"],
    }
    commit_interpretation(db, application_id, "r1", revision, **arguments)
    before = everything(db, application_id)

    for expected in (revision, revision + 1):
        with pytest.raises(WrongTurnPhase):
            commit_interpretation(db, application_id, "r1", expected, **arguments)

    assert everything(db, application_id) == before
    assert before[0].snapshot.pending_questions == ["¿Cuánto se paga?"]


@pytest.mark.parametrize(
    ("operation", "table"),
    [("UPDATE", "applications"), ("UPDATE", "turns")],
)
def test_failed_commit_interpretation_rolls_everything_back(db, operation, table):
    application_id, revision = received(db)
    before = everything(db, application_id)
    fail_on(db, operation, table)

    with pytest.raises(StorageFailure):
        commit_interpretation(
            db, application_id, "r1", revision, proposals=[prop("full_name", "Ana")]
        )

    assert everything(db, application_id) == before
    stop_failing(db)
    snapshot = commit_interpretation(
        db, application_id, "r1", revision, proposals=[prop("full_name", "Ana")]
    )
    assert snapshot.fields.full_name.status == "resolved"


def test_commit_interpretation_refuses_unknown_application_and_turn(db):
    application_id, revision = received(db)

    with pytest.raises(UnknownApplication):
        commit_interpretation(db, "nope", "r1", revision, proposals=[])
    with pytest.raises(UnknownTurn):
        commit_interpretation(db, application_id, "nope", revision, proposals=[])


def test_stale_commit_interpretation_loses_to_the_other_writer(db):
    application_id, revision = received(db)
    loaded = load_application(db, application_id).snapshot.revision
    save_turn(db, application_id, revision, proposals=[LICENSE_TRUE])
    before = everything(db, application_id)

    with pytest.raises(StaleRevision):
        commit_interpretation(
            db, application_id, "r1", loaded, proposals=[prop("full_name", "Ana")]
        )

    assert everything(db, application_id) == before


def test_commit_interpretation_keeps_a_recruiter_decision_made_meanwhile(db):
    application_id, revision = received(db)
    set_recruiter_decision(db, application_id, "advance")

    snapshot = commit_interpretation(db, application_id, "r1", revision, proposals=[])

    assert snapshot.recruiter_decision == "advance"


def test_complete_turn_saves_reply_question_context_and_finishes_the_turn(db):
    application_id, revision = interpreted(db)

    snapshot = complete_turn(
        db,
        application_id,
        "r1",
        revision,
        reply="¿Tienes carnet? \ud83d",
        question_field="driving_license",
        question_kind="initial",
    )

    stored = load_application(db, application_id)
    assert stored.snapshot == snapshot
    assert snapshot.revision == revision + 1
    assert snapshot.last_question_field == "driving_license"
    assert snapshot.last_question_kind == "initial"
    assert snapshot.fields.driving_license.clarifications_asked == 0
    assert [(m.sequence, m.role, m.text) for m in stored.messages][-1] == (
        2,
        "assistant",
        "¿Tienes carnet? ?",
    )
    turn = load_turn(db, application_id, "r1")
    assert (turn.phase, turn.reply_sequence) == ("completed", 2)
    assert load_unfinished_turn(db, application_id) is None


def test_clarification_consumes_budget_and_stop_acknowledgement_keeps_the_context(db):
    application_id, revision = interpreted(db)
    snapshot = complete_turn(
        db,
        application_id,
        "r1",
        revision,
        reply="¿Con apellidos?",
        question_field="full_name",
        question_kind="clarification",
    )
    assert snapshot.fields.full_name.clarifications_asked == 1

    receive_turn(db, application_id, "r2", "Paro aquí")
    revision = snapshot.revision + 1
    revision = commit_interpretation(
        db, application_id, "r2", revision, proposals=[], stop_requested=True
    ).revision
    acknowledged = complete_turn(db, application_id, "r2", revision, reply="De acuerdo.")

    assert acknowledged.last_question_field == "full_name"
    assert acknowledged.last_question_kind == "clarification"
    assert acknowledged.fields.full_name.clarifications_asked == 1


def run_clarified_turn(db, application_id, revision, request_id):
    receive_turn(db, application_id, request_id, "no sé")
    snapshot = commit_interpretation(db, application_id, request_id, revision + 1, proposals=[])
    return snapshot.revision


def test_third_clarification_is_refused_and_changes_nothing(db):
    application_id, revision = interpreted(db)
    clarify = {"question_field": "full_name", "question_kind": "clarification"}
    revision = complete_turn(db, application_id, "r1", revision, reply="¿Otra vez?", **clarify).revision
    revision = run_clarified_turn(db, application_id, revision, "r2")
    revision = complete_turn(db, application_id, "r2", revision, reply="¿Ejemplo?", **clarify).revision
    revision = run_clarified_turn(db, application_id, revision, "r3")
    before = everything(db, application_id)

    with pytest.raises(ClarificationRefused) as refused:
        complete_turn(db, application_id, "r3", revision, reply="¿Una más?", **clarify)

    assert refused.value.field == "full_name"
    assert everything(db, application_id) == before
    assert load_turn(db, application_id, "r3").phase == "interpreted"
    assert before[0].snapshot.fields.full_name.clarifications_asked == 2

    finished = complete_turn(
        db,
        application_id,
        "r3",
        revision,
        reply="¿Tienes carnet?",
        question_field="driving_license",
        question_kind="initial",
    )
    assert finished.fields.full_name.clarifications_asked == 2
    assert finished.last_question_field == "driving_license"


@pytest.mark.parametrize("reply", ["", "   ", None, 3])
def test_invalid_reply_writes_nothing(db, reply):
    application_id, revision = interpreted(db)
    before = everything(db, application_id)

    with pytest.raises(InvalidReply):
        complete_turn(db, application_id, "r1", revision, reply=reply)

    assert everything(db, application_id) == before


@pytest.mark.parametrize(
    "question",
    [
        {"question_field": "full_name"},
        {"question_kind": "initial"},
        {"question_field": "salary", "question_kind": "initial"},
        {"question_field": ["full_name"], "question_kind": "initial"},
        {"question_field": "full_name", "question_kind": "reminder"},
    ],
)
def test_inconsistent_question_context_is_a_caller_bug_and_writes_nothing(db, question):
    application_id, revision = interpreted(db)
    before = everything(db, application_id)

    with pytest.raises(ValueError):
        complete_turn(db, application_id, "r1", revision, reply="¿Algo?", **question)

    assert everything(db, application_id) == before


@pytest.mark.parametrize(
    ("operation", "table"),
    [("INSERT", "messages"), ("UPDATE", "applications"), ("UPDATE", "turns")],
)
def test_failed_complete_turn_rolls_everything_back(db, operation, table):
    application_id, revision = interpreted(db)
    before = everything(db, application_id)
    clarify = {"question_field": "full_name", "question_kind": "clarification"}
    fail_on(db, operation, table)

    with pytest.raises(StorageFailure):
        complete_turn(db, application_id, "r1", revision, reply="¿Otra vez?", **clarify)

    assert everything(db, application_id) == before
    stop_failing(db)
    snapshot = complete_turn(db, application_id, "r1", revision, reply="¿Otra vez?", **clarify)
    assert snapshot.fields.full_name.clarifications_asked == 1


def test_complete_turn_requires_the_interpreted_phase(db):
    application_id, revision = received(db)
    before = everything(db, application_id)

    with pytest.raises(WrongTurnPhase):
        complete_turn(db, application_id, "r1", revision, reply="Hola")

    assert everything(db, application_id) == before


def test_retried_complete_turn_does_not_append_or_count_again(db):
    application_id, revision = interpreted(db)
    clarify = {"question_field": "full_name", "question_kind": "clarification"}
    complete_turn(db, application_id, "r1", revision, reply="¿Otra vez?", **clarify)
    before = everything(db, application_id)

    with pytest.raises(WrongTurnPhase):
        complete_turn(db, application_id, "r1", revision, reply="¿Otra vez?", **clarify)

    assert everything(db, application_id) == before


def test_stale_complete_turn_is_refused(db):
    application_id, revision = interpreted(db)
    save_turn(db, application_id, revision, proposals=[LICENSE_TRUE])
    before = everything(db, application_id)

    with pytest.raises(StaleRevision):
        complete_turn(db, application_id, "r1", revision, reply="Hola")

    assert everything(db, application_id) == before


def test_complete_turn_refuses_unknown_application_and_turn(db):
    application_id, revision = interpreted(db)

    with pytest.raises(UnknownApplication):
        complete_turn(db, "nope", "r1", revision, reply="Hola")
    with pytest.raises(UnknownTurn):
        complete_turn(db, application_id, "nope", revision, reply="Hola")


def test_duplicate_receive_after_completion_returns_the_committed_reply(db):
    application_id, revision = interpreted(db)
    complete_turn(db, application_id, "r1", revision, reply="¿Tienes carnet?")
    before = everything(db, application_id)

    again = receive_turn(db, application_id, "r1", "Soy Ana")

    assert again.created is False
    assert again.turn.phase == "completed"
    assert again.turn.reply_sequence == 2
    assert [message.text for message in again.application.messages][-1] == "¿Tienes carnet?"
    assert everything(db, application_id) == before
    with pytest.raises(TurnConflict):
        receive_turn(db, application_id, "r1", "Soy Eva")


def test_next_request_is_accepted_after_completion(db):
    application_id, revision = interpreted(db)
    complete_turn(db, application_id, "r1", revision, reply="¿Tienes carnet?")

    assert receive_turn(db, application_id, "r2", "Sí").turn.candidate_sequence == 3


def test_close_with_request_id_saves_everything_and_completes_the_turn(db):
    application_id, revision = interpreted(db, proposals=ALL_RESOLVED)

    closed = close_application(
        db, application_id, revision, "Gracias \ud83d", "Resumen \ud83d", request_id="r1"
    )

    stored = load_application(db, application_id)
    assert stored.snapshot == closed
    assert closed.outcome == "qualified"
    assert closed.summary == "Resumen ?"
    assert closed.closed_at is not None
    assert closed.revision == revision + 1
    assert [message.text for message in stored.messages] == ["Soy Ana", "Gracias ?"]
    turn = load_turn(db, application_id, "r1")
    assert (turn.phase, turn.reply_sequence) == ("completed", 2)
    assert load_unfinished_turn(db, application_id) is None


def test_surrogate_in_the_summary_no_longer_blocks_an_e2_style_close(db):
    application = create_application(db)
    save_turn(db, application.id, 0, proposals=ALL_RESOLVED)

    closed = close_application(db, application.id, 1, "Gracias", "Resumen \ud83d")

    assert closed.summary == "Resumen ?"
    assert load_application(db, application.id).snapshot == closed


@pytest.mark.parametrize("phase", ["received", "interpreted"])
def test_close_without_a_request_id_cannot_orphan_an_unfinished_turn(db, phase):
    if phase == "received":
        application_id = create_application(db).id
        revision = receive_turn(db, application_id, "r1", "Hola").application.snapshot.revision
    else:
        application_id, revision = interpreted(db, proposals=ALL_RESOLVED)
    before = everything(db, application_id)

    with pytest.raises(CannotClose, match="turn_unfinished"):
        close_application(db, application_id, revision, "Gracias", "Resumen")

    assert everything(db, application_id) == before


def test_retry_of_a_completed_closing_request_gets_its_committed_result(db):
    application_id, revision = interpreted(db, proposals=ALL_RESOLVED)
    close_application(db, application_id, revision, "Gracias", "Resumen", request_id="r1")
    before = everything(db, application_id)

    again = receive_turn(db, application_id, "r1", "Soy Ana")

    assert again.created is False
    assert again.turn.phase == "completed"
    assert again.application.snapshot.outcome == "qualified"
    assert again.application.messages[-1].text == "Gracias"
    with pytest.raises(TurnConflict):
        receive_turn(db, application_id, "r1", "Soy Eva")
    with pytest.raises(ApplicationClosed):
        receive_turn(db, application_id, "r2", "Hola")
    assert everything(db, application_id) == before


@pytest.mark.parametrize(
    "operation",
    [
        lambda db, app, rev: close_application(db, app, rev, "Otra", "Otro", request_id="r1"),
        lambda db, app, rev: complete_turn(db, app, "r1", rev, reply="Otra"),
        lambda db, app, rev: commit_interpretation(db, app, "r1", rev, proposals=[]),
    ],
    ids=["close", "complete", "interpret"],
)
def test_retries_after_a_completed_close_report_the_phase_and_write_nothing(db, operation):
    application_id, revision = interpreted(db, proposals=ALL_RESOLVED)
    close_application(db, application_id, revision, "Gracias", "Resumen", request_id="r1")
    before = everything(db, application_id)

    for expected in (revision, revision + 1):
        with pytest.raises(WrongTurnPhase):
            operation(db, application_id, expected)

    assert everything(db, application_id) == before


def test_close_with_request_id_requires_the_interpreted_phase(db):
    application = create_application(db)
    save_turn(db, application.id, 0, proposals=ALL_RESOLVED)
    receive_turn(db, application.id, "r1", "Soy Ana")
    before = everything(db, application.id)

    with pytest.raises(WrongTurnPhase):
        close_application(db, application.id, 2, "Gracias", "Resumen", request_id="r1")

    assert everything(db, application.id) == before


def test_close_with_unknown_application_or_turn_is_refused(db):
    application_id, revision = interpreted(db, proposals=ALL_RESOLVED)

    with pytest.raises(UnknownApplication):
        close_application(db, "nope", revision, "Gracias", "Resumen", request_id="r1")
    with pytest.raises(UnknownTurn):
        close_application(db, application_id, revision, "Gracias", "Resumen", request_id="nope")


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"proposals": ALL_RESOLVED, "stop_requested": True}, "stop_requested"),
        ({"proposals": [LICENSE_TRUE]}, "not_closeable"),
    ],
    ids=["stopped", "not_closeable"],
)
def test_refused_close_with_request_id_leaves_the_turn_interpreted(db, changes, code):
    application_id, revision = interpreted(db, **changes)
    before = everything(db, application_id)

    with pytest.raises(CannotClose, match=code):
        close_application(db, application_id, revision, "Gracias", "Resumen", request_id="r1")

    assert everything(db, application_id) == before
    assert load_turn(db, application_id, "r1").phase == "interpreted"


@pytest.mark.parametrize(
    ("final_message", "summary", "code"),
    [(" ", "Resumen", "final_message_missing"), ("Gracias", 3, "summary_missing")],
)
def test_close_with_invalid_texts_is_refused_and_keeps_the_turn(db, final_message, summary, code):
    application_id, revision = interpreted(db, proposals=ALL_RESOLVED)
    before = everything(db, application_id)

    with pytest.raises(CannotClose, match=code):
        close_application(db, application_id, revision, final_message, summary, request_id="r1")

    assert everything(db, application_id) == before


def test_stale_close_with_request_id_is_refused(db):
    application_id, revision = interpreted(db, proposals=ALL_RESOLVED)
    save_turn(db, application_id, revision, pending_questions=["¿Cuánto?"])
    before = everything(db, application_id)

    with pytest.raises(StaleRevision):
        close_application(db, application_id, revision, "Gracias", "Resumen", request_id="r1")

    assert everything(db, application_id) == before


def test_close_re_evaluates_fields_changed_before_it_inside_the_transaction(db):
    application_id, revision = interpreted(db, proposals=ALL_RESOLVED)
    revision = save_turn(db, application_id, revision, proposals=[LICENSE_FALSE]).snapshot.revision

    closed = close_application(db, application_id, revision, "Gracias", "Resumen", request_id="r1")

    assert closed.outcome == "disqualified"
    assert closed.outcome_reasons == ["no_driving_license"]


@pytest.mark.parametrize(
    ("operation", "table"),
    [("INSERT", "messages"), ("UPDATE", "applications"), ("UPDATE", "turns")],
)
def test_failed_close_with_request_id_rolls_everything_back(db, operation, table):
    application_id, revision = interpreted(db, proposals=ALL_RESOLVED)
    before = everything(db, application_id)
    fail_on(db, operation, table)

    with pytest.raises(StorageFailure):
        close_application(db, application_id, revision, "Gracias", "Resumen", request_id="r1")

    assert everything(db, application_id) == before
    stop_failing(db)
    closed = close_application(db, application_id, revision, "Gracias", "Resumen", request_id="r1")
    assert closed.outcome == "qualified"


@pytest.mark.parametrize(
    ("prepare", "operation"),
    [
        (received, lambda db, app, rev: commit_interpretation(db, app, "r1", rev, proposals=[])),
        (interpreted, lambda db, app, rev: complete_turn(db, app, "r1", rev, reply="Hola")),
        (
            lambda db: interpreted(db, proposals=ALL_RESOLVED),
            lambda db, app, rev: close_application(db, app, rev, "Hola", "Resumen", request_id="r1"),
        ),
    ],
    ids=["interpret", "complete", "close"],
)
def test_exception_outside_sqlite_in_the_write_rolls_everything_back(
    db, monkeypatch, prepare, operation
):
    application_id, revision = prepare(db)
    before = everything(db, application_id)

    def boom(*_):
        raise RuntimeError("forced")

    monkeypatch.setattr("backend.storage._write_snapshot", boom)
    with pytest.raises(RuntimeError):
        operation(db, application_id, revision)
    monkeypatch.undo()

    assert everything(db, application_id) == before


def test_exception_outside_sqlite_rolls_back_receive(db, monkeypatch):
    application = create_application(db)
    before = everything(db, application.id)

    def boom(*_):
        raise RuntimeError("forced")

    monkeypatch.setattr("backend.storage._write_snapshot", boom)
    with pytest.raises(RuntimeError):
        receive_turn(db, application.id, "r1", "Soy Ana")
    monkeypatch.undo()

    assert everything(db, application.id) == before


RACERS = 8


def race(calls):
    """Run each call in its own thread, released together; each opens its own connection."""
    barrier = threading.Barrier(len(calls))
    outcomes = [None] * len(calls)

    def run(index, call):
        barrier.wait()
        try:
            outcomes[index] = call()
        except Exception as exc:
            outcomes[index] = exc

    threads = [threading.Thread(target=run, args=item) for item in enumerate(calls)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return outcomes


def test_racing_the_same_new_request_appends_one_message(db):
    application = create_application(db)

    outcomes = race(
        [lambda: receive_turn(db, application.id, "r1", "Soy Ana") for _ in range(RACERS)]
    )

    assert not [outcome for outcome in outcomes if isinstance(outcome, Exception)]
    assert sorted(outcome.created for outcome in outcomes) == [False] * (RACERS - 1) + [True]
    assert {outcome.turn.candidate_sequence for outcome in outcomes} == {1}
    assert message_texts(db, application.id) == ["Soy Ana"]
    assert load_application(db, application.id).snapshot.revision == 1


def test_racing_different_requests_leaves_one_unfinished_turn(db):
    application = create_application(db)

    outcomes = race(
        [
            lambda number=number: receive_turn(db, application.id, f"r{number}", f"texto {number}")
            for number in range(RACERS)
        ]
    )

    winners = [outcome for outcome in outcomes if not isinstance(outcome, Exception)]
    losers = [outcome for outcome in outcomes if isinstance(outcome, Exception)]
    assert len(winners) == 1
    assert winners[0].created is True
    assert all(isinstance(loser, TurnPending) for loser in losers)
    assert {loser.request_id for loser in losers} == {winners[0].turn.request_id}
    assert load_unfinished_turn(db, application.id) == winners[0].turn
    assert len(message_texts(db, application.id)) == 1
    assert execute(db, "SELECT COUNT(*) FROM turns") == [(1,)]
    assert load_application(db, application.id).snapshot.revision == 1


def test_racing_commits_of_one_interpretation_apply_it_once(db):
    application_id, revision = received(db)

    outcomes = race(
        [
            lambda: commit_interpretation(
                db, application_id, "r1", revision, proposals=[], pending_questions=["¿Cuánto?"]
            )
            for _ in range(RACERS)
        ]
    )

    assert sum(isinstance(outcome, WrongTurnPhase) for outcome in outcomes) == RACERS - 1
    stored = load_application(db, application_id).snapshot
    assert stored.pending_questions == ["¿Cuánto?"]
    assert stored.revision == revision + 1


@pytest.mark.parametrize(
    "finish",
    [
        lambda db, app, rev: complete_turn(db, app, "r1", rev, reply="Fin"),
        lambda db, app, rev: close_application(db, app, rev, "Fin", "Resumen", request_id="r1"),
    ],
    ids=["complete", "close"],
)
def test_racing_finishes_of_one_turn_append_one_reply(db, finish):
    application_id, revision = interpreted(db, proposals=ALL_RESOLVED)

    outcomes = race([lambda: finish(db, application_id, revision) for _ in range(RACERS)])

    assert sum(isinstance(outcome, WrongTurnPhase) for outcome in outcomes) == RACERS - 1
    assert message_texts(db, application_id) == ["Soy Ana", "Fin"]
    assert load_application(db, application_id).snapshot.revision == revision + 1


# The single reminder

T0 = datetime(2026, 11, 5, 10, 0, tzinfo=timezone.utc)
REMINDER_ES = "Si quieres, podemos continuar cuando estés listo."
REMINDER_EN = "If you'd like, we can continue when you're ready."
TEN_MINUTES = timedelta(minutes=10)


class Clock:
    def __init__(self):
        self.now = T0

    def advance(self, delta=TEN_MINUTES):
        self.now += delta


@pytest.fixture
def clock(monkeypatch):
    controlled = Clock()
    monkeypatch.setattr(storage, "_now", lambda: controlled.now)
    return controlled


def greeted(db):
    return create_application(db, greeting="Hola, ¿cómo te llamas?").id


def answered_turn(db, application_id, request_id, **interpretation):
    """A whole candidate turn, leaving the given interpretation applied."""
    revision = receive_turn(db, application_id, request_id, "texto").application.snapshot.revision
    kept = {"proposals": [], **interpretation}
    interpreted = commit_interpretation(db, application_id, request_id, revision, **kept)
    return complete_turn(db, application_id, request_id, interpreted.revision, reply="Vale")


def due_at(db, application_id):
    stored, turn = load_application_with_turn(db, application_id)
    return reminder_due_at(stored, turn)


@pytest.mark.parametrize(
    "elapsed, sent",
    [(TEN_MINUTES - timedelta(microseconds=1), False), (TEN_MINUTES, True), (TEN_MINUTES * 2, True)],
)
def test_reminder_needs_ten_minutes_since_the_greeting(db, clock, elapsed, sent):
    application_id = greeted(db)
    clock.advance(elapsed)

    assert send_reminder(db, application_id) is sent

    stored = load_application(db, application_id)
    assert stored.snapshot.reminder_sent is sent
    assert len(stored.messages) == (2 if sent else 1)
    if sent:
        assert stored.messages[-1].role == "assistant"
        assert stored.messages[-1].created_at == clock.now.isoformat()


@pytest.mark.parametrize("language, text", [("es", REMINDER_ES), ("en", REMINDER_EN)])
def test_reminder_uses_the_saved_language(db, clock, language, text):
    application_id = greeted(db)
    answered_turn(db, application_id, "r1", language=language)
    clock.advance()

    assert send_reminder(db, application_id) is True
    assert message_texts(db, application_id)[-1] == text


def test_reminder_counts_from_the_last_assistant_message_not_from_the_snapshot(db, clock):
    application_id = greeted(db)
    clock.advance()
    set_recruiter_decision(db, application_id, "advance")

    assert send_reminder(db, application_id) is True


def test_reminder_waits_for_ten_minutes_after_the_latest_reply(db, clock):
    application_id = greeted(db)
    clock.advance()
    answered_turn(db, application_id, "r1")

    assert send_reminder(db, application_id) is False
    clock.advance()
    assert send_reminder(db, application_id) is True


def stopped(db, application_id):
    answered_turn(db, application_id, "r1", stop_requested=True)


def closed(db, application_id):
    revision = receive_turn(db, application_id, "r1", "No").application.snapshot.revision
    interpreted = commit_interpretation(
        db, application_id, "r1", revision, proposals=[LICENSE_FALSE]
    )
    close_application(db, application_id, interpreted.revision, "Fin", "Resumen", request_id="r1")


def pending(db, application_id):
    receive_turn(db, application_id, "r1", "Soy Ana")


def pending_with_an_assistant_message_last(db, application_id):
    revision = receive_turn(db, application_id, "r1", "Soy Ana").application.snapshot.revision
    save_turn(db, application_id, revision, assistant_message="Un momento")


def already_reminded(db, application_id):
    original = storage._now
    storage._now = lambda: T0 + TEN_MINUTES
    assert send_reminder(db, application_id) is True
    storage._now = original


def candidate_spoke_last(db, application_id):
    revision = load_application(db, application_id).snapshot.revision
    save_turn(db, application_id, revision, candidate_message="hola")


@pytest.mark.parametrize(
    "prepare",
    [
        stopped,
        closed,
        pending,
        pending_with_an_assistant_message_last,
        already_reminded,
        candidate_spoke_last,
    ],
)
def test_no_reminder_when_it_is_not_eligible_and_nothing_changes(db, clock, prepare):
    application_id = greeted(db)
    prepare(db, application_id)
    clock.advance(TEN_MINUTES * 3)
    before = everything(db, application_id)

    assert due_at(db, application_id) is None
    assert send_reminder(db, application_id) is False
    assert everything(db, application_id) == before


def test_no_reminder_without_any_message(db, clock):
    application_id = create_application(db).id
    clock.advance()

    assert due_at(db, application_id) is None
    assert send_reminder(db, application_id) is False


def test_reminder_of_an_unknown_application_is_refused(db):
    with pytest.raises(UnknownApplication):
        send_reminder(db, "nope")


@pytest.mark.parametrize("operation, table", [("INSERT", "messages"), ("UPDATE", "applications")])
def test_failed_reminder_rolls_back_and_can_be_retried(db, clock, operation, table):
    application_id = greeted(db)
    clock.advance()
    before = everything(db, application_id)
    fail_on(db, operation, table)

    with pytest.raises(StorageFailure):
        send_reminder(db, application_id)

    stop_failing(db)
    assert everything(db, application_id) == before
    assert send_reminder(db, application_id) is True
    assert message_texts(db, application_id)[-1] == REMINDER_ES


def test_racing_reminders_save_only_one(db, clock):
    application_id = greeted(db)
    clock.advance()

    outcomes = race([lambda: send_reminder(db, application_id) for _ in range(RACERS)])

    assert sorted(outcomes) == [False] * (RACERS - 1) + [True]
    assert message_texts(db, application_id).count(REMINDER_ES) == 1


def test_reminder_keeps_fields_question_context_and_counts(db, clock):
    application_id = greeted(db)
    revision = receive_turn(db, application_id, "r1", "Ana").application.snapshot.revision
    interpreted = commit_interpretation(
        db, application_id, "r1", revision, proposals=[unresolved("full_name")]
    )
    complete_turn(
        db,
        application_id,
        "r1",
        interpreted.revision,
        reply="¿Tu nombre completo?",
        question_field="full_name",
        question_kind="clarification",
    )
    before = load_application(db, application_id).snapshot
    turns_before = execute(db, "SELECT * FROM turns WHERE application_id = ?", application_id)
    clock.advance()

    assert send_reminder(db, application_id) is True

    after = load_application(db, application_id).snapshot
    assert after.fields == before.fields
    assert after.fields.full_name.clarifications_asked == 1
    assert (after.last_question_field, after.last_question_kind) == ("full_name", "clarification")
    assert (after.outcome, after.summary, after.closed_at) == (before.outcome, None, None)
    assert after.pending_questions == before.pending_questions
    assert after.revision == before.revision + 1
    assert execute(db, "SELECT * FROM turns WHERE application_id = ?", application_id) == turns_before


def test_a_turn_can_follow_a_reminder(db, clock):
    application_id = greeted(db)
    clock.advance()
    send_reminder(db, application_id)

    answered_turn(db, application_id, "r1")

    assert message_texts(db, application_id)[-2:] == ["texto", "Vale"]


def test_resume_permits_a_first_reminder(db, clock):
    application_id = greeted(db)
    answered_turn(db, application_id, "r1", stop_requested=True)
    answered_turn(db, application_id, "r2", stop_requested=False)
    clock.advance()

    assert send_reminder(db, application_id) is True


def test_stopping_and_resuming_never_permit_a_second_reminder(db, clock):
    application_id = greeted(db)
    clock.advance()
    send_reminder(db, application_id)
    answered_turn(db, application_id, "r1", stop_requested=True)
    answered_turn(db, application_id, "r2", stop_requested=False)
    clock.advance()

    assert load_application(db, application_id).snapshot.reminder_sent is True
    assert due_at(db, application_id) is None
    assert send_reminder(db, application_id) is False


def test_due_time_is_ten_minutes_after_the_last_assistant_message(db, clock):
    application_id = greeted(db)
    assert due_at(db, application_id) == T0 + TEN_MINUTES

    clock.advance(timedelta(minutes=3))
    answered_turn(db, application_id, "r1")

    assert due_at(db, application_id) == T0 + timedelta(minutes=13)
