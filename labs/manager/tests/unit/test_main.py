from fastapi.testclient import TestClient

from app.main import app


def test_healthz_is_get_only() -> None:
    client = TestClient(app)
    assert client.post("/healthz").status_code == 405
