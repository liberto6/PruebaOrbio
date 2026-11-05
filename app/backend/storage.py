import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError

from backend.rules import (
    CandidateFields,
    CandidateSnapshot,
    FieldError,
    Language,
    NonBlank,
    StrictModel,
    apply_proposals,
    evaluate,
    hold_location_for_alternatives,
    new_snapshot,
    record_clarifications,
)

_QUESTION = TypeAdapter(NonBlank)
_LANGUAGE = TypeAdapter(Language)

REMINDER_DELAY = timedelta(minutes=10)
REMINDER_TEXTS = {
    "es": "Si quieres, podemos continuar cuando estés listo.",
    "en": "If you'd like, we can continue when you're ready.",
}


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


class WrongTurnPhase(Exception):
    """The turn is not in the phase this step needs: reload and resume it."""


class InvalidExtraction(Exception):
    """An interpretation with an invalid part; errors has the details, nothing was written."""

    def __init__(self, errors: list[FieldError]):
        self.errors = [
            FieldError(
                field=None if error.field is None else _sanitized(error.field),
                error=_sanitized(error.error),
            )
            for error in errors
        ]
        super().__init__("; ".join(error.error for error in self.errors))


class ClarificationRefused(Exception):
    """The clarification was not saved; field and error say why, nothing was written."""

    def __init__(self, field: str, error: str):
        super().__init__(f"{field}: {error}")
        self.field = field
        self.error = error


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


def list_applications(
    db_path, outcome: str | None = None, recruiter_decision: str | None = None
) -> list[CandidateSnapshot]:
    """Matching snapshots, newest first then by id. Conversations are not read."""
    with _transaction(db_path) as connection:
        rows = connection.execute(
            "SELECT snapshot FROM applications"
            " WHERE (:outcome IS NULL OR outcome = :outcome)"
            " AND (:decision IS NULL OR recruiter_decision = :decision)",
            {"outcome": outcome, "decision": recruiter_decision},
        ).fetchall()
    snapshots = [CandidateSnapshot.model_validate_json(row[0]) for row in rows]
    # Sorted as instants: the stored text omits the fraction of a whole second, so it misorders.
    return sorted(snapshots, key=lambda snapshot: (-snapshot.created_at.timestamp(), snapshot.id))


def load_turn(db_path, application_id: str, request_id: str) -> Turn:
    with _transaction(db_path) as connection:
        _read_snapshot(connection, application_id)
        return _require_turn(connection, application_id, request_id)


def load_unfinished_turn(db_path, application_id: str) -> Turn | None:
    with _transaction(db_path) as connection:
        _read_snapshot(connection, application_id)
        return _unfinished_turn(connection, application_id)


def load_application_with_turn(
    db_path, application_id: str
) -> tuple[StoredApplication, Turn | None]:
    """The application and its unfinished turn, read together so they cannot disagree."""
    with _transaction(db_path) as connection:
        stored = _read_stored(connection, application_id)
        return stored, _unfinished_turn(connection, application_id)


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
        updated = _next_revision(
            snapshot,
            now,
            fields=fields,
            pending_questions=[*snapshot.pending_questions, *questions],
            stop_requested=snapshot.stop_requested if stop_requested is None else stop_requested,
        )
        if candidate_message is not None:
            _append_message(connection, application_id, "candidate", candidate_message, now)
        if assistant_message is not None and not refusals:
            _append_message(connection, application_id, "assistant", assistant_message, now)
        _write_snapshot(connection, updated)
    return TurnResult(snapshot=updated, errors=applied.errors + refusals + question_errors)


def commit_interpretation(
    db_path,
    application_id: str,
    request_id: str,
    expected_revision: int,
    *,
    proposals,
    pending_questions=(),
    language: str | None = None,
    stop_requested: bool | None = None,
) -> CandidateSnapshot:
    with _transaction(db_path) as connection:
        snapshot, turn = _read_turn_state(
            connection, application_id, request_id, "received", expected_revision
        )
        now = _now()
        updated = _interpreted_snapshot(
            snapshot, now, proposals, pending_questions, language, stop_requested
        )
        _write_snapshot(connection, updated)
        _advance_turn(connection, application_id, turn.request_id, "interpreted", now)
    return updated


def complete_turn(
    db_path,
    application_id: str,
    request_id: str,
    expected_revision: int,
    *,
    reply: str,
    question_field: str | None = None,
    question_kind: str | None = None,
) -> CandidateSnapshot:
    _check_question_context(question_field, question_kind)
    with _transaction(db_path) as connection:
        snapshot, turn = _read_turn_state(
            connection, application_id, request_id, "interpreted", expected_revision
        )
        _check_reply(reply)
        changes = {}
        if question_kind == "clarification":
            counted = record_clarifications(snapshot.fields, [question_field])
            if counted.errors:
                raise ClarificationRefused(question_field, counted.errors[0].error)
            changes["fields"] = counted.fields
        if question_kind == "alternatives":
            changes["fields"] = hold_location_for_alternatives(snapshot.fields)
            changes["location_alternatives_offered"] = True
        if question_field is not None:
            changes["last_question_field"] = question_field
            changes["last_question_kind"] = question_kind
        now = _now()
        sequence = _append_message(connection, application_id, "assistant", reply, now)
        updated = _next_revision(snapshot, now, **changes)
        _write_snapshot(connection, updated)
        _advance_turn(connection, application_id, turn.request_id, "completed", now, sequence)
    return updated


def close_application(
    db_path,
    application_id: str,
    expected_revision: int,
    final_message: str,
    summary: str,
    *,
    request_id: str | None = None,
) -> CandidateSnapshot:
    with _transaction(db_path) as connection:
        if request_id is None:
            turn = None
            snapshot = _read_open_snapshot(connection, application_id, expected_revision)
            if _unfinished_turn(connection, application_id) is not None:
                raise CannotClose("turn_unfinished")
        else:
            snapshot, turn = _read_turn_state(
                connection, application_id, request_id, "interpreted", expected_revision
            )
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
        closed = _next_revision(
            snapshot,
            now,
            outcome=evaluation.outcome,
            outcome_reasons=evaluation.outcome_reasons,
            closed_at=now,
            summary=_sanitized(summary),
        )
        sequence = _append_message(connection, application_id, "assistant", final_message, now)
        _write_snapshot(connection, closed)
        if turn is not None:
            _advance_turn(connection, application_id, turn.request_id, "completed", now, sequence)
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


def reminder_due_at(stored: StoredApplication, unfinished_turn: Turn | None) -> datetime | None:
    """When the single reminder may be sent, or None if it cannot be. Never uses `updated_at`."""
    snapshot = stored.snapshot
    if (
        snapshot.closed_at is not None
        or snapshot.stop_requested
        or snapshot.reminder_sent
        or unfinished_turn is not None
        or not stored.messages
        or stored.messages[-1].role != "assistant"
    ):
        return None
    return datetime.fromisoformat(stored.messages[-1].created_at) + REMINDER_DELAY


def send_reminder(db_path, application_id: str) -> bool:
    """Saves the fixed reminder if it is due. False means it was not eligible: nothing changed."""
    with _transaction(db_path) as connection:
        stored = _read_stored(connection, application_id)
        due = reminder_due_at(stored, _unfinished_turn(connection, application_id))
        now = _now()
        if due is None or now < due:
            return False
        snapshot = stored.snapshot
        text = REMINDER_TEXTS[snapshot.language]
        _append_message(connection, application_id, "assistant", text, now)
        _write_snapshot(connection, _next_revision(snapshot, now, reminder_sent=True))
    return True


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


def _check_question_context(question_field, question_kind) -> None:
    if (question_field is None) != (question_kind is None):
        raise ValueError("question_field and question_kind go together")
    if question_field is None:
        return
    if not isinstance(question_field, str) or question_field not in CandidateFields.model_fields:
        raise ValueError("question_field must be a screening field")
    if question_kind not in ("initial", "clarification", "alternatives"):
        raise ValueError("question_kind must be initial, clarification or alternatives")


def _interpreted_snapshot(
    snapshot: CandidateSnapshot, now: datetime, proposals, pending_questions, language, stop_requested
) -> CandidateSnapshot:
    """The snapshot with a whole interpretation applied, or InvalidExtraction."""
    try:
        errors = []
        fields = snapshot.fields
        if isinstance(proposals, (list, tuple)):
            applied = apply_proposals(fields, proposals)
            fields = applied.fields
            errors.extend(applied.errors)
        else:
            errors.append(FieldError(field=None, error="invalid_proposals"))
        questions, question_errors = _valid_questions(pending_questions)
        errors.extend(question_errors)
        if language is not None and not _is_language(language):
            errors.append(FieldError(field="language", error="invalid_language"))
        if stop_requested is not None and not isinstance(stop_requested, bool):
            errors.append(FieldError(field="stop_requested", error="invalid_stop_requested"))
        if errors:
            raise InvalidExtraction(errors)
        updated = _next_revision(
            snapshot,
            now,
            fields=fields,
            pending_questions=[*snapshot.pending_questions, *questions],
            questions_this_turn=questions,
            language=snapshot.language if language is None else language,
            stop_requested=snapshot.stop_requested if stop_requested is None else stop_requested,
        )
        updated.model_dump_json()
    except ValueError as exc:  # Includes Pydantic validation and serialization errors.
        unusable = FieldError(field=None, error="unserializable_output")
        raise InvalidExtraction([unusable]) from exc
    return updated


def _is_language(language) -> bool:
    try:
        _LANGUAGE.validate_python(language, strict=True)
    except ValidationError:
        return False
    return True


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
    _check_open(snapshot, expected_revision)
    return snapshot


def _check_open(snapshot: CandidateSnapshot, expected_revision: int) -> None:
    if snapshot.closed_at is not None:
        raise ApplicationClosed(snapshot.id)
    if snapshot.revision != expected_revision:
        raise StaleRevision(snapshot.id)


def _read_turn_state(
    connection, application_id: str, request_id: str, phase: str, expected_revision: int
) -> tuple[CandidateSnapshot, Turn]:
    # The phase goes first: a step retried after it succeeded has an old revision by
    # definition, and the caller must see "resume", not "stale", even after closing.
    snapshot = _read_snapshot(connection, application_id)
    turn = _require_turn(connection, application_id, request_id)
    if turn.phase != phase:
        raise WrongTurnPhase(f"{turn.request_id}: {turn.phase}")
    _check_open(snapshot, expected_revision)
    return snapshot, turn


def _advance_turn(
    connection,
    application_id: str,
    request_id: str,
    phase: str,
    now: datetime,
    reply_sequence: int | None = None,
) -> None:
    connection.execute(
        "UPDATE turns SET phase = ?, reply_sequence = ?, updated_at = ?"
        " WHERE application_id = ? AND request_id = ?",
        (phase, reply_sequence, now.isoformat(), application_id, request_id),
    )


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
