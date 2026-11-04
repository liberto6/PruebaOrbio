import json
import sqlite3

import pytest
from pydantic import ValidationError

from backend.rules import CandidateSnapshot, evaluate
from backend.storage import (
    ApplicationClosed,
    CannotClose,
    StaleRevision,
    StorageFailure,
    UnknownApplication,
    _transaction,
    close_application,
    create_application,
    init_db,
    load_application,
    save_turn,
    set_recruiter_decision,
)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "test.db"
    init_db(path)
    return path


def row_of(db, application_id):
    connection = sqlite3.connect(db)
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute(
            "SELECT * FROM applications WHERE id = ?", (application_id,)
        ).fetchone()
    finally:
        connection.close()


def test_created_application_is_incomplete_with_seven_missing_fields_after_reload(db):
    created = create_application(db)

    stored = load_application(db, created.id)

    assert stored.snapshot == created
    assert stored.messages == []
    assert stored.snapshot.outcome == "incomplete"
    assert stored.snapshot.recruiter_decision == "unreviewed"
    assert stored.snapshot.revision == 0
    data = json.loads(row_of(db, created.id)["snapshot"])
    assert len(data["fields"]) == 7
    assert all(field["status"] == "missing" for field in data["fields"].values())


def test_each_application_gets_its_own_id(db):
    assert create_application(db).id != create_application(db).id


def test_loading_an_unknown_application_is_refused(db):
    with pytest.raises(UnknownApplication):
        load_application(db, "nope")


def test_stored_snapshot_is_valid_canonical_json(db):
    created = create_application(db)

    assert CandidateSnapshot.model_validate_json(row_of(db, created.id)["snapshot"]) == created


def test_foreign_keys_are_enforced_on_our_connections(db):
    with pytest.raises(StorageFailure, match="FOREIGN KEY"):
        with _transaction(db) as connection:
            connection.execute(
                "INSERT INTO messages VALUES ('nope', 1, 'candidate', 'hola', 'now')"
            )


def test_message_sequence_is_unique_per_application(db):
    application = create_application(db)
    connection = sqlite3.connect(db)
    try:
        insert = "INSERT INTO messages VALUES (?, 1, 'candidate', 'hola', 'now')"
        connection.execute(insert, (application.id,))
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(insert, (application.id,))
    finally:
        connection.close()


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


def message_tuples(db, application_id):
    return [
        (message.sequence, message.role, message.text)
        for message in load_application(db, application_id).messages
    ]


def test_full_turn_is_recovered_after_reopening_the_file(db):
    application = create_application(db)

    result = save_turn(
        db,
        application.id,
        0,
        candidate_message="Soy Ana García y tengo carnet",
        assistant_message="¿Dónde quieres trabajar?",
        proposals=[prop("full_name", "Ana García"), unresolved("start_timing")],
        pending_questions=["¿Cuánto se paga?"],
        clarifications=["start_timing"],
    )

    stored = load_application(db, application.id)
    assert result.errors == []
    assert stored.snapshot == result.snapshot
    assert stored.snapshot.revision == 1
    assert stored.snapshot.updated_at >= application.updated_at
    assert stored.snapshot.fields.full_name.normalized == "Ana García"
    assert stored.snapshot.fields.start_timing.clarifications_asked == 1
    assert stored.snapshot.pending_questions == ["¿Cuánto se paga?"]
    assert message_tuples(db, application.id) == [
        (1, "candidate", "Soy Ana García y tengo carnet"),
        (2, "assistant", "¿Dónde quieres trabajar?"),
    ]
    data = json.loads(row_of(db, application.id)["snapshot"])
    assert len(data["fields"]) == 7
    assert data["fields"]["availability"]["normalized"] is None


def test_evaluate_works_on_a_reloaded_snapshot(db):
    application = create_application(db)
    save_turn(db, application.id, 0, proposals=ALL_RESOLVED)

    evaluation = evaluate(load_application(db, application.id).snapshot.fields)

    assert evaluation.outcome == "qualified"


def test_open_turn_never_persists_a_tentative_outcome(db):
    application = create_application(db)

    result = save_turn(db, application.id, 0, proposals=ALL_RESOLVED)

    assert result.snapshot.fields.full_name.status == "resolved"
    assert result.snapshot.outcome == "incomplete"
    assert result.snapshot.outcome_reasons == []
    assert result.snapshot.closed_at is None
    assert row_of(db, application.id)["outcome"] == "incomplete"


def test_candidate_only_and_assistant_only_turns_keep_their_order(db):
    application = create_application(db)

    save_turn(db, application.id, 0, assistant_message="Hola, ¿cómo te llamas?")
    save_turn(db, application.id, 1, candidate_message="¿Qué tiempo hace?")

    assert message_tuples(db, application.id) == [
        (1, "assistant", "Hola, ¿cómo te llamas?"),
        (2, "candidate", "¿Qué tiempo hace?"),
    ]
    assert load_application(db, application.id).snapshot.revision == 2


def test_invalid_proposal_keeps_the_message_and_the_valid_rest_of_the_turn(db):
    application = create_application(db)

    result = save_turn(
        db,
        application.id,
        0,
        candidate_message="yes, 3",
        assistant_message="¿Puedes aclararlo?",
        proposals=[prop("driving_license", "yes"), prop("full_name", "Ana García")],
        clarifications=["salary"],
    )

    stored = load_application(db, application.id)
    assert [error.field for error in result.errors] == ["driving_license", "salary"]
    assert stored.snapshot.fields.driving_license.status == "missing"
    assert stored.snapshot.fields.full_name.status == "resolved"
    assert stored.snapshot.revision == 1
    assert message_tuples(db, application.id) == [(1, "candidate", "yes, 3")]


def test_refused_clarification_is_reported_and_the_turn_still_commits(db):
    application = create_application(db)
    save_turn(db, application.id, 0, assistant_message="¿Nombre?", clarifications=["full_name"])
    save_turn(db, application.id, 1, assistant_message="¿Apellidos?", clarifications=["full_name"])

    result = save_turn(
        db,
        application.id,
        2,
        candidate_message="Ana",
        assistant_message="¿Y tu nombre?",
        clarifications=["full_name"],
    )

    stored = load_application(db, application.id)
    assert [error.error for error in result.errors] == ["clarification_limit_reached"]
    assert stored.snapshot.fields.full_name.clarifications_asked == 2
    assert stored.snapshot.revision == 3
    assert message_tuples(db, application.id) == [
        (1, "assistant", "¿Nombre?"),
        (2, "assistant", "¿Apellidos?"),
        (3, "candidate", "Ana"),
    ]


def test_one_refused_clarification_counts_nothing_but_the_rest_of_the_turn_commits(db):
    application = create_application(db)
    save_turn(db, application.id, 0, assistant_message="¿Nombre?", clarifications=["full_name"])
    save_turn(db, application.id, 1, assistant_message="¿Apellidos?", clarifications=["full_name"])

    result = save_turn(
        db,
        application.id,
        2,
        candidate_message="Ana",
        assistant_message="¿Nombre y disponibilidad?",
        proposals=[LICENSE_TRUE],
        pending_questions=["¿Cuánto se paga?"],
        clarifications=["availability", "full_name"],
    )

    stored = load_application(db, application.id).snapshot
    assert [(error.field, error.error) for error in result.errors] == [
        ("full_name", "clarification_limit_reached")
    ]
    assert stored.fields.availability.clarifications_asked == 0
    assert stored.fields.driving_license.normalized is True
    assert stored.pending_questions == ["¿Cuánto se paga?"]
    assert stored.revision == 3
    assert message_tuples(db, application.id)[-1] == (3, "candidate", "Ana")


def test_clarification_without_an_assistant_message_is_reported_and_not_counted(db):
    application = create_application(db)

    result = save_turn(
        db,
        application.id,
        0,
        candidate_message="Ana",
        proposals=[LICENSE_TRUE],
        clarifications=["full_name"],
    )

    stored = load_application(db, application.id).snapshot
    assert [(error.field, error.error) for error in result.errors] == [
        (None, "clarification_without_message")
    ]
    assert stored.fields.full_name.clarifications_asked == 0
    assert stored.fields.driving_license.normalized is True
    assert stored.revision == 1
    assert message_tuples(db, application.id) == [(1, "candidate", "Ana")]


def test_invalid_proposal_alone_does_not_drop_the_assistant_message(db):
    application = create_application(db)

    result = save_turn(
        db,
        application.id,
        0,
        assistant_message="¿Puedes aclararlo?",
        proposals=[prop("driving_license", "yes")],
    )

    assert [error.field for error in result.errors] == ["driving_license"]
    assert message_tuples(db, application.id) == [(1, "assistant", "¿Puedes aclararlo?")]


def test_stale_revision_is_rejected_without_duplicating_the_turn(db):
    application = create_application(db)
    save_turn(db, application.id, 0, candidate_message="hola")

    with pytest.raises(StaleRevision):
        save_turn(db, application.id, 0, candidate_message="hola")

    assert message_tuples(db, application.id) == [(1, "candidate", "hola")]
    assert load_application(db, application.id).snapshot.revision == 1


def test_turn_on_an_unknown_application_is_refused(db):
    with pytest.raises(UnknownApplication):
        save_turn(db, "nope", 0, candidate_message="hola")


def test_stop_request_keeps_the_application_incomplete_and_can_be_cleared(db):
    application = create_application(db)

    stopped = save_turn(db, application.id, 0, stop_requested=True)
    kept = save_turn(db, application.id, 1, candidate_message="sigo aquí")
    resumed = save_turn(db, application.id, 2, stop_requested=False)

    assert stopped.snapshot.stop_requested is True
    assert stopped.snapshot.outcome == "incomplete"
    assert kept.snapshot.stop_requested is True
    assert resumed.snapshot.stop_requested is False
    assert load_application(db, application.id).snapshot.stop_requested is False


def test_incomplete_application_resumes_from_stored_state(db):
    application = create_application(db)
    save_turn(
        db,
        application.id,
        0,
        assistant_message="¿Nombre completo?",
        proposals=[LICENSE_TRUE],
        clarifications=["full_name"],
    )

    stored = load_application(db, application.id).snapshot
    result = save_turn(
        db, stored.id, stored.revision, proposals=[prop("full_name", "Ana García")]
    )

    assert result.snapshot.fields.driving_license.normalized is True
    assert result.snapshot.fields.full_name.status == "resolved"
    assert result.snapshot.fields.full_name.clarifications_asked == 1


def fail_on_snapshot_update(db):
    """A real failure inside the transaction, after its message inserts."""
    connection = sqlite3.connect(db)
    connection.execute(
        "CREATE TRIGGER forced_failure BEFORE UPDATE ON applications"
        " BEGIN SELECT RAISE(ABORT, 'forced failure'); END"
    )
    connection.close()


def stop_failing(db):
    connection = sqlite3.connect(db)
    connection.execute("DROP TRIGGER forced_failure")
    connection.close()


def test_failed_turn_rolls_everything_back_and_can_be_retried(db):
    application = create_application(db)
    turn = {
        "candidate_message": "Ana García",
        "assistant_message": "¿Tienes carnet?",
        "proposals": [prop("full_name", "Ana García")],
    }
    fail_on_snapshot_update(db)

    with pytest.raises(StorageFailure):
        save_turn(db, application.id, 0, **turn)

    assert load_application(db, application.id).snapshot == application
    assert message_tuples(db, application.id) == []

    stop_failing(db)
    assert save_turn(db, application.id, 0, **turn).snapshot.revision == 1
    assert len(message_tuples(db, application.id)) == 2


def ready_to_close(db):
    application = create_application(db)
    save_turn(
        db,
        application.id,
        0,
        candidate_message="Empiezo cuando sea",
        proposals=ALL_RESOLVED,
        pending_questions=["¿Cuánto se paga?"],
    )
    return application.id


def assert_row_matches_json(db, application_id):
    row = row_of(db, application_id)
    data = json.loads(row["snapshot"])
    for column in ("id", "created_at", "updated_at", "closed_at", "outcome", "recruiter_decision"):
        assert row[column] == data[column]


def test_close_saves_outcome_summary_and_final_message_durably(db):
    application_id = ready_to_close(db)

    closed = close_application(db, application_id, 1, "Gracias por tu tiempo, Ana.", "Resumen")

    stored = load_application(db, application_id)
    assert stored.snapshot == closed
    assert closed.outcome == "qualified"
    assert closed.outcome_reasons == ["screening_passed"]
    assert closed.summary == "Resumen"
    assert closed.closed_at is not None
    assert closed.revision == 2
    assert closed.pending_questions == ["¿Cuánto se paga?"]
    assert closed.fields.delivery_experience.normalized.years == 1.5
    assert message_tuples(db, application_id) == [
        (1, "candidate", "Empiezo cuando sea"),
        (2, "assistant", "Gracias por tu tiempo, Ana."),
    ]


def test_early_rejection_closes_with_null_fields_and_all_keys(db):
    application = create_application(db)
    save_turn(db, application.id, 0, proposals=[LICENSE_FALSE])

    closed = close_application(db, application.id, 1, "Gracias.", "Sin carnet")

    assert closed.outcome == "disqualified"
    assert closed.outcome_reasons == ["no_driving_license"]
    data = json.loads(row_of(db, application.id)["snapshot"])
    assert len(data["fields"]) == 7
    assert data["fields"]["full_name"]["normalized"] is None


def test_filter_columns_match_the_json_after_every_operation(db):
    application_id = ready_to_close(db)
    assert_row_matches_json(db, application_id)

    set_recruiter_decision(db, application_id, "advance")
    assert_row_matches_json(db, application_id)

    close_application(db, application_id, 1, "Gracias.", "Resumen")
    row = row_of(db, application_id)
    assert_row_matches_json(db, application_id)
    assert row["outcome"] == "qualified"
    assert row["recruiter_decision"] == "advance"
    assert row["closed_at"] is not None


@pytest.mark.parametrize(
    ("final_message", "summary", "code"),
    [
        ("Gracias.", None, "summary_missing"),
        ("Gracias.", "   ", "summary_missing"),
        (None, "Resumen", "final_message_missing"),
    ],
)
def test_close_without_summary_or_final_message_is_refused_and_changes_nothing(
    db, final_message, summary, code
):
    application_id = ready_to_close(db)
    before = load_application(db, application_id)

    with pytest.raises(CannotClose, match=code):
        close_application(db, application_id, 1, final_message, summary)

    assert load_application(db, application_id) == before


def test_close_is_refused_while_fields_are_still_missing(db):
    application = create_application(db)
    save_turn(db, application.id, 0, candidate_message="hola", proposals=[LICENSE_TRUE])
    before = load_application(db, application.id)

    with pytest.raises(CannotClose, match="not_closeable"):
        close_application(db, application.id, 1, "Gracias.", "Resumen")

    assert load_application(db, application.id) == before


def test_close_is_refused_after_a_stop_request_until_it_is_cleared(db):
    application_id = ready_to_close(db)
    save_turn(db, application_id, 1, stop_requested=True)

    with pytest.raises(CannotClose, match="stop_requested"):
        close_application(db, application_id, 2, "Gracias.", "Resumen")
    assert load_application(db, application_id).snapshot.outcome == "incomplete"

    save_turn(db, application_id, 2, stop_requested=False)
    assert close_application(db, application_id, 3, "Gracias.", "Resumen").outcome == "qualified"


def test_close_with_a_stale_revision_is_refused(db):
    application_id = ready_to_close(db)
    before = load_application(db, application_id)

    with pytest.raises(StaleRevision):
        close_application(db, application_id, 0, "Gracias.", "Resumen")

    assert load_application(db, application_id) == before


def test_close_of_an_unknown_application_is_refused(db):
    with pytest.raises(UnknownApplication):
        close_application(db, "nope", 0, "Gracias.", "Resumen")


def test_failed_close_rolls_back_final_message_and_summary_and_keeps_the_candidate_turn(db):
    application_id = ready_to_close(db)
    before = load_application(db, application_id)
    fail_on_snapshot_update(db)

    with pytest.raises(StorageFailure):
        close_application(db, application_id, 1, "Gracias.", "Resumen")

    assert load_application(db, application_id) == before
    assert before.messages[-1].text == "Empiezo cuando sea"
    assert row_of(db, application_id)["outcome"] == "incomplete"

    stop_failing(db)
    assert close_application(db, application_id, 1, "Gracias.", "Resumen").outcome == "qualified"


def test_failure_at_commit_returns_no_close_and_preserves_the_last_state(db):
    application_id = ready_to_close(db)
    before = load_application(db, application_id)
    connection = sqlite3.connect(db)
    connection.executescript(
        """
        CREATE TABLE commit_parent (id INTEGER PRIMARY KEY);
        CREATE TABLE commit_child (
            parent INTEGER REFERENCES commit_parent (id) DEFERRABLE INITIALLY DEFERRED
        );
        CREATE TRIGGER fail_at_commit AFTER UPDATE ON applications
        BEGIN INSERT INTO commit_child VALUES (1); END;
        """
    )
    connection.close()

    with pytest.raises(StorageFailure, match="FOREIGN KEY"):
        close_application(db, application_id, 1, "Gracias.", "Resumen")

    assert load_application(db, application_id) == before


def test_closed_application_rejects_turns_and_repeated_close(db):
    application_id = ready_to_close(db)
    closed = close_application(db, application_id, 1, "Gracias.", "Resumen")
    before = load_application(db, application_id)

    with pytest.raises(ApplicationClosed):
        save_turn(db, application_id, closed.revision, candidate_message="otra cosa")
    with pytest.raises(ApplicationClosed):
        save_turn(db, application_id, 0, candidate_message="reintento antiguo")
    with pytest.raises(ApplicationClosed):
        close_application(db, application_id, closed.revision, "Otra vez.", "Otro")
    with pytest.raises(ApplicationClosed):
        close_application(db, application_id, 1, "Gracias.", "Resumen")

    assert load_application(db, application_id) == before


def test_recruiter_decision_is_durable_and_rewrites_nothing_else_on_a_closed_application(db):
    application_id = ready_to_close(db)
    close_application(db, application_id, 1, "Gracias.", "Resumen")
    before = load_application(db, application_id)

    result = set_recruiter_decision(db, application_id, "rejected")

    after = load_application(db, application_id)
    assert result == after.snapshot
    assert after.snapshot.recruiter_decision == "rejected"
    assert after.messages == before.messages
    assert after.snapshot.model_dump(exclude={"recruiter_decision", "updated_at"}) == (
        before.snapshot.model_dump(exclude={"recruiter_decision", "updated_at"})
    )

    set_recruiter_decision(db, application_id, "unreviewed")
    assert load_application(db, application_id).snapshot.recruiter_decision == "unreviewed"


def test_recruiter_decision_does_not_make_an_open_turn_stale(db):
    application = create_application(db)

    set_recruiter_decision(db, application.id, "advance")
    result = save_turn(db, application.id, 0, candidate_message="hola")

    assert result.snapshot.revision == 1
    assert result.snapshot.recruiter_decision == "advance"


def test_invalid_recruiter_decision_is_refused(db):
    application = create_application(db)

    with pytest.raises(ValidationError):
        set_recruiter_decision(db, application.id, "maybe")

    assert load_application(db, application.id).snapshot == application


def test_recruiter_decision_on_an_unknown_application_is_refused(db):
    with pytest.raises(UnknownApplication):
        set_recruiter_decision(db, "nope", "advance")


@pytest.mark.parametrize("entry", [3, ["x"], None])
def test_malformed_clarification_entry_follows_the_refusal_rule(db, entry):
    application = create_application(db)

    result = save_turn(
        db,
        application.id,
        0,
        candidate_message="hola",
        assistant_message="¿Nombre?",
        clarifications=["full_name", entry],
    )

    stored = load_application(db, application.id)
    assert [(error.field, error.error) for error in result.errors] == [(None, "unknown_field")]
    assert stored.snapshot.fields.full_name.clarifications_asked == 0
    assert message_tuples(db, application.id) == [(1, "candidate", "hola")]


def test_pending_questions_accumulate_across_turns(db):
    application = create_application(db)

    save_turn(db, application.id, 0, pending_questions=["¿Cuánto se paga?"])
    save_turn(db, application.id, 1, pending_questions=["¿Hay moto?"])

    stored = load_application(db, application.id).snapshot
    assert stored.pending_questions == ["¿Cuánto se paga?", "¿Hay moto?"]


def test_close_as_pending_review_saves_its_reasons(db):
    application = create_application(db)
    proposals = [*ALL_RESOLVED[:-1], unresolved("start_timing")]
    save_turn(db, application.id, 0, proposals=proposals)

    closed = close_application(db, application.id, 1, "Gracias.", "Resumen")

    assert closed.outcome == "pending_review"
    assert closed.outcome_reasons == ["start_timing_unresolved"]
    assert row_of(db, application.id)["outcome"] == "pending_review"


def test_init_db_on_an_existing_database_keeps_the_data(db):
    application = create_application(db)
    save_turn(db, application.id, 0, candidate_message="hola")
    before = load_application(db, application.id)

    init_db(db)

    assert load_application(db, application.id) == before


def test_lone_surrogate_in_a_candidate_message_does_not_lose_it(db):
    application = create_application(db)

    save_turn(db, application.id, 0, candidate_message="hola \ud83d")

    assert message_tuples(db, application.id) == [(1, "candidate", "hola ?")]


def test_lone_surrogate_in_the_final_message_does_not_prevent_closing(db):
    application_id = ready_to_close(db)

    closed = close_application(db, application_id, 1, "gracias \ud83d", "Resumen")

    assert closed.outcome == "qualified"
    assert message_tuples(db, application_id)[-1] == (2, "assistant", "gracias ?")


@pytest.mark.parametrize("bad", [None, 3, "", "   ", "hola \ud83d"])
def test_invalid_pending_question_is_reported_and_the_rest_of_the_turn_commits(db, bad):
    application = create_application(db)

    result = save_turn(
        db,
        application.id,
        0,
        candidate_message="hola",
        pending_questions=["¿Cuánto se paga?", bad],
    )

    stored = load_application(db, application.id)
    assert [(error.field, error.error) for error in result.errors] == [
        ("pending_questions", "invalid_pending_question")
    ]
    assert stored.snapshot.pending_questions == ["¿Cuánto se paga?"]
    assert stored.snapshot.revision == 1
    assert message_tuples(db, application.id) == [(1, "candidate", "hola")]


def test_bare_string_of_pending_questions_is_not_split_into_characters(db):
    application = create_application(db)

    result = save_turn(db, application.id, 0, candidate_message="hola", pending_questions="abc")

    stored = load_application(db, application.id)
    assert [(error.field, error.error) for error in result.errors] == [
        ("pending_questions", "invalid_pending_question")
    ]
    assert stored.snapshot.pending_questions == []
    assert message_tuples(db, application.id) == [(1, "candidate", "hola")]


@pytest.mark.parametrize(
    "bad_argument",
    [{"stop_requested": "false"}, {"stop_requested": 1}, {"candidate_message": 3}, {"assistant_message": b"x"}],
)
def test_wrong_argument_type_is_refused_with_nothing_committed(db, bad_argument):
    application = create_application(db)
    before = load_application(db, application.id)

    with pytest.raises(TypeError):
        save_turn(
            db,
            application.id,
            0,
            **{"candidate_message": "hola", "proposals": [LICENSE_TRUE], **bad_argument},
        )

    assert load_application(db, application.id) == before


@pytest.mark.parametrize(
    "turn",
    [
        {"pending_questions": [None]},
        {"pending_questions": "abc"},
        {"pending_questions": 3},
        {"pending_questions": ["hola \ud83d"]},
        {"stop_requested": "false"},
        {"candidate_message": 3},
        {"candidate_message": "hola \ud83d"},
        {"assistant_message": "hola \ud83d"},
        {"clarifications": [3]},
        {"clarifications": [["x"]]},
        {"clarifications": "full_name", "assistant_message": "¿Nombre?"},
        {"proposals": [prop("full_name", "Ana", raw="hola \ud83d")]},
        {"proposals": [prop("full_name", "hola \ud83d")]},
        {"proposals": [unresolved("full_name") | {"reason": "hola \ud83d"}]},
        {"proposals": [prop("availability", {"options": [], "notes": "hola \ud83d"})]},
        {"proposals": [prop("delivery_experience", {"amount": float("nan"), "unit": "years", "platforms": ["x"]})]},
        {"proposals": [None, 3, "x"]},
    ],
)
def test_no_turn_input_commits_a_row_that_cannot_be_loaded(db, turn):
    application = create_application(db)

    try:
        save_turn(db, application.id, 0, **turn)
    except (TypeError, ValidationError):
        pass

    stored = load_application(db, application.id)
    assert stored.snapshot.id == application.id
