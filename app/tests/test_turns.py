import json
import sqlite3

import pytest

from backend.rules import CandidateSnapshot
from backend.storage import create_application, init_db, load_application, save_turn

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
