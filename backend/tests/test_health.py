from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app

app = create_app(Settings(_env_file=None, database_path=":memory:"))


def test_live_endpoint() -> None:
    with TestClient(app) as client:
        response = client.get("/api/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_reports_safe_capabilities() -> None:
    with TestClient(app) as client:
        response = client.get("/api/health")

    payload = response.json()
    assert response.status_code == 200
    assert payload["service"] == "nova-backend"
    assert payload["status"] == "ok"
    assert payload["phase"] == 5
    assert payload["capabilities"]["phase"] == 5
    assert "ollama" in payload["llm"]
    assert "openrouter_api_key" not in response.text


def test_public_config_does_not_expose_secret() -> None:
    with TestClient(app) as client:
        response = client.get("/api/config/public")

    assert response.status_code == 200
    assert "openrouter_api_key" not in response.json()


def test_websocket_bridge_ready_and_ping() -> None:
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as websocket:
            ready = websocket.receive_json()
            assert ready["type"] == "system.ready"
            websocket.send_json({"type": "ping"})
            pong = websocket.receive_json()

    assert pong["type"] == "system.pong"
