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


class InvalidReply(Exception):
    """An assistant text that is not a nonblank string."""


class UnknownTurn(Exception):
    pass


class TurnConflict(Exception):
    """The request id already exists with a different text."""


class TurnPending(Exception):
    """Another turn is unfinished; request_id names the one to resume."""

    def __init__(self, request_id: str):
        super().__init__(request_id)
        self.request_id = request_id


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


class Turn(StrictModel):
    request_id: str
    phase: Literal["received", "interpreted", "completed"]
    candidate_sequence: int
    reply_sequence: int | None


class ReceivedTurn(StrictModel):
    application: StoredApplication
    turn: Turn
    created: bool


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
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS turns (
                application_id TEXT NOT NULL REFERENCES applications (id),
                request_id TEXT NOT NULL,
                candidate_sequence INTEGER NOT NULL,
                phase TEXT NOT NULL CHECK (phase IN ('received', 'interpreted', 'completed')),
                reply_sequence INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (application_id, request_id)
            )
            """
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS one_unfinished_turn_per_application
            ON turns (application_id) WHERE phase <> 'completed'
            """
        )


def create_application(db_path, greeting: str | None = None) -> CandidateSnapshot:
    now = _now()
    snapshot = new_snapshot(uuid4().hex, now)
    if greeting is not None:
        _check_reply(greeting)
        snapshot = snapshot.model_copy(
            update={
                "last_question_field": "full_name",
                "last_question_kind": "initial",
                "revision": snapshot.revision + 1,
            }
        )
    with _transaction(db_path) as connection:
        connection.execute(
            "INSERT INTO applications"
            " (created_at, updated_at, closed_at, outcome, recruiter_decision, snapshot, id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (*_column_values(snapshot), snapshot.id),
        )
        if greeting is not None:
            _append_message(connection, snapshot.id, "assistant", greeting, now)
    return snapshot


def load_application(db_path, application_id: str) -> StoredApplication:
    with _transaction(db_path) as connection:
        return _read_stored(connection, application_id)


def load_turn(db_path, application_id: str, request_id: str) -> Turn:
    with _transaction(db_path) as connection:
        _read_snapshot(connection, application_id)
        return _require_turn(connection, application_id, request_id)


def load_unfinished_turn(db_path, application_id: str) -> Turn | None:
    with _transaction(db_path) as connection:
        _read_snapshot(connection, application_id)
        return _unfinished_turn(connection, application_id)


def receive_turn(db_path, application_id: str, request_id: str, text: str) -> ReceivedTurn:
    if not isinstance(request_id, str) or not isinstance(text, str):
        raise TypeError("request_id and text must be strings")
    request_id = _sanitized(request_id)
    with _transaction(db_path) as connection:
        snapshot = _read_snapshot(connection, application_id)
        turn = _find_turn(connection, application_id, request_id)
        created = turn is None
        if created:
            if snapshot.closed_at is not None:
                raise ApplicationClosed(application_id)
            pending = _unfinished_turn(connection, application_id)
            if pending is not None:
                raise TurnPending(pending.request_id)
            now = _now()
            sequence = _append_message(connection, application_id, "candidate", text, now)
            connection.execute(
                "INSERT INTO turns (application_id, request_id, candidate_sequence, phase,"
                " created_at, updated_at) VALUES (?, ?, ?, 'received', ?, ?)",
                (application_id, request_id, sequence, now.isoformat(), now.isoformat()),
            )
            _write_snapshot(connection, _next_revision(snapshot, now))
            turn = _require_turn(connection, application_id, request_id)
        elif _message_text(connection, application_id, turn.candidate_sequence) != _sanitized(text):
            raise TurnConflict(request_id)
        return ReceivedTurn(
            application=_read_stored(connection, application_id), turn=turn, created=created
        )


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
    return not isinstance(text, str) or not text.strip()


def _sanitized(text: str) -> str:
    """Lone surrogates cannot be stored or serialized, so they become `?`."""
    return text.encode("utf-8", "replace").decode()


def _check_reply(reply) -> None:
    if _is_blank(reply):
        raise InvalidReply("reply_missing")


def _next_revision(snapshot: CandidateSnapshot, now: datetime, **changes) -> CandidateSnapshot:
    return snapshot.model_copy(
        update={**changes, "updated_at": now, "revision": snapshot.revision + 1}
    )


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


def _read_stored(connection, application_id: str) -> StoredApplication:
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


def _message_text(connection, application_id: str, sequence: int) -> str:
    return connection.execute(
        "SELECT text FROM messages WHERE application_id = ? AND sequence = ?",
        (application_id, sequence),
    ).fetchone()[0]


_TURN_COLUMNS = "request_id, phase, candidate_sequence, reply_sequence"


def _turn_from_row(row) -> Turn:
    request_id, phase, candidate_sequence, reply_sequence = row
    return Turn(
        request_id=request_id,
        phase=phase,
        candidate_sequence=candidate_sequence,
        reply_sequence=reply_sequence,
    )


def _find_turn(connection, application_id: str, request_id: str) -> Turn | None:
    row = connection.execute(
        f"SELECT {_TURN_COLUMNS} FROM turns WHERE application_id = ? AND request_id = ?",
        (application_id, _sanitized(request_id)),
    ).fetchone()
    return None if row is None else _turn_from_row(row)


def _require_turn(connection, application_id: str, request_id: str) -> Turn:
    turn = _find_turn(connection, application_id, request_id)
    if turn is None:
        raise UnknownTurn(request_id)
    return turn


def _unfinished_turn(connection, application_id: str) -> Turn | None:
    row = connection.execute(
        f"SELECT {_TURN_COLUMNS} FROM turns WHERE application_id = ? AND phase <> 'completed'",
        (application_id,),
    ).fetchone()
    return None if row is None else _turn_from_row(row)


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


def _append_message(connection, application_id: str, role: str, text: str, now: datetime) -> int:
    last = connection.execute(
        "SELECT COALESCE(MAX(sequence), 0) FROM messages WHERE application_id = ?",
        (application_id,),
    ).fetchone()[0]
    connection.execute(
        "INSERT INTO messages (application_id, sequence, role, text, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (application_id, last + 1, role, _sanitized(text), now.isoformat()),
    )
    return last + 1
