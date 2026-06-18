from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.main import app
from app.models import NormalizedRecord
from app.repositories.checkpoints import SyncCheckpointRepository
from app.repositories.normalized_records import NormalizedRecordInput
from app.sources.exceptions import ProviderClientError
from app.sources.types import FetchResult
from app.sync.dependencies import get_sync_orchestrator
from app.sync.orchestrator import SyncOrchestrator

NOW = datetime(2026, 6, 18, 8, 30, tzinfo=UTC)


class StaticAdapterFactory:
    def __init__(self, adapters: dict[str, Any]) -> None:
        self.adapters = adapters

    def create(self, provider: str) -> Any:
        return self.adapters[provider]


class StaticAdapter:
    def __init__(
        self,
        *,
        provider: str,
        full_result: FetchResult | None = None,
        incremental_result: FetchResult | None = None,
        error: Exception | None = None,
    ) -> None:
        self.provider = provider
        self.full_result = full_result
        self.incremental_result = incremental_result
        self.error = error
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    def fetch_full(self, checkpoint_data: dict[str, Any] | None = None) -> FetchResult:
        self.calls.append(("full", checkpoint_data))
        if self.error is not None:
            raise self.error
        if self.full_result is None:
            raise AssertionError(f"{self.provider} full result was not configured")
        return self.full_result

    def fetch_incremental(self, checkpoint_data: dict[str, Any]) -> FetchResult:
        self.calls.append(("incremental", checkpoint_data))
        if self.error is not None:
            raise self.error
        if self.incremental_result is not None:
            return self.incremental_result
        if self.full_result is not None:
            return self.full_result
        raise AssertionError(f"{self.provider} incremental result was not configured")


class GoogleDemoExpiredAdapter:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def fetch_full(self, checkpoint_data: dict[str, Any] | None = None) -> FetchResult:
        return provider_result(
            "google_calendar",
            [provider_record("google_calendar", "calendar_event", "primary:event-1")],
            {"calendar_id": "primary", "sync_token": "replacement-token"},
            effective_mode="full",
        )

    def fetch_incremental(self, checkpoint_data: dict[str, Any]) -> FetchResult:
        self.calls.append(checkpoint_data)
        assert checkpoint_data["sync_token"] == "demo-expired-sync-token"
        return provider_result(
            "google_calendar",
            [provider_record("google_calendar", "calendar_event", "primary:event-1")],
            {"calendar_id": "primary", "sync_token": "replacement-token"},
            effective_mode="recovery_full",
        )


@pytest.fixture()
def client_factory(
    engine: Engine,
    db_session: Session,
) -> Callable[[dict[str, Any], bool], TestClient]:
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    clients: list[TestClient] = []

    def factory(adapters: dict[str, Any], demo_enabled: bool = False) -> TestClient:
        settings = Settings(demo_failure_injection_enabled=demo_enabled)
        orchestrator = SyncOrchestrator(
            session_factory=session_factory,
            adapter_factory=StaticAdapterFactory(adapters),
            settings=settings,
        )

        def override_get_session() -> Any:
            with session_factory() as session:
                yield session

        app.dependency_overrides[get_sync_orchestrator] = lambda: orchestrator
        app.dependency_overrides[get_session] = override_get_session
        client = TestClient(app)
        clients.append(client)
        return client

    yield factory

    app.dependency_overrides.pop(get_sync_orchestrator, None)
    app.dependency_overrides.pop(get_session, None)
    for client in clients:
        client.close()


def provider_record(
    provider: str,
    entity_type: str,
    external_id: str,
    *,
    updated_at: datetime = NOW,
    canonical_data: dict[str, Any] | None = None,
) -> NormalizedRecordInput:
    return NormalizedRecordInput(
        provider=provider,
        entity_type=entity_type,
        external_id=external_id,
        source_updated_at=updated_at,
        canonical_data=canonical_data or {"external_id": external_id},
        raw_payload={"id": external_id, "provider": provider},
    )


def provider_result(
    provider: str,
    records: list[NormalizedRecordInput],
    checkpoint: dict[str, Any],
    *,
    effective_mode: str = "full",
    rejected_records: int = 0,
) -> FetchResult:
    return FetchResult(
        provider=provider,
        effective_mode=effective_mode,  # type: ignore[arg-type]
        records=records,
        records_fetched=len(records) + rejected_records,
        rejected_records=rejected_records,
        pages_fetched=1,
        next_checkpoint_data=checkpoint,
        started_at=NOW,
    )


def success_adapters() -> dict[str, StaticAdapter]:
    return {
        "hubspot": StaticAdapter(
            provider="hubspot",
            full_result=provider_result(
                "hubspot",
                [provider_record("hubspot", "contact", "contact-1")],
                {"watermark": "2026-06-18T08:30:00Z", "overlap_seconds": 120},
            ),
        ),
        "google_calendar": StaticAdapter(
            provider="google_calendar",
            full_result=provider_result(
                "google_calendar",
                [provider_record("google_calendar", "calendar_event", "primary:event-1")],
                {"calendar_id": "primary", "sync_token": "sync-token-1"},
            ),
        ),
        "stripe": StaticAdapter(
            provider="stripe",
            full_result=provider_result(
                "stripe",
                [provider_record("stripe", "payment_intent", "pi_1")],
                {"event_watermark": "2026-06-18T08:30:00Z", "overlap_seconds": 120},
            ),
        ),
    }


def test_sync_all_providers_succeed(
    client_factory: Callable[[dict[str, Any], bool], TestClient],
    db_session: Session,
) -> None:
    client = client_factory(success_adapters())

    response = client.post("/api/v1/sync", json={"mode": "full"})

    assert response.status_code == 200
    body = response.json()
    assert body["requested_mode"] == "full"
    assert body["requested_providers"] == ["hubspot", "google_calendar", "stripe"]
    assert body["status"] == "success"
    assert [result["status"] for result in body["provider_results"]] == [
        "success",
        "success",
        "success",
    ]
    assert client.get("/api/v1/records/counts").json()["total"] == 3
    assert SyncCheckpointRepository(db_session).get("hubspot") is not None
    assert SyncCheckpointRepository(db_session).get("google_calendar") is not None
    assert SyncCheckpointRepository(db_session).get("stripe") is not None


def test_one_provider_failing_does_not_block_others(
    client_factory: Callable[[dict[str, Any], bool], TestClient],
) -> None:
    adapters = success_adapters()
    adapters["hubspot"] = StaticAdapter(
        provider="hubspot",
        error=ProviderClientError("HubSpot unavailable"),
    )
    client = client_factory(adapters)

    response = client.post("/api/v1/sync", json={"mode": "full"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "partial_success"
    results = {result["provider"]: result for result in body["provider_results"]}
    assert results["hubspot"]["status"] == "failed"
    assert results["hubspot"]["error_summary"] == "HubSpot unavailable"
    assert results["google_calendar"]["status"] == "success"
    assert results["stripe"]["status"] == "success"
    assert client.get("/api/v1/records/counts").json()["total"] == 2


def test_all_providers_failing_marks_run_failed(
    client_factory: Callable[[dict[str, Any], bool], TestClient],
) -> None:
    client = client_factory(
        {
            provider: StaticAdapter(provider=provider, error=ProviderClientError("down"))
            for provider in ("hubspot", "google_calendar", "stripe")
        }
    )

    response = client.post("/api/v1/sync", json={"mode": "incremental"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert [result["status"] for result in body["provider_results"]] == [
        "failed",
        "failed",
        "failed",
    ]


def test_admin_api_key_is_required_when_configured(
    client_factory: Callable[[dict[str, Any], bool], TestClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key")
    get_settings.cache_clear()
    try:
        client = client_factory(success_adapters())

        assert client.post("/api/v1/sync", json={"mode": "full"}).status_code == 401
        response = client.post(
            "/api/v1/sync",
            json={"mode": "full"},
            headers={"X-Admin-API-Key": "test-admin-key"},
        )

        assert response.status_code == 200
    finally:
        get_settings.cache_clear()


def test_invalid_provider_name_returns_validation_error(
    client_factory: Callable[[dict[str, Any], bool], TestClient],
) -> None:
    client = client_factory(success_adapters())

    assert (
        client.post(
            "/api/v1/sync",
            json={"mode": "full", "providers": ["not_a_provider"]},
        ).status_code
        == 422
    )
    assert client.post("/api/v1/sync/not_a_provider", json={"mode": "full"}).status_code == 422


def test_full_and_incremental_request_validation(
    client_factory: Callable[[dict[str, Any], bool], TestClient],
) -> None:
    adapters = success_adapters()
    client = client_factory(adapters)

    assert (
        client.post(
            "/api/v1/sync",
            json={"mode": "full", "providers": ["hubspot"]},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/v1/sync",
            json={"mode": "incremental", "providers": ["stripe"]},
        ).status_code
        == 200
    )
    assert client.post("/api/v1/sync", json={"mode": "invalid"}).status_code == 422
    assert client.post("/api/v1/sync", json={"mode": "full", "providers": []}).status_code == 422


def test_checkpoint_updates_after_success(
    client_factory: Callable[[dict[str, Any], bool], TestClient],
    db_session: Session,
) -> None:
    client = client_factory(success_adapters())

    response = client.post("/api/v1/sync/hubspot", json={"mode": "full"})

    assert response.status_code == 200
    checkpoint = SyncCheckpointRepository(db_session).get("hubspot")
    assert checkpoint is not None
    assert checkpoint.checkpoint_data == {
        "watermark": "2026-06-18T08:30:00Z",
        "overlap_seconds": 120,
    }


def test_checkpoint_unchanged_after_failed_persistence(
    client_factory: Callable[[dict[str, Any], bool], TestClient],
    db_session: Session,
) -> None:
    SyncCheckpointRepository(db_session).upsert(
        "hubspot",
        {"watermark": "old-watermark", "overlap_seconds": 120},
    )
    db_session.commit()
    adapters = {
        "hubspot": StaticAdapter(
            provider="hubspot",
            full_result=provider_result(
                "hubspot",
                [
                    provider_record(
                        "not_allowed",
                        "contact",
                        "contact-1",
                    )
                ],
                {"watermark": "new-watermark", "overlap_seconds": 120},
            ),
        )
    }
    client = client_factory(adapters)

    response = client.post("/api/v1/sync/hubspot", json={"mode": "full"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["provider_results"][0]["error_summary"] == "database persistence failed"
    checkpoint = SyncCheckpointRepository(db_session).get("hubspot")
    assert checkpoint is not None
    assert checkpoint.checkpoint_data == {"watermark": "old-watermark", "overlap_seconds": 120}
    assert db_session.execute(select(NormalizedRecord)).scalars().all() == []


def test_repeated_execution_does_not_create_duplicates(
    client_factory: Callable[[dict[str, Any], bool], TestClient],
    db_session: Session,
) -> None:
    client = client_factory(success_adapters())

    assert client.post("/api/v1/sync/hubspot", json={"mode": "full"}).status_code == 200
    assert client.post("/api/v1/sync/hubspot", json={"mode": "full"}).status_code == 200

    records = db_session.execute(select(NormalizedRecord)).scalars().all()
    assert len(records) == 1
    assert records[0].provider == "hubspot"
    assert records[0].external_id == "contact-1"


def test_google_expired_token_fallback_persists_replacement_checkpoint(
    client_factory: Callable[[dict[str, Any], bool], TestClient],
    db_session: Session,
) -> None:
    SyncCheckpointRepository(db_session).upsert(
        "google_calendar",
        {"calendar_id": "primary", "sync_token": "stale-token"},
    )
    db_session.commit()
    adapters = {
        "google_calendar": StaticAdapter(
            provider="google_calendar",
            incremental_result=provider_result(
                "google_calendar",
                [provider_record("google_calendar", "calendar_event", "primary:event-1")],
                {"calendar_id": "primary", "sync_token": "replacement-token"},
                effective_mode="recovery_full",
            ),
        )
    }
    client = client_factory(adapters)

    response = client.post("/api/v1/sync/google_calendar", json={"mode": "incremental"})

    assert response.status_code == 200
    result = response.json()["provider_results"][0]
    assert result["status"] == "success"
    assert result["effective_mode"] == "recovery_full"
    assert result["fallback_full_sync"] is True
    checkpoint = SyncCheckpointRepository(db_session).get("google_calendar")
    assert checkpoint is not None
    assert checkpoint.checkpoint_data == {
        "calendar_id": "primary",
        "sync_token": "replacement-token",
    }


def test_demo_failure_injection_disabled_is_rejected(
    client_factory: Callable[[dict[str, Any], bool], TestClient],
) -> None:
    client = client_factory(success_adapters(), demo_enabled=False)

    response = client.post(
        "/api/v1/sync",
        json={"mode": "full", "debug": {"fail_source": "hubspot"}},
    )

    assert response.status_code == 422
    assert "disabled" in response.json()["detail"]


def test_demo_failure_injection_enabled(
    client_factory: Callable[[dict[str, Any], bool], TestClient],
) -> None:
    adapters: dict[str, Any] = success_adapters()
    adapters["google_calendar"] = GoogleDemoExpiredAdapter()
    client = client_factory(adapters, demo_enabled=True)

    response = client.post(
        "/api/v1/sync",
        json={
            "mode": "incremental",
            "debug": {
                "fail_source": "hubspot",
                "malformed_records_provider": "stripe",
                "google_expired_sync_token": True,
            },
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "partial_success"
    results = {result["provider"]: result for result in body["provider_results"]}
    assert results["hubspot"]["status"] == "failed"
    assert results["hubspot"]["error_type"] == "DemoInjectedProviderUnavailableError"
    assert results["google_calendar"]["effective_mode"] == "recovery_full"
    assert results["google_calendar"]["fallback_full_sync"] is True
    assert results["stripe"]["status"] == "success"
    assert results["stripe"]["rejected_count"] == 1
