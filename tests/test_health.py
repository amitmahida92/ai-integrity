from fastapi.testclient import TestClient

from app.main import app


def test_health_and_ready_endpoints_report_database(migrated_database: None) -> None:
    client = TestClient(app)

    assert client.get("/health").json() == {"status": "ok", "database": "reachable"}
    assert client.get("/healthz").json() == {"status": "ok", "database": "reachable"}
    assert client.get("/ready").json() == {"status": "ready", "database": "reachable"}
