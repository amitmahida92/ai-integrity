import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.health import check_required_tables
from app.main import app


def test_health_and_ready_endpoints_report_database(migrated_database: None) -> None:
    client = TestClient(app)

    assert client.get("/health").json() == {"status": "ok", "database": "reachable"}
    assert client.get("/ready").json() == {
        "status": "ready",
        "database": "reachable",
        "tables": "ready",
    }


def test_ready_table_check_rejects_missing_required_table(db_session: Session) -> None:
    db_session.execute(text("alter table sync_source_results rename to sync_source_results_hidden"))

    with pytest.raises(HTTPException) as exc_info:
        check_required_tables(db_session)

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == {
        "status": "error",
        "database": "not_ready",
        "missing_tables": ["sync_source_results"],
    }
    db_session.rollback()
