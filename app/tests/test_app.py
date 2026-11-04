from fastapi.testclient import TestClient

from backend.main import app


def test_health():
    with TestClient(app) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_unknown_api_path_is_json_not_frontend():
    with TestClient(app) as client:
        response = client.get("/api/unknown")
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {"detail": "Not Found"}
