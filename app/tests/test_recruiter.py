from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend import main, storage
from backend.main import app, get_llm
from backend.rules import Proposal
from backend.storage import (
    StorageFailure,
    close_application,
    create_application,
    init_db,
    load_application,
    save_turn,
    set_recruiter_decision,
)
from test_app import (  # noqa: F401 - client and isolated are fixtures used by this module.
    ALL_RESOLVED,
    NAME,
    NO_LICENSE,
    SUMMARY,
    FakeLLM,
    client,
    create,
    isolated,
    resolved,
    send,
    understood,
    use_model,
)

FIELD_ORDER = [
    "full_name",
    "driving_license",
    "work_locations",
    "availability",
    "preferred_schedule",
    "delivery_experience",
    "start_timing",
]
ITEM_KEYS = {
    "id",
    "created_at",
    "outcome",
    "recruiter_decision",
    "full_name",
    "work_locations",
    "resolved_fields",
}
DETAIL_KEYS = {
    "id",
    "created_at",
    "updated_at",
    "closed_at",
    "outcome",
    "outcome_reasons",
    "recruiter_decision",
    "fields",
    "pending_questions",
    "summary",
    "messages",
}
LIST_PATH = "/api/recruiter/applications"
UNCLEAR_START = Proposal(
    field="start_timing", status="unresolved", value=None, raw="ya veremos", reason="not_answered"
)
WITHOUT_START = [proposal for proposal in ALL_RESOLVED if proposal.field != "start_timing"]


def detail_path(application_id):
    return f"{LIST_PATH}/{application_id}"


def decision_path(application_id):
    return f"{detail_path(application_id)}/decision"


def screened(db, proposals, summary=SUMMARY, questions=()):
    """A closed application, saved directly: E3 already covers how a conversation gets here."""
    application = create_application(db, greeting="Hola")
    saved = save_turn(
        db,
        application.id,
        application.revision,
        candidate_message="Mi respuesta",
        proposals=proposals,
        pending_questions=questions,
    ).snapshot
    return close_application(db, application.id, saved.revision, "Gracias", summary).id


def open_with(db, proposals):
    application = create_application(db, greeting="Hola")
    save_turn(db, application.id, application.revision, proposals=proposals)
    return application.id


def one_per_outcome(db) -> dict:
    return {
        "qualified": screened(db, ALL_RESOLVED),
        "disqualified": screened(db, [NAME, NO_LICENSE]),
        "pending_review": screened(db, [*WITHOUT_START, UNCLEAR_START]),
        "incomplete": create_application(db, greeting="Hola").id,
    }


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "list.db"
    init_db(path)
    return path


def at(monkeypatch, moment):
    monkeypatch.setattr(storage, "_now", lambda: moment)


# The list read


def test_list_of_an_empty_database_is_empty(db):
    assert storage.list_applications(db) == []


def test_list_filters_by_each_outcome_and_omitting_it_returns_all(db):
    ids = one_per_outcome(db)

    assert {snapshot.id for snapshot in storage.list_applications(db)} == set(ids.values())
    for outcome, application_id in ids.items():
        found = storage.list_applications(db, outcome=outcome)
        assert [snapshot.id for snapshot in found] == [application_id]


def test_list_filters_by_decision_and_combines_filters_with_and(db):
    ids = one_per_outcome(db)
    set_recruiter_decision(db, ids["qualified"], "advance")
    set_recruiter_decision(db, ids["disqualified"], "advance")

    advanced = storage.list_applications(db, recruiter_decision="advance")
    assert {snapshot.id for snapshot in advanced} == {ids["qualified"], ids["disqualified"]}
    both = storage.list_applications(db, outcome="qualified", recruiter_decision="advance")
    assert [snapshot.id for snapshot in both] == [ids["qualified"]]
    assert storage.list_applications(db, outcome="incomplete", recruiter_decision="advance") == []


def test_list_treats_a_filter_value_as_data_not_sql(db):
    create_application(db)
    assert storage.list_applications(db, outcome="x' OR '1'='1") == []


def test_list_orders_newest_first_then_by_id_and_ignores_later_updates(db, monkeypatch):
    start = datetime(2026, 11, 5, 10, tzinfo=timezone.utc)
    at(monkeypatch, start)
    oldest = create_application(db).id
    at(monkeypatch, start + timedelta(hours=1))
    tied = sorted(create_application(db).id for _ in range(3))
    at(monkeypatch, start + timedelta(hours=2))
    newest = create_application(db).id

    at(monkeypatch, start + timedelta(hours=3))
    set_recruiter_decision(db, oldest, "advance")

    found = [snapshot.id for snapshot in storage.list_applications(db)]
    assert found == [newest, *tied, oldest]


def test_list_orders_by_instant_when_a_timestamp_has_no_microseconds(db, monkeypatch):
    whole_second = datetime(2026, 11, 5, 10, tzinfo=timezone.utc)
    at(monkeypatch, whole_second)
    earlier = create_application(db).id
    at(monkeypatch, whole_second + timedelta(microseconds=500000))
    later = create_application(db).id

    found = [snapshot.id for snapshot in storage.list_applications(db)]
    assert found == [later, earlier]


# List over HTTP


def test_http_list_of_an_empty_database(client):
    response = client.get(LIST_PATH)
    assert response.status_code == 200
    assert response.json() == {"items": []}


def test_http_list_filters_alone_and_combined(client):
    ids = one_per_outcome(app.state.db_path)
    set_recruiter_decision(app.state.db_path, ids["qualified"], "rejected")

    def found(**params):
        return {item["id"] for item in client.get(LIST_PATH, params=params).json()["items"]}

    assert found() == set(ids.values())
    for outcome, application_id in ids.items():
        assert found(outcome=outcome) == {application_id}
    assert found(recruiter_decision="rejected") == {ids["qualified"]}
    assert found(outcome="qualified", recruiter_decision="rejected") == {ids["qualified"]}
    assert found(outcome="disqualified", recruiter_decision="rejected") == set()


def test_greeting_only_application_is_listed_without_a_name_or_progress(client):
    application_id = create(client)["id"]

    items = client.get(LIST_PATH).json()["items"]

    assert len(items) == 1
    item = items[0]
    assert set(item) == ITEM_KEYS
    assert item["id"] == application_id
    assert item["outcome"] == "incomplete"
    assert item["recruiter_decision"] == "unreviewed"
    assert item["full_name"] is None
    assert item["resolved_fields"] == 0
    assert item["work_locations"]["status"] == "missing"


def test_item_counts_only_resolved_fields_and_keeps_the_saved_location_state(client):
    db = app.state.db_path
    name_only_evidence = Proposal(
        field="full_name", status="unresolved", value=None, raw="Ana", reason="incomplete_name"
    )
    doubtful_place = Proposal(
        field="work_locations",
        status="resolved",
        value={"identified": [], "unresolved": ["Vallecas"]},
        raw="en Vallecas",
        reason=None,
    )
    open_with(db, [name_only_evidence, doubtful_place, resolved("driving_license", True)])

    item = client.get(LIST_PATH).json()["items"][0]

    assert item["full_name"] is None  # Unresolved evidence is not a resolved name.
    assert item["resolved_fields"] == 2
    assert item["work_locations"]["status"] == "resolved"
    assert item["work_locations"]["raw"] == "en Vallecas"
    assert item["work_locations"]["normalized"]["unresolved"] == ["Vallecas"]


def test_item_of_a_complete_application_counts_seven_and_has_the_name(client):
    screened(app.state.db_path, ALL_RESOLVED)
    item = client.get(LIST_PATH).json()["items"][0]
    assert item["full_name"] == "Ana Ruiz"
    assert item["resolved_fields"] == 7


def test_http_list_keeps_the_order_of_the_storage_read(client, monkeypatch):
    db = app.state.db_path
    start = datetime(2026, 11, 5, 10, tzinfo=timezone.utc)
    at(monkeypatch, start)
    older = create_application(db).id
    at(monkeypatch, start + timedelta(hours=1))
    newer = create_application(db).id

    assert [item["id"] for item in client.get(LIST_PATH).json()["items"]] == [newer, older]


# Detail


def test_detail_of_a_closed_application_has_the_whole_saved_screening(client):
    db = app.state.db_path
    application_id = screened(db, ALL_RESOLVED, questions=["¿Cuánto se paga?"])

    response = client.get(detail_path(application_id))

    assert response.status_code == 200
    detail = response.json()
    assert set(detail) == DETAIL_KEYS
    assert detail["id"] == application_id
    assert detail["outcome"] == "qualified"
    assert detail["outcome_reasons"] == ["screening_passed"]
    assert detail["recruiter_decision"] == "unreviewed"
    assert detail["summary"] == SUMMARY
    assert detail["closed_at"] is not None
    assert detail["pending_questions"] == ["¿Cuánto se paga?"]
    assert list(detail["fields"]) == FIELD_ORDER
    for state in detail["fields"].values():
        assert set(state) == {"raw", "normalized", "status", "reason", "clarifications_asked"}
    assert detail["fields"]["start_timing"]["normalized"] == {"kind": "unknown", "value": None}
    assert detail["fields"]["delivery_experience"]["normalized"] == {"years": 0, "platforms": []}


def test_detail_returns_the_ordered_conversation(client):
    application_id = screened(app.state.db_path, ALL_RESOLVED)

    messages = client.get(detail_path(application_id)).json()["messages"]

    assert [message["sequence"] for message in messages] == [1, 2, 3]
    assert [message["role"] for message in messages] == ["assistant", "candidate", "assistant"]
    assert [message["text"] for message in messages] == ["Hola", "Mi respuesta", "Gracias"]
    for message in messages:
        assert set(message) == {"sequence", "role", "text", "created_at"}


def test_detail_of_an_incomplete_application_keeps_progress_and_a_null_summary(client):
    application_id = open_with(app.state.db_path, [NAME])

    detail = client.get(detail_path(application_id)).json()

    assert set(detail) == DETAIL_KEYS
    assert detail["outcome"] == "incomplete"
    assert detail["outcome_reasons"] == []
    assert detail["summary"] is None
    assert detail["closed_at"] is None
    assert detail["fields"]["full_name"]["normalized"] == "Ana Ruiz"
    assert detail["fields"]["start_timing"]["status"] == "missing"


def test_detail_of_a_pending_review_application_keeps_the_raw_evidence(client):
    application_id = screened(app.state.db_path, [*WITHOUT_START, UNCLEAR_START])

    detail = client.get(detail_path(application_id)).json()

    assert detail["outcome"] == "pending_review"
    assert detail["outcome_reasons"] == ["start_timing_unresolved"]
    state = detail["fields"]["start_timing"]
    assert (state["status"], state["raw"], state["reason"]) == (
        "unresolved",
        "ya veremos",
        "not_answered",
    )


def test_detail_shows_a_candidate_turn_still_awaiting_processing_without_diagnostics(client):
    application_id = create(client)["id"]
    storage.receive_turn(app.state.db_path, application_id, "r1", "Soy Ana")

    response = client.get(detail_path(application_id))

    assert response.json()["messages"][-1]["text"] == "Soy Ana"
    assert set(response.json()) == DETAIL_KEYS
    assert "request_id" not in response.text


# Decision


@pytest.mark.parametrize("decision", ["unreviewed", "advance", "rejected"])
@pytest.mark.parametrize("closed", [True, False], ids=["closed", "open"])
def test_each_decision_is_saved_on_open_and_closed_applications(client, decision, closed):
    db = app.state.db_path
    application_id = screened(db, ALL_RESOLVED) if closed else open_with(db, [NAME])
    # Start from another value so "unreviewed" is a real change too.
    set_recruiter_decision(db, application_id, "rejected" if decision != "rejected" else "advance")
    before = client.get(detail_path(application_id)).json()

    response = client.patch(decision_path(application_id), json={"recruiter_decision": decision})

    assert response.status_code == 200
    assert response.json() == {"id": application_id, "recruiter_decision": decision}
    with TestClient(app) as restarted:
        after = restarted.get(detail_path(application_id)).json()
    assert after["recruiter_decision"] == decision
    changing = {"recruiter_decision", "updated_at"}
    assert {k: v for k, v in after.items() if k not in changing} == {
        k: v for k, v in before.items() if k not in changing
    }


def test_a_decision_replaces_any_other_and_never_changes_the_revision(client):
    db = app.state.db_path
    application_id = screened(db, [NAME, NO_LICENSE])
    revision = load_application(db, application_id).snapshot.revision

    for decision in ("advance", "rejected", "unreviewed", "advance"):
        response = client.patch(
            decision_path(application_id), json={"recruiter_decision": decision}
        )
        assert response.status_code == 200

    snapshot = load_application(db, application_id).snapshot
    assert snapshot.recruiter_decision == "advance"
    assert snapshot.outcome == "disqualified"
    assert snapshot.revision == revision


def test_a_decision_stays_out_of_the_candidate_response(client):
    application_id = create(client)["id"]
    client.patch(decision_path(application_id), json={"recruiter_decision": "advance"})

    response = client.get(f"/api/applications/{application_id}")

    assert set(response.json()) == {
        "id",
        "messages",
        "language",
        "closed",
        "pending",
        "reminder_due_at",
    }
    assert "advance" not in response.text


# Refusals and failures


@pytest.mark.parametrize(
    "params",
    [
        {"outcome": "ENTRADA-CRUDA"},
        {"recruiter_decision": "ENTRADA-CRUDA"},
        {"outcome": "qualified", "recruiter_decision": "ENTRADA-CRUDA"},
        {"outcome": ""},
    ],
)
def test_an_invalid_filter_is_refused_without_echoing_it(client, params):
    response = client.get(LIST_PATH, params=params)

    assert response.status_code == 422
    assert set(response.json()) == {"code", "notice"}
    assert response.json()["code"] == "invalid_request"
    assert "ENTRADA-CRUDA" not in response.text


@pytest.mark.parametrize(
    "body",
    [
        {"recruiter_decision": "ENTRADA-CRUDA"},
        {"recruiter_decision": None},
        {"recruiter_decision": 1},
        {"recruiter_decision": ["advance"]},
        {"recruiter_decision": "advance", "extra": "ENTRADA-CRUDA"},
        {},
        ["ENTRADA-CRUDA"],
    ],
)
def test_an_invalid_decision_changes_nothing_and_echoes_nothing(client, body):
    application_id = create(client)["id"]

    response = client.patch(decision_path(application_id), json=body)

    assert response.status_code == 422
    assert set(response.json()) == {"code", "notice"}
    assert response.json()["code"] == "invalid_request"
    assert "ENTRADA-CRUDA" not in response.text
    snapshot = load_application(app.state.db_path, application_id).snapshot
    assert snapshot.recruiter_decision == "unreviewed"


@pytest.mark.parametrize("raw", [b"{not json", b'{"recruiter_decision": "\xff"}'])
def test_an_unreadable_decision_body_is_an_invalid_request(client, raw):
    application_id = create(client)["id"]

    response = client.patch(
        decision_path(application_id),
        content=raw,
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"


def test_unknown_ids_are_not_found_for_detail_and_decision(client):
    responses = [
        client.get(detail_path("no-existe")),
        client.patch(decision_path("no-existe"), json={"recruiter_decision": "advance"}),
    ]
    for response in responses:
        assert response.status_code == 404
        assert set(response.json()) == {"code", "notice"}
        assert response.json()["code"] == "application_not_found"
        assert "no-existe" not in response.text


def test_recruiter_notices_are_recruiter_copy_in_spanish_even_for_an_english_candidate(client):
    db = app.state.db_path
    application = create_application(db, greeting="Hello")
    use_model(FakeLLM(understood(language="en")))
    send(client, application.id)
    assert load_application(db, application.id).snapshot.language == "en"

    refused = client.patch(decision_path(application.id), json={"recruiter_decision": "nope"})
    unknown = client.get(detail_path("no-existe"))

    candidate_notices = {
        notice
        for code in ("invalid_request", "application_not_found")
        for notice in main.PUBLIC_ERRORS[code][1].values()
    }
    for response in (refused, unknown):
        assert response.json()["notice"] not in candidate_notices


REQUESTS = [
    ("list_applications", "get", LIST_PATH, None),
    ("load_application", "get", detail_path("any"), None),
    ("set_recruiter_decision", "patch", decision_path("any"), {"recruiter_decision": "advance"}),
]


@pytest.mark.parametrize("target, method, path, body", REQUESTS)
def test_storage_failures_are_a_generic_service_error(client, monkeypatch, target, method, path, body):
    def fail(*args, **kwargs):
        raise StorageFailure("database is locked at C:/secret/path")

    monkeypatch.setattr(main, target, fail)
    response = client.request(method, path, json=body)

    assert response.status_code == 503
    assert set(response.json()) == {"code", "notice"}
    assert response.json()["code"] == "service_unavailable"
    assert "secret" not in response.text


@pytest.mark.parametrize("target, method, path, body", REQUESTS)
def test_unexpected_failures_are_generic_and_hide_their_detail(monkeypatch, target, method, path, body):
    def fail(*args, **kwargs):
        raise RuntimeError("detalle interno")

    monkeypatch.setattr(main, target, fail)
    with TestClient(app, raise_server_exceptions=False) as client_without_raising:
        response = client_without_raising.request(method, path, json=body)

    assert response.status_code == 500
    assert set(response.json()) == {"code", "notice"}
    assert response.json()["code"] == "internal_error"
    assert "detalle interno" not in response.text


def test_recruiter_operations_work_with_the_real_dependency_and_no_credentials(client):
    app.dependency_overrides.clear()  # The real dependency, with no OPENAI_API_KEY.
    application_id = screened(app.state.db_path, ALL_RESOLVED)

    assert client.get(LIST_PATH).status_code == 200
    assert client.get(detail_path(application_id)).json()["summary"] == SUMMARY
    response = client.patch(decision_path(application_id), json={"recruiter_decision": "advance"})
    assert response.status_code == 200


def test_unknown_recruiter_paths_stay_json_404s(client):
    response = client.get("/api/recruiter/unknown")
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {"detail": "Not Found"}


# Serving the screen


def test_recruiter_path_serves_the_same_page_as_the_root_and_creates_nothing(client):
    root = client.get("/")
    recruiter = client.get("/recruiter")

    # With a build both are the HTML; without one both are the actionable JSON 503.
    assert recruiter.status_code == root.status_code
    assert recruiter.headers["content-type"] == root.headers["content-type"]
    assert recruiter.content == root.content
    assert client.get(LIST_PATH).json() == {"items": []}


# From candidate to recruiter


def test_a_completed_screening_reaches_the_recruiter_and_its_decision_lasts(client):
    application_id = create(client)["id"]
    model = use_model(FakeLLM(understood(*ALL_RESOLVED)))
    assert send(client, application_id).json()["closed"] is True
    app.dependency_overrides[get_llm] = lambda: pytest.fail("the recruiter must not use the model")

    items = client.get(LIST_PATH, params={"outcome": "qualified"}).json()["items"]
    assert [item["id"] for item in items] == [application_id]
    assert items[0]["full_name"] == "Ana Ruiz"
    assert items[0]["resolved_fields"] == 7

    detail = client.get(detail_path(application_id)).json()
    assert detail["summary"] == SUMMARY
    assert [message["role"] for message in detail["messages"]] == [
        "assistant",
        "candidate",
        "assistant",
    ]

    saved = client.patch(decision_path(application_id), json={"recruiter_decision": "advance"})
    assert saved.status_code == 200
    with TestClient(app) as restarted:
        reloaded = restarted.get(detail_path(application_id)).json()
        filtered = restarted.get(LIST_PATH, params={"recruiter_decision": "advance"}).json()
    assert reloaded["recruiter_decision"] == "advance"
    assert reloaded["summary"] == SUMMARY
    assert [item["id"] for item in filtered["items"]] == [application_id]
    assert model.interpret_calls == 1


def test_a_candidate_turn_keeps_a_decision_saved_during_the_conversation(client):
    application_id = create(client)["id"]
    client.patch(decision_path(application_id), json={"recruiter_decision": "rejected"})

    use_model(FakeLLM(understood(NAME)))
    assert send(client, application_id).status_code == 200

    detail = client.get(detail_path(application_id)).json()
    assert detail["recruiter_decision"] == "rejected"
    assert detail["fields"]["full_name"]["normalized"] == "Ana Ruiz"
