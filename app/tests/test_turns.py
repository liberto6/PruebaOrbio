import json
import sqlite3

import pytest

from backend.rules import CandidateSnapshot
from backend.storage import (
    ApplicationClosed,
    InvalidReply,
    StorageFailure,
    TurnConflict,
    TurnPending,
    UnknownApplication,
    UnknownTurn,
    close_application,
    create_application,
    init_db,
    load_application,
    load_turn,
    load_unfinished_turn,
    receive_turn,
    save_turn,
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
