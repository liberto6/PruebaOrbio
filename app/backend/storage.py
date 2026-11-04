import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError

from backend.rules import (
    CandidateSnapshot,
    FieldError,
    NonBlank,
    StrictModel,
    apply_proposals,
    evaluate,
    new_snapshot,
    record_clarifications,
)

_QUESTION = TypeAdapter(NonBlank)


class StorageFailure(Exception):
    """A technical failure: the operation rolled back and can be retried."""


class UnknownApplication(Exception):
    pass


class ApplicationClosed(Exception):
    pass


class StaleRevision(Exception):
    pass


class CannotClose(Exception):
    """Refused close; the message is a stable reason code."""


class Message(StrictModel):
    sequence: int
    role: Literal["candidate", "assistant"]
    text: str
    created_at: str


class StoredApplication(StrictModel):
    snapshot: CandidateSnapshot
    messages: list[Message]


class TurnResult(StrictModel):
    snapshot: CandidateSnapshot
    errors: list[FieldError]


@contextmanager
def _transaction(db_path):
    connection = None
    try:
        connection = sqlite3.connect(db_path, isolation_level=None)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        yield connection
        connection.execute("COMMIT")
    except sqlite3.Error as exc:
        raise StorageFailure(str(exc)) from exc
    finally:
        # Closing with the transaction still open rolls it back.
        if connection is not None:
            connection.close()


def init_db(db_path) -> None:
    with _transaction(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS applications (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                closed_at TEXT,
                outcome TEXT NOT NULL,
                recruiter_decision TEXT NOT NULL,
                snapshot TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                application_id TEXT NOT NULL REFERENCES applications (id),
                sequence INTEGER NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('candidate', 'assistant')),
                text TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (application_id, sequence)
            )
            """
        )


def create_application(db_path) -> CandidateSnapshot:
    snapshot = new_snapshot(uuid4().hex, _now())
    with _transaction(db_path) as connection:
        connection.execute(
            "INSERT INTO applications"
            " (created_at, updated_at, closed_at, outcome, recruiter_decision, snapshot, id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (*_column_values(snapshot), snapshot.id),
        )
    return snapshot


def load_application(db_path, application_id: str) -> StoredApplication:
    with _transaction(db_path) as connection:
        snapshot = _read_snapshot(connection, application_id)
        rows = connection.execute(
            "SELECT sequence, role, text, created_at FROM messages"
            " WHERE application_id = ? ORDER BY sequence",
            (application_id,),
        ).fetchall()
    messages = [
        Message(sequence=sequence, role=role, text=text, created_at=created_at)
        for sequence, role, text, created_at in rows
    ]
    return StoredApplication(snapshot=snapshot, messages=messages)


def save_turn(
    db_path,
    application_id: str,
    expected_revision: int,
    *,
    candidate_message: str | None = None,
    assistant_message: str | None = None,
    proposals=(),
    pending_questions=(),
    clarifications=(),
    stop_requested: bool | None = None,
) -> TurnResult:
    _check_argument_types(candidate_message, assistant_message, stop_requested)
    with _transaction(db_path) as connection:
        snapshot = _read_open_snapshot(connection, application_id, expected_revision)
        applied = apply_proposals(snapshot.fields, proposals)
        counted = record_clarifications(applied.fields, clarifications)
        refusals = counted.errors
        if clarifications and assistant_message is None:
            refusals = [*refusals, FieldError(field=None, error="clarification_without_message")]
        # A refusal counts nothing and drops the question, so E3 can reply differently.
        fields = applied.fields if refusals else counted.fields
        questions, question_errors = _valid_questions(pending_questions)
        now = _now()
        updated = snapshot.model_copy(
            update={
                "fields": fields,
                "pending_questions": [*snapshot.pending_questions, *questions],
                "stop_requested": (
                    snapshot.stop_requested if stop_requested is None else stop_requested
                ),
                "updated_at": now,
                "revision": snapshot.revision + 1,
            }
        )
        if candidate_message is not None:
            _append_message(connection, application_id, "candidate", candidate_message, now)
        if assistant_message is not None and not refusals:
            _append_message(connection, application_id, "assistant", assistant_message, now)
        _write_snapshot(connection, updated)
    return TurnResult(snapshot=updated, errors=applied.errors + refusals + question_errors)


def close_application(
    db_path, application_id: str, expected_revision: int, final_message: str, summary: str
) -> CandidateSnapshot:
    with _transaction(db_path) as connection:
        snapshot = _read_open_snapshot(connection, application_id, expected_revision)
        if snapshot.stop_requested:
            raise CannotClose("stop_requested")
        if _is_blank(final_message):
            raise CannotClose("final_message_missing")
        if _is_blank(summary):
            raise CannotClose("summary_missing")
        evaluation = evaluate(snapshot.fields)
        if not evaluation.closeable:
            raise CannotClose("not_closeable")
        now = _now()
        closed = snapshot.model_copy(
            update={
                "outcome": evaluation.outcome,
                "outcome_reasons": evaluation.outcome_reasons,
                "closed_at": now,
                "summary": summary,
                "updated_at": now,
                "revision": snapshot.revision + 1,
            }
        )
        _append_message(connection, application_id, "assistant", final_message, now)
        _write_snapshot(connection, closed)
    return closed


def set_recruiter_decision(db_path, application_id: str, decision: str) -> CandidateSnapshot:
    with _transaction(db_path) as connection:
        snapshot = _read_snapshot(connection, application_id)
        # Validating rejects an unknown decision. The revision stays the same on purpose,
        # so a recruiter action cannot make a candidate's turn in progress stale.
        updated = CandidateSnapshot.model_validate(
            snapshot.model_dump() | {"recruiter_decision": decision, "updated_at": _now()}
        )
        _write_snapshot(connection, updated)
    return updated


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _is_blank(text: str | None) -> bool:
    return text is None or not text.strip()


def _check_argument_types(candidate_message, assistant_message, stop_requested) -> None:
    for message in (candidate_message, assistant_message):
        if message is not None and not isinstance(message, str):
            raise TypeError("messages must be strings or None")
    if stop_requested is not None and not isinstance(stop_requested, bool):
        raise TypeError("stop_requested must be a bool or None")


def _valid_questions(submitted) -> tuple[list[str], list[FieldError]]:
    invalid = FieldError(field="pending_questions", error="invalid_pending_question")
    if not isinstance(submitted, (list, tuple)):
        return [], [invalid]
    questions = []
    errors = []
    for question in submitted:
        try:
            questions.append(_QUESTION.validate_python(question, strict=True))
        except ValidationError:
            errors.append(invalid)
    return questions, errors


def _column_values(snapshot: CandidateSnapshot) -> tuple:
    """Filter columns and JSON, all taken from the same snapshot."""
    data = snapshot.model_dump(mode="json")
    return (
        data["created_at"],
        data["updated_at"],
        data["closed_at"],
        data["outcome"],
        data["recruiter_decision"],
        snapshot.model_dump_json(),
    )


def _read_snapshot(connection, application_id: str) -> CandidateSnapshot:
    row = connection.execute(
        "SELECT snapshot FROM applications WHERE id = ?", (application_id,)
    ).fetchone()
    if row is None:
        raise UnknownApplication(application_id)
    return CandidateSnapshot.model_validate_json(row[0])


def _read_open_snapshot(connection, application_id: str, expected_revision: int) -> CandidateSnapshot:
    snapshot = _read_snapshot(connection, application_id)
    if snapshot.closed_at is not None:
        raise ApplicationClosed(application_id)
    if snapshot.revision != expected_revision:
        raise StaleRevision(application_id)
    return snapshot


def _write_snapshot(connection, snapshot: CandidateSnapshot) -> None:
    connection.execute(
        "UPDATE applications SET created_at = ?, updated_at = ?, closed_at = ?, outcome = ?,"
        " recruiter_decision = ?, snapshot = ? WHERE id = ?",
        (*_column_values(snapshot), snapshot.id),
    )


def _append_message(connection, application_id: str, role: str, text: str, now: datetime) -> None:
    last = connection.execute(
        "SELECT COALESCE(MAX(sequence), 0) FROM messages WHERE application_id = ?",
        (application_id,),
    ).fetchone()[0]
    connection.execute(
        "INSERT INTO messages (application_id, sequence, role, text, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (application_id, last + 1, role, text.encode("utf-8", "replace").decode(), now.isoformat()),
    )
