from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.api.problem_2 import get_revenue_metrics_service
from app.db.session import get_session
from app.main import app
from app.models import NormalizedFinancialRecord, RevenueStatusAllowlist


@pytest.fixture()
def client(engine: Engine) -> Iterator[TestClient]:
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_get_session() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.pop(get_session, None)


def test_problem_2_seed_endpoint_is_idempotent(
    client: TestClient,
    db_session: Session,
) -> None:
    first_response = client.post("/api/v1/problem-2/seed")
    second_response = client.post("/api/v1/problem-2/seed")

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert _count(db_session, RevenueStatusAllowlist) == 3
    assert _count(db_session, NormalizedFinancialRecord) == 7


def test_summary_revenue_uses_allowlist_and_excludes_non_collected_statuses(
    client: TestClient,
) -> None:
    assert client.post("/api/v1/problem-2/seed").status_code == 200

    response = client.get(
        "/api/v1/metrics/revenue/summary",
        params={"from_date": "2026-06-01", "to_date": "2026-06-30"},
    )

    assert response.status_code == 200
    assert response.json()["totals_by_currency"] == {"USD": 15000}


def test_summary_and_breakdown_aggregate_totals_agree(client: TestClient) -> None:
    assert client.post("/api/v1/problem-2/seed").status_code == 200

    summary = client.get(
        "/api/v1/metrics/revenue/summary",
        params={"from_date": "2026-06-01", "to_date": "2026-06-30"},
    ).json()
    breakdown = client.get(
        "/api/v1/metrics/revenue/breakdown",
        params={"from_date": "2026-06-01", "to_date": "2026-06-30", "grain": "day"},
    ).json()

    assert breakdown["aggregate_totals_by_currency"] == summary["totals_by_currency"]
    assert breakdown["buckets"] == [
        {"date": "2026-06-01", "totals_by_currency": {"USD": 10000}},
        {"date": "2026-06-02", "totals_by_currency": {"USD": 5000}},
    ]


def test_unknown_status_does_not_count_until_allowlisted(
    client: TestClient,
    db_session: Session,
) -> None:
    assert client.post("/api/v1/problem-2/seed").status_code == 200
    db_session.add(
        NormalizedFinancialRecord(
            source_name="future_source",
            source_entity_type="invoice",
            external_id="future-invoice-1",
            amount_minor=32100,
            currency="USD",
            raw_status="settled_new",
            occurred_at=datetime(2026, 6, 8, tzinfo=UTC),
            customer_reference="customer-1",
            raw_payload={"id": "future-invoice-1"},
        )
    )
    db_session.commit()

    assert _summary_total(client) == {"USD": 15000}

    db_session.add(
        RevenueStatusAllowlist(
            source_name="future_source",
            source_entity_type="invoice",
            raw_status="settled_new",
            canonical_status="collected",
            counts_as_collected=True,
        )
    )
    db_session.commit()

    assert _summary_total(client) == {"USD": 47100}
    assert _breakdown_total(client) == {"USD": 47100}


def test_revenue_api_endpoints_delegate_to_revenue_metrics_service(
    client: TestClient,
) -> None:
    class SpyRevenueMetricsService:
        def __init__(self) -> None:
            self.summary_calls: list[dict[str, Any]] = []
            self.breakdown_calls: list[dict[str, Any]] = []

        def collected_revenue_summary(self, **kwargs: Any) -> dict[str, int]:
            self.summary_calls.append(kwargs)
            return {"USD": 123}

        def collected_revenue_breakdown(
            self,
            **kwargs: Any,
        ) -> tuple[list[dict[str, Any]], dict[str, int]]:
            self.breakdown_calls.append(kwargs)
            return ([{"date": "2026-06-01", "totals_by_currency": {"USD": 123}}], {"USD": 123})

    spy_service = SpyRevenueMetricsService()
    app.dependency_overrides[get_revenue_metrics_service] = lambda: spy_service

    try:
        summary = client.get(
            "/api/v1/metrics/revenue/summary",
            params={"from_date": "2026-06-01", "to_date": "2026-06-30"},
        )
        breakdown = client.get(
            "/api/v1/metrics/revenue/breakdown",
            params={"from_date": "2026-06-01", "to_date": "2026-06-30", "grain": "day"},
        )
    finally:
        app.dependency_overrides.pop(get_revenue_metrics_service, None)

    assert summary.status_code == 200
    assert breakdown.status_code == 200
    assert spy_service.summary_calls
    assert spy_service.breakdown_calls


def _count(db_session: Session, model: type[Any]) -> int:
    return db_session.execute(select(func.count()).select_from(model)).scalar_one()


def _summary_total(client: TestClient) -> dict[str, int]:
    response = client.get(
        "/api/v1/metrics/revenue/summary",
        params={"from_date": "2026-06-01", "to_date": "2026-06-30"},
    )
    assert response.status_code == 200
    return response.json()["totals_by_currency"]


def _breakdown_total(client: TestClient) -> dict[str, int]:
    response = client.get(
        "/api/v1/metrics/revenue/breakdown",
        params={"from_date": "2026-06-01", "to_date": "2026-06-30", "grain": "day"},
    )
    assert response.status_code == 200
    return response.json()["aggregate_totals_by_currency"]
