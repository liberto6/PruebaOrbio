import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.conversation import GREETING, REPEAT_REQUEST_TEXTS
from backend.llm import (
    Interpretation,
    PermanentLLMFailure,
    TransientLLMFailure,
    UnusableOutput,
)
from backend.main import app, get_llm, resolve_db_path
from backend.rules import CatalogError, Proposal
from backend.storage import StorageFailure, load_application


class NoModel:
    """Fails the test if a request that needs no model reaches one."""

    def __getattr__(self, name):
        raise AssertionError(f"unexpected model call: {name}")


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """A temporary database and no real provider, for every test in this module."""
    monkeypatch.setenv("SCREENING_DB", str(tmp_path / "test.db"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(main, "ENV_FILE", tmp_path / "no.env")
    monkeypatch.setattr("backend.llm.ENV_FILE", tmp_path / "no.env")
    app.dependency_overrides[get_llm] = NoModel
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def create(client) -> dict:
    response = client.post("/api/applications")
    assert response.status_code == 201
    return response.json()


def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_unknown_api_path_is_json_not_frontend(client):
    response = client.get("/api/unknown")
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {"detail": "Not Found"}


# Startup and configuration


def test_startup_creates_the_parent_directory_and_tables(tmp_path, monkeypatch):
    database = tmp_path / "nested" / "dir" / "screening.db"
    monkeypatch.setenv("SCREENING_DB", str(database))

    with TestClient(app):
        pass

    with sqlite3.connect(database) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
    assert {"applications", "messages", "turns"} <= tables


def test_startup_refuses_an_empty_catalog(monkeypatch):
    monkeypatch.setattr(main, "COVERAGE_CATALOG", frozenset())
    with pytest.raises(CatalogError):
        with TestClient(app):
            pass


def test_default_database_is_under_app_independently_of_the_launch_directory(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("SCREENING_DB")
    monkeypatch.chdir(tmp_path)
    app_dir = Path(main.__file__).resolve().parent.parent
    assert resolve_db_path() == app_dir / "data" / "screening.db"


def test_relative_database_path_resolves_against_app(tmp_path, monkeypatch):
    monkeypatch.setenv("SCREENING_DB", "other/place.db")
    monkeypatch.chdir(tmp_path)
    app_dir = Path(main.__file__).resolve().parent.parent
    assert resolve_db_path() == app_dir / "other" / "place.db"


def test_env_file_supplies_the_database_without_overriding_the_environment(tmp_path, monkeypatch):
    env_file = tmp_path / "file.env"
    env_file.write_text(f"SCREENING_DB={tmp_path / 'from_file.db'}\n", encoding="utf-8")
    monkeypatch.setattr(main, "ENV_FILE", env_file)

    assert resolve_db_path() == tmp_path / "test.db"

    monkeypatch.delenv("SCREENING_DB")
    assert resolve_db_path() == tmp_path / "from_file.db"


# Creating and loading


def test_create_returns_only_the_public_state_with_one_greeting(client):
    state = create(client)

    assert set(state) == {"id", "messages", "language", "closed", "pending", "reminder_due_at"}
    assert state["language"] == "es"
    assert state["closed"] is False
    assert state["pending"] is None
    assert len(state["messages"]) == 1
    message = state["messages"][0]
    assert set(message) == {"sequence", "role", "text", "created_at"}
    assert (message["sequence"], message["role"], message["text"]) == (1, "assistant", GREETING)


def test_create_makes_a_new_application_each_time(client):
    assert create(client)["id"] != create(client)["id"]


def test_load_returns_saved_state_and_never_repeats_the_greeting(client):
    created = create(client)

    first = client.get(f"/api/applications/{created['id']}")
    second = client.get(f"/api/applications/{created['id']}")

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json() == created
    database = main.app.state.db_path
    assert len(load_application(database, created["id"]).messages) == 1


def test_load_of_an_unknown_application_has_a_stable_code_and_no_internal_text(client):
    response = client.get("/api/applications/does-not-exist")

    assert response.status_code == 404
    body = response.json()
    assert set(body) == {"code", "notice"}
    assert body["code"] == "application_not_found"
    assert "does-not-exist" not in response.text


def test_the_app_starts_creates_and_loads_without_provider_credentials(client):
    app.dependency_overrides.clear()  # The real dependency, with no OPENAI_API_KEY.
    created = create(client)
    assert client.get(f"/api/applications/{created['id']}").status_code == 200


# Sending, errors, idempotency and recovery


def resolved(field, value):
    return Proposal(field=field, status="resolved", value=value, raw="evidence", reason=None)


def understood(*proposals, language=None, stop_intent=None):
    return Interpretation(
        proposals=list(proposals), job_questions=[], language=language, stop_intent=stop_intent
    )


NAME = resolved("full_name", "Ana Ruiz")
NO_LICENSE = resolved("driving_license", False)
ALL_ANSWERS = "Soy Ana Ruiz y quiero Madrid"  # A catalog city must be in the candidate's words.
ALL_RESOLVED = [
    NAME,
    resolved("driving_license", True),
    resolved(
        "work_locations",
        {"identified": [{"country": "ES", "locality": "madrid"}], "unresolved": []},
    ),
    resolved("availability", {"options": ["full_time"], "notes": None}),
    resolved("preferred_schedule", {"options": ["morning"], "notes": None}),
    resolved("delivery_experience", {"amount": 0, "unit": "years", "platforms": []}),
    resolved("start_timing", {"kind": "unknown", "value": None}),
]
SUMMARY = "RESUMEN-INTERNO-CONFIDENCIAL"


class FakeLLM:
    """Returns the interpretation it was given, or raises what a test sets for each step."""

    def __init__(self, interpretation=None, **failures):
        self.interpretation = interpretation or understood()
        self.failures = failures
        self.interpret_calls = 0

    def _fail_if_set(self, step):
        if step in self.failures:
            raise self.failures[step]

    def interpret(self, context):
        self.interpret_calls += 1
        self._fail_if_set("interpret")
        return self.interpretation

    def write_reply(self, context, action):
        self._fail_if_set("write_reply")
        return f"respuesta {action.kind}"

    def write_summary(self, context):
        self._fail_if_set("write_summary")
        return SUMMARY


def use_model(model):
    app.dependency_overrides[get_llm] = lambda: model
    return model


def send(client, application_id, request_id="r1", text="Soy Ana Ruiz"):
    return client.post(
        f"/api/applications/{application_id}/messages",
        json={"request_id": request_id, "text": text},
    )


def saved_messages(application_id):
    return load_application(app.state.db_path, application_id).messages


def test_send_saves_the_message_and_returns_the_reply(client):
    application_id = create(client)["id"]
    use_model(FakeLLM(understood(NAME)))

    response = send(client, application_id)

    assert response.status_code == 200
    state = response.json()
    assert set(state) == {"id", "messages", "language", "closed", "pending", "reminder_due_at"}
    assert [(m["role"], m["text"]) for m in state["messages"][1:]] == [
        ("candidate", "Soy Ana Ruiz"),
        ("assistant", "respuesta ask"),
    ]
    assert state["closed"] is False
    assert state["pending"] is None


def test_a_duplicate_request_returns_the_saved_turn_without_repeating_work(client):
    application_id = create(client)["id"]
    model = use_model(FakeLLM(understood(NAME)))
    first = send(client, application_id)

    second = send(client, application_id)

    assert second.status_code == 200
    assert second.json() == first.json()
    assert model.interpret_calls == 1


def test_the_same_request_id_with_different_text_is_a_conflict(client):
    application_id = create(client)["id"]
    use_model(FakeLLM(understood(NAME)))
    send(client, application_id)

    response = send(client, application_id, text="Otro texto")

    assert response.status_code == 409
    assert response.json()["code"] == "request_conflict"
    assert "Otro texto" not in response.text
    assert len(saved_messages(application_id)) == 3


def test_a_new_message_is_refused_while_a_turn_is_pending_and_the_turn_recovers(client):
    application_id = create(client)["id"]
    use_model(FakeLLM(understood(NAME), interpret=TransientLLMFailure("provider_timeout")))
    assert send(client, application_id).status_code == 503

    pending = client.get(f"/api/applications/{application_id}").json()["pending"]
    assert pending == {"request_id": "r1", "candidate_sequence": 2}

    refused = send(client, application_id, request_id="r2", text="Otro mensaje")
    assert refused.status_code == 409
    assert refused.json()["code"] == "turn_pending"

    use_model(FakeLLM(understood(NAME)))
    recovered = send(client, application_id)
    assert recovered.status_code == 200
    assert recovered.json()["pending"] is None
    assert [m["role"] for m in recovered.json()["messages"]] == [
        "assistant",
        "candidate",
        "assistant",
    ]


@pytest.mark.parametrize(
    "failures, code",
    [
        ({"interpret": TransientLLMFailure("provider_timeout")}, "service_unavailable"),
        ({"interpret": PermanentLLMFailure("provider_quota")}, "service_configuration"),
        ({"write_reply": UnusableOutput("no_text")}, "service_unavailable"),
        ({"write_reply": PermanentLLMFailure("provider_rejected")}, "service_configuration"),
    ],
)
def test_provider_failures_map_to_a_public_code_and_keep_the_message(client, failures, code):
    application_id = create(client)["id"]
    use_model(FakeLLM(understood(NAME), **failures))

    response = send(client, application_id)

    assert response.status_code == 503
    body = response.json()
    assert set(body) == {"code", "notice"}
    assert body["code"] == code
    for internal in ("provider", "no_text", "llm_"):
        assert internal not in response.text
    assert saved_messages(application_id)[-1].text == "Soy Ana Ruiz"


def test_a_failed_summary_keeps_the_turn_and_a_retry_closes_once(client):
    application_id = create(client)["id"]
    use_model(FakeLLM(understood(*ALL_RESOLVED), write_summary=UnusableOutput("no_text")))
    assert send(client, application_id, text=ALL_ANSWERS).status_code == 503
    assert client.get(f"/api/applications/{application_id}").json()["closed"] is False

    use_model(FakeLLM())
    retried = send(client, application_id, text=ALL_ANSWERS)

    assert retried.status_code == 200
    assert retried.json()["closed"] is True
    assert len(saved_messages(application_id)) == 3


def test_notices_use_the_active_language(client):
    application_id = create(client)["id"]
    use_model(FakeLLM(understood(NAME, language="en")))
    send(client, application_id)
    use_model(FakeLLM(interpret=TransientLLMFailure("provider_timeout")))

    response = send(client, application_id, request_id="r2", text="Yes")

    assert response.json()["notice"] == "There was a technical problem. Please try again."


def test_configuration_failures_tell_the_candidate_to_come_back_later(client):
    application_id = create(client)["id"]
    use_model(FakeLLM(interpret=PermanentLLMFailure("provider_quota")))

    response = send(client, application_id)

    assert response.json()["notice"] == (
        "El servicio no está disponible ahora mismo. Puedes volver a intentarlo más tarde."
    )


def test_an_unusable_interpretation_is_a_completed_turn_not_an_http_error(client):
    application_id = create(client)["id"]
    use_model(FakeLLM(interpret=UnusableOutput("schema_mismatch")))

    response = send(client, application_id)

    assert response.status_code == 200
    assert response.json()["messages"][-1]["text"] == REPEAT_REQUEST_TEXTS["es"]
    assert response.json()["pending"] is None


def test_missing_credentials_save_the_message_and_keep_the_turn_for_a_retry(client):
    application_id = create(client)["id"]
    app.dependency_overrides.clear()  # The real dependency, with no OPENAI_API_KEY.

    response = send(client, application_id)

    assert response.status_code == 503
    assert response.json()["code"] == "service_configuration"
    assert "OPENAI" not in response.text
    state = client.get(f"/api/applications/{application_id}").json()
    assert state["pending"] == {"request_id": "r1", "candidate_sequence": 2}
    assert state["messages"][-1]["text"] == "Soy Ana Ruiz"

    use_model(FakeLLM(understood(NAME)))
    assert send(client, application_id).status_code == 200
    assert len(saved_messages(application_id)) == 3


def test_a_lost_closing_response_is_recovered_without_credentials_or_a_model_call(client):
    application_id = create(client)["id"]
    use_model(FakeLLM(understood(*ALL_RESOLVED)))
    closed = send(client, application_id, text=ALL_ANSWERS)
    assert closed.json()["closed"] is True

    app.dependency_overrides.clear()  # The real dependency, with no OPENAI_API_KEY.
    recovered = send(client, application_id, text=ALL_ANSWERS)

    assert recovered.status_code == 200
    assert recovered.json() == closed.json()


def test_a_new_message_to_a_closed_application_is_refused(client):
    application_id = create(client)["id"]
    use_model(FakeLLM(understood(NO_LICENSE)))
    assert send(client, application_id).json()["closed"] is True

    response = send(client, application_id, request_id="r2", text="Sí tengo")

    assert response.status_code == 409
    assert response.json()["code"] == "application_closed"
    assert len(saved_messages(application_id)) == 3


def test_a_message_to_an_unknown_application_is_not_found(client):
    use_model(FakeLLM())
    response = send(client, "does-not-exist")
    assert response.status_code == 404
    assert response.json()["code"] == "application_not_found"


def test_a_voluntary_stop_is_not_a_closure(client):
    application_id = create(client)["id"]
    use_model(FakeLLM(understood(stop_intent="stop")))

    state = send(client, application_id).json()

    assert state["closed"] is False
    assert state["pending"] is None
    assert state["messages"][-1]["text"] == "respuesta acknowledge_stop"


def test_responses_never_contain_the_summary_or_internal_data(client):
    application_id = create(client)["id"]
    use_model(FakeLLM(understood(*ALL_RESOLVED)))

    responses = [
        send(client, application_id, text="Quiero Madrid"),
        client.get(f"/api/applications/{application_id}"),
    ]

    assert load_application(app.state.db_path, application_id).snapshot.summary == SUMMARY
    for response in responses:
        assert set(response.json()) == {
            "id",
            "messages",
            "language",
            "closed",
            "pending",
            "reminder_due_at",
        }
        for internal in (SUMMARY, "Ana Ruiz", "recruiter", "outcome", "driving_license"):
            assert internal not in response.text


@pytest.mark.parametrize(
    "body",
    [
        {"request_id": 1, "text": "ENTRADA-CRUDA"},
        {"request_id": None, "text": "ENTRADA-CRUDA"},
        {"request_id": "   ", "text": "ENTRADA-CRUDA"},
        {"request_id": "r1", "text": ["ENTRADA-CRUDA"]},
        {"request_id": "r1", "text": 1},
        {"request_id": "r1", "text": " \n "},
        {"request_id": "r1", "text": ""},
        {"request_id": "r1"},
        {"text": "ENTRADA-CRUDA"},
        {"request_id": "r1", "text": "ENTRADA-CRUDA", "extra": "ENTRADA-CRUDA"},
        ["ENTRADA-CRUDA"],
    ],
)
def test_an_invalid_envelope_stores_nothing_and_echoes_nothing(client, body):
    application_id = create(client)["id"]

    response = client.post(f"/api/applications/{application_id}/messages", json=body)

    assert response.status_code == 422
    assert set(response.json()) == {"code", "notice"}
    assert response.json()["code"] == "invalid_request"
    assert "ENTRADA-CRUDA" not in response.text
    assert len(saved_messages(application_id)) == 1


@pytest.mark.parametrize(
    "raw",
    [
        b'{"request_id": "r1", "text": "\\ud800"}',
        b'{"request_id": "\\ud800", "text": "hola"}',
        b'{"request_id": "r1", "text": "\xff"}',
        b'{"request_id": "r1", "text": ',
        b"no es json",
    ],
)
def test_invalid_unicode_or_json_stores_nothing(client, raw):
    application_id = create(client)["id"]

    response = client.post(
        f"/api/applications/{application_id}/messages",
        content=raw,
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 422
    assert set(response.json()) == {"code", "notice"}
    assert response.json()["code"] == "invalid_request"
    assert len(saved_messages(application_id)) == 1


def test_a_body_that_is_not_json_is_an_invalid_request(client):
    application_id = create(client)["id"]
    response = client.post(
        f"/api/applications/{application_id}/messages",
        content=b"hola",
        headers={"content-type": "text/plain"},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"


def test_storage_failure_while_receiving_is_a_generic_service_error(client, monkeypatch):
    application_id = create(client)["id"]
    use_model(FakeLLM())

    def fail(*args):
        raise StorageFailure("database is locked at C:/secret/path")

    monkeypatch.setattr("backend.conversation.receive_turn", fail)
    response = send(client, application_id)

    assert response.status_code == 503
    assert response.json()["code"] == "service_unavailable"
    assert "secret" not in response.text


def test_storage_failure_while_loading_is_a_generic_service_error(client, monkeypatch):
    application_id = create(client)["id"]

    def fail(*args):
        raise StorageFailure("database is locked at C:/secret/path")

    monkeypatch.setattr(main, "load_application_with_turn", fail)
    response = client.get(f"/api/applications/{application_id}")

    assert response.status_code == 503
    assert response.json()["code"] == "service_unavailable"
    assert "secret" not in response.text


def test_an_unexpected_failure_is_generic_and_hides_its_detail(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("detalle interno")

    monkeypatch.setattr(main, "handle_message", fail)
    with TestClient(app, raise_server_exceptions=False) as client:
        application_id = create(client)["id"]
        response = send(client, application_id)

    assert response.status_code == 500
    assert set(response.json()) == {"code", "notice"}
    assert response.json()["code"] == "internal_error"
    assert "detalle interno" not in response.text


# The single reminder

REMINDER = "Si quieres, podemos continuar cuando estés listo."


@pytest.fixture
def clock(monkeypatch):
    """Controls the time the server stores and compares, starting at the real now."""
    times = {"now": datetime.now(timezone.utc)}
    monkeypatch.setattr("backend.storage._now", lambda: times["now"])

    def advance(minutes):
        times["now"] += timedelta(minutes=minutes)

    return advance


def remind(client, application_id):
    return client.post(f"/api/applications/{application_id}/reminder")


def test_public_state_schedules_the_reminder_ten_minutes_after_the_last_assistant_message(client):
    state = create(client)

    created_at = datetime.fromisoformat(state["messages"][-1]["created_at"])
    assert datetime.fromisoformat(state["reminder_due_at"]) == created_at + timedelta(minutes=10)


def test_a_due_reminder_is_saved_and_returned_without_credentials_or_a_model(client, clock):
    application_id = create(client)["id"]
    clock(10)

    response = remind(client, application_id)

    assert response.status_code == 200
    state = response.json()
    assert set(state) == {"id", "messages", "language", "closed", "pending", "reminder_due_at"}
    assert [m["text"] for m in state["messages"]][1:] == [REMINDER]
    assert state["messages"][-1]["role"] == "assistant"
    assert state["reminder_due_at"] is None


def test_a_reminder_that_is_not_due_is_a_no_op_returning_the_public_state(client, clock):
    created = create(client)
    clock(9)

    response = remind(client, created["id"])

    assert response.status_code == 200
    assert response.json() == created
    assert len(saved_messages(created["id"])) == 1


def test_a_lost_reminder_response_is_recovered_from_history_and_never_repeats(client, clock):
    application_id = create(client)["id"]
    clock(10)
    remind(client, application_id)  # The browser never reads this response.
    clock(30)

    loaded = client.get(f"/api/applications/{application_id}").json()
    again = remind(client, application_id).json()

    assert [m["text"] for m in loaded["messages"]][1:] == [REMINDER]
    assert again == loaded
    assert loaded["reminder_due_at"] is None


def test_a_reminder_for_an_unknown_application_is_not_found(client):
    response = remind(client, "nope")

    assert response.status_code == 404
    assert response.json()["code"] == "application_not_found"


def test_a_reminder_storage_failure_is_a_generic_service_error(client, clock, monkeypatch):
    application_id = create(client)["id"]
    clock(10)

    def fail(*args):
        raise StorageFailure("database is locked at C:/secret/path")

    monkeypatch.setattr(main, "send_reminder", fail)
    response = remind(client, application_id)

    assert response.status_code == 503
    assert response.json()["code"] == "service_unavailable"
    assert "secret" not in response.text


def test_no_reminder_is_scheduled_or_saved_while_stopped_or_closed(client, clock):
    stopped_id = create(client)["id"]
    use_model(FakeLLM(understood(stop_intent="stop")))
    assert send(client, stopped_id).json()["reminder_due_at"] is None
    closed_id = create(client)["id"]
    use_model(FakeLLM(understood(NO_LICENSE)))
    assert send(client, closed_id).json()["reminder_due_at"] is None
    clock(30)

    for application_id in (stopped_id, closed_id):
        state = remind(client, application_id).json()
        assert state["reminder_due_at"] is None
        assert REMINDER not in [m["text"] for m in state["messages"]]


def test_no_reminder_is_scheduled_or_saved_during_an_unfinished_turn(client, clock):
    application_id = create(client)["id"]
    use_model(FakeLLM(interpret=TransientLLMFailure("x")))
    assert send(client, application_id).status_code == 503
    clock(30)

    state = remind(client, application_id).json()

    assert state["pending"] is not None
    assert state["reminder_due_at"] is None
    assert REMINDER not in [m["text"] for m in state["messages"]]


def test_a_new_reply_moves_the_deadline(client, clock):
    created = create(client)
    clock(3)
    use_model(FakeLLM(understood(NAME)))

    state = send(client, created["id"]).json()

    assert state["reminder_due_at"] != created["reminder_due_at"]
    reply_at = datetime.fromisoformat(state["messages"][-1]["created_at"])
    assert datetime.fromisoformat(state["reminder_due_at"]) == reply_at + timedelta(minutes=10)
