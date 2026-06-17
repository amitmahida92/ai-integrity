from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, selectinload, sessionmaker

from app.core.config import Settings
from app.db.session import get_session
from app.main import app
from app.models import NormalizedRecord, SyncRun
from app.repositories.checkpoints import SyncCheckpointRepository
from app.repositories.normalized_records import NormalizedRecordInput
from app.sources.exceptions import GoogleSyncTokenExpired, ProviderResponseError
from app.sources.google_calendar import GoogleCalendarEventsAdapter
from app.sources.hubspot import HubSpotContactsAdapter
from app.sources.stripe import StripePaymentIntentsAdapter
from app.sources.types import FetchResult, ProviderPage
from app.sync.dependencies import get_sync_orchestrator
from app.sync.orchestrator import SyncOrchestrator

NOW = datetime(2026, 6, 18, 8, 30, tzinfo=UTC)


class AcceptanceAdapterFactory:
    def __init__(self, adapters: dict[str, Any]) -> None:
        self.adapters = adapters

    def create(self, provider: str) -> Any:
        return self.adapters[provider]


@pytest.fixture()
def client_factory(engine: Engine) -> Callable[[dict[str, Any]], TestClient]:
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    clients: list[TestClient] = []

    def factory(adapters: dict[str, Any]) -> TestClient:
        orchestrator = SyncOrchestrator(
            session_factory=session_factory,
            adapter_factory=AcceptanceAdapterFactory(adapters),
            settings=Settings(),
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


class SequencedHubSpotClient:
    def __init__(self, full_runs: list[list[dict[str, Any]]]) -> None:
        self.full_runs = full_runs
        self.full_calls = 0

    def fetch_contacts_page(self, *, after: str | None, limit: int) -> ProviderPage:
        assert after is None
        assert limit == 100
        run_index = min(self.full_calls, len(self.full_runs) - 1)
        self.full_calls += 1
        return ProviderPage(items=self.full_runs[run_index])

    def search_contacts_modified_since_page(
        self,
        *,
        modified_since: datetime,
        after: str | None,
        limit: int,
    ) -> ProviderPage:
        raise AssertionError("HubSpot incremental fetch was not expected in this test")


class FailingHubSpotClient:
    def fetch_contacts_page(self, *, after: str | None, limit: int) -> ProviderPage:
        raise ProviderResponseError("HubSpot unavailable")

    def search_contacts_modified_since_page(
        self,
        *,
        modified_since: datetime,
        after: str | None,
        limit: int,
    ) -> ProviderPage:
        raise ProviderResponseError("HubSpot unavailable")


class StaticGoogleClient:
    def __init__(
        self,
        events: list[dict[str, Any]],
        *,
        next_sync_token: str = "google-sync-token",
    ) -> None:
        self.events = events
        self.next_sync_token = next_sync_token
        self.calls: list[tuple[str | None, str | None]] = []

    def fetch_events_page(
        self,
        *,
        calendar_id: str,
        page_token: str | None,
        sync_token: str | None,
    ) -> ProviderPage:
        assert calendar_id == "primary"
        self.calls.append((page_token, sync_token))
        return ProviderPage(items=self.events, next_sync_token=self.next_sync_token)


class ExpiredThenRecoveryGoogleClient(StaticGoogleClient):
    def fetch_events_page(
        self,
        *,
        calendar_id: str,
        page_token: str | None,
        sync_token: str | None,
    ) -> ProviderPage:
        self.calls.append((page_token, sync_token))
        if sync_token == "stale-token":
            raise GoogleSyncTokenExpired("expired")
        return ProviderPage(items=self.events, next_sync_token=self.next_sync_token)


class StaticStripeClient:
    def __init__(
        self,
        *,
        payment_intents: dict[str, dict[str, Any]],
        full_payment_intents: list[dict[str, Any]] | None = None,
        events: list[dict[str, Any]] | None = None,
    ) -> None:
        self.payment_intents = payment_intents
        self.full_payment_intents = full_payment_intents or list(payment_intents.values())
        self.events = events or []
        self.event_calls: list[datetime] = []

    def fetch_payment_intents_page(
        self,
        *,
        starting_after: str | None,
        limit: int,
    ) -> ProviderPage:
        assert starting_after is None
        assert limit == 100
        return ProviderPage(items=self.full_payment_intents, has_more=False)

    def fetch_events_page(
        self,
        *,
        created_gte: datetime,
        starting_after: str | None,
        limit: int,
    ) -> ProviderPage:
        assert starting_after is None
        assert limit == 100
        self.event_calls.append(created_gte)
        return ProviderPage(items=self.events, has_more=False)

    def retrieve_payment_intent(self, payment_intent_id: str) -> dict[str, Any]:
        return self.payment_intents[payment_intent_id]


@dataclass
class StaticResultAdapter:
    result: FetchResult

    def fetch_full(self, checkpoint_data: dict[str, Any] | None = None) -> FetchResult:
        return self.result

    def fetch_incremental(self, checkpoint_data: dict[str, Any]) -> FetchResult:
        return self.result


def hubspot_adapter(client: Any) -> HubSpotContactsAdapter:
    return HubSpotContactsAdapter(client=client, now=lambda: NOW)


def google_adapter(client: Any) -> GoogleCalendarEventsAdapter:
    return GoogleCalendarEventsAdapter(client=client, now=lambda: NOW)


def stripe_adapter(client: Any) -> StripePaymentIntentsAdapter:
    return StripePaymentIntentsAdapter(client=client, now=lambda: NOW)


def hubspot_contact(
    contact_id: str,
    *,
    email: str,
    updated: str,
    first_name: str = "Ada",
) -> dict[str, Any]:
    return {
        "id": contact_id,
        "properties": {
            "email": email,
            "firstname": first_name,
            "lastname": "Lovelace",
            "phone": "+15551234567",
            "lastmodifieddate": updated,
        },
        "updatedAt": updated,
    }


def google_event(
    event_id: str,
    *,
    updated: str = "2026-06-18T08:20:00Z",
    summary: str = "Planning",
) -> dict[str, Any]:
    return {
        "id": event_id,
        "updated": updated,
        "summary": summary,
        "description": "Demo event",
        "status": "confirmed",
        "start": {"dateTime": "2026-06-18T09:00:00Z"},
        "end": {"dateTime": "2026-06-18T10:00:00Z"},
    }


def stripe_payment_intent(
    payment_intent_id: str,
    *,
    status: str = "succeeded",
    created: str = "2026-06-18T08:00:00Z",
) -> dict[str, Any]:
    return {
        "id": payment_intent_id,
        "amount": 1250,
        "amount_received": 1250,
        "currency": "usd",
        "status": status,
        "customer": "cus_123",
        "created": created,
    }


def stripe_event(
    event_id: str,
    payment_intent_id: str,
    *,
    created: str = "2026-06-18T08:25:00Z",
    event_type: str = "payment_intent.succeeded",
) -> dict[str, Any]:
    return {
        "id": event_id,
        "type": event_type,
        "created": created,
        "data": {"object": {"id": payment_intent_id}},
    }


def provider_result(
    provider: str,
    records: list[NormalizedRecordInput],
    checkpoint: dict[str, Any],
    *,
    effective_mode: str = "full",
) -> FetchResult:
    return FetchResult(
        provider=provider,
        effective_mode=effective_mode,  # type: ignore[arg-type]
        records=records,
        records_fetched=len(records),
        rejected_records=0,
        pages_fetched=1,
        next_checkpoint_data=checkpoint,
        started_at=NOW,
    )


def valid_all_provider_adapters() -> dict[str, Any]:
    stripe_pi = stripe_payment_intent("pi_1")
    return {
        "hubspot": hubspot_adapter(
            SequencedHubSpotClient(
                [
                    [
                        hubspot_contact(
                            "contact-1",
                            email="ada@example.com",
                            updated="2026-06-18T08:10:00Z",
                        )
                    ]
                ]
            )
        ),
        "google_calendar": google_adapter(
            StaticGoogleClient(
                [google_event("event-1", summary="Planning")],
                next_sync_token="google-sync-token",
            )
        ),
        "stripe": stripe_adapter(
            StaticStripeClient(payment_intents={"pi_1": stripe_pi})
        ),
    }


def load_run(db_session: Session, run_id: str) -> SyncRun:
    db_session.expire_all()
    run = db_session.execute(
        select(SyncRun)
        .options(selectinload(SyncRun.source_results))
        .where(SyncRun.id == UUID(run_id))
    ).scalar_one()
    return run


def source_results(run: SyncRun) -> dict[str, Any]:
    return {result.provider: result for result in run.source_results}


def records_by_key(db_session: Session) -> dict[tuple[str, str, str], NormalizedRecord]:
    db_session.expire_all()
    records = db_session.execute(select(NormalizedRecord)).scalars().all()
    return {
        (record.provider, record.entity_type, record.external_id): record
        for record in records
    }


def normalized_count(db_session: Session) -> int:
    db_session.expire_all()
    return db_session.scalar(select(func.count()).select_from(NormalizedRecord)) or 0


def checkpoint_data(db_session: Session, provider: str) -> dict[str, Any] | None:
    db_session.expire_all()
    checkpoint = SyncCheckpointRepository(db_session).get(provider)
    return checkpoint.checkpoint_data if checkpoint is not None else None


def test_repeating_full_sync_does_not_increase_normalized_row_count(
    client_factory: Callable[[dict[str, Any]], TestClient],
    db_session: Session,
) -> None:
    contact = hubspot_contact(
        "contact-1",
        email="ada@example.com",
        updated="2026-06-18T08:10:00Z",
    )
    client = client_factory(
        {"hubspot": hubspot_adapter(SequencedHubSpotClient([[contact], [contact]]))}
    )

    first_response = client.post("/api/v1/sync/hubspot", json={"mode": "full"})
    assert first_response.status_code == 200
    assert normalized_count(db_session) == 1

    second_response = client.post("/api/v1/sync/hubspot", json={"mode": "full"})
    assert second_response.status_code == 200

    assert normalized_count(db_session) == 1
    records = records_by_key(db_session)
    stored = records[("hubspot", "contact", "contact-1")]
    assert stored.canonical_data["email"] == "ada@example.com"
    assert stored.raw_payload["id"] == "contact-1"

    first_run = load_run(db_session, first_response.json()["id"])
    second_run = load_run(db_session, second_response.json()["id"])
    first_source = source_results(first_run)["hubspot"]
    second_source = source_results(second_run)["hubspot"]
    expected_checkpoint = {
        "watermark": "2026-06-18T08:10:00Z",
        "overlap_seconds": 120,
    }

    assert first_run.status == "succeeded"
    assert second_run.status == "succeeded"
    assert first_source.status == "succeeded"
    assert first_source.records_fetched == 1
    assert first_source.records_upserted == 1
    assert first_source.records_rejected == 0
    assert first_source.checkpoint_before == {}
    assert first_source.checkpoint_after == expected_checkpoint
    assert second_source.status == "succeeded"
    assert second_source.records_fetched == 1
    assert second_source.records_upserted == 1
    assert second_source.records_rejected == 0
    assert second_source.checkpoint_before == expected_checkpoint
    assert second_source.checkpoint_after == expected_checkpoint


def test_repeating_incremental_sync_does_not_create_duplicate_rows(
    client_factory: Callable[[dict[str, Any]], TestClient],
    db_session: Session,
) -> None:
    SyncCheckpointRepository(db_session).upsert(
        "stripe",
        {
            "event_watermark": "2026-06-18T08:00:00Z",
            "overlap_seconds": 120,
            "last_event_id": "evt_old",
        },
    )
    db_session.commit()

    stripe_pi = stripe_payment_intent("pi_1", status="succeeded")
    stripe_client = StaticStripeClient(
        payment_intents={"pi_1": stripe_pi},
        events=[stripe_event("evt_1", "pi_1")],
    )
    client = client_factory({"stripe": stripe_adapter(stripe_client)})

    first_response = client.post("/api/v1/sync/stripe", json={"mode": "incremental"})
    second_response = client.post("/api/v1/sync/stripe", json={"mode": "incremental"})

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert normalized_count(db_session) == 1
    stored = records_by_key(db_session)[("stripe", "payment_intent", "pi_1")]
    assert stored.canonical_data["status"] == "succeeded"
    assert stored.raw_payload["source_event"]["id"] == "evt_1"

    first_source = source_results(load_run(db_session, first_response.json()["id"]))["stripe"]
    second_source = source_results(load_run(db_session, second_response.json()["id"]))["stripe"]
    expected_after = {
        "event_watermark": "2026-06-18T08:30:00Z",
        "overlap_seconds": 120,
        "last_event_id": "evt_1",
    }

    assert first_source.requested_mode == "incremental"
    assert first_source.effective_mode == "incremental"
    assert first_source.records_fetched == 1
    assert first_source.records_upserted == 1
    assert first_source.checkpoint_after == expected_after
    assert second_source.requested_mode == "incremental"
    assert second_source.effective_mode == "incremental"
    assert second_source.records_fetched == 1
    assert second_source.records_upserted == 1
    assert second_source.checkpoint_before == expected_after
    assert second_source.checkpoint_after == expected_after


def test_changed_source_object_updates_existing_normalized_row(
    client_factory: Callable[[dict[str, Any]], TestClient],
    db_session: Session,
) -> None:
    hubspot_client = SequencedHubSpotClient(
        [
            [
                hubspot_contact(
                    "contact-1",
                    email="old@example.com",
                    updated="2026-06-18T08:10:00Z",
                )
            ],
            [
                hubspot_contact(
                    "contact-1",
                    email="new@example.com",
                    updated="2026-06-18T08:25:00Z",
                    first_name="Grace",
                )
            ],
            [
                hubspot_contact(
                    "contact-1",
                    email="stale@example.com",
                    updated="2026-06-18T08:15:00Z",
                )
            ],
        ]
    )
    client = client_factory({"hubspot": hubspot_adapter(hubspot_client)})

    first_response = client.post("/api/v1/sync/hubspot", json={"mode": "full"})
    initial_record = records_by_key(db_session)[("hubspot", "contact", "contact-1")]
    second_response = client.post("/api/v1/sync/hubspot", json={"mode": "full"})
    updated_record = records_by_key(db_session)[("hubspot", "contact", "contact-1")]
    third_response = client.post("/api/v1/sync/hubspot", json={"mode": "full"})
    final_record = records_by_key(db_session)[("hubspot", "contact", "contact-1")]

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert third_response.status_code == 200
    assert normalized_count(db_session) == 1
    assert updated_record.id == initial_record.id
    assert updated_record.canonical_data["email"] == "new@example.com"
    assert updated_record.canonical_data["first_name"] == "Grace"
    assert updated_record.raw_payload["properties"]["email"] == "new@example.com"
    assert updated_record.source_updated_at == datetime(2026, 6, 18, 8, 25, tzinfo=UTC)

    assert final_record.id == initial_record.id
    assert final_record.canonical_data["email"] == "new@example.com"
    assert final_record.source_updated_at == datetime(2026, 6, 18, 8, 25, tzinfo=UTC)

    for response in (first_response, second_response, third_response):
        run = load_run(db_session, response.json()["id"])
        source = source_results(run)["hubspot"]
        assert run.status == "succeeded"
        assert source.status == "succeeded"
        assert source.records_upserted == 1


def test_google_expired_token_fallback_persists_replacement_after_records(
    client_factory: Callable[[dict[str, Any]], TestClient],
    db_session: Session,
) -> None:
    SyncCheckpointRepository(db_session).upsert(
        "google_calendar",
        {"calendar_id": "primary", "sync_token": "stale-token"},
    )
    db_session.commit()
    google_client = ExpiredThenRecoveryGoogleClient(
        [google_event("event-1", summary="Recovered event")],
        next_sync_token="replacement-token",
    )
    client = client_factory({"google_calendar": google_adapter(google_client)})

    response = client.post("/api/v1/sync/google_calendar", json={"mode": "incremental"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["provider_results"][0]["effective_mode"] == "recovery_full"
    assert body["provider_results"][0]["fallback_full_sync"] is True
    assert google_client.calls == [(None, "stale-token"), (None, None)]

    records = records_by_key(db_session)
    stored = records[("google_calendar", "calendar_event", "primary:event-1")]
    assert stored.canonical_data["title"] == "Recovered event"
    assert stored.raw_payload["id"] == "event-1"
    assert checkpoint_data(db_session, "google_calendar") == {
        "calendar_id": "primary",
        "sync_token": "replacement-token",
    }

    run = load_run(db_session, body["id"])
    source = source_results(run)["google_calendar"]
    assert run.status == "succeeded"
    assert source.requested_mode == "incremental"
    assert source.effective_mode == "recovery_full"
    assert source.status == "succeeded"
    assert source.records_fetched == 1
    assert source.records_upserted == 1
    assert source.records_rejected == 0
    assert source.checkpoint_before == {
        "calendar_id": "primary",
        "sync_token": "stale-token",
    }
    assert source.checkpoint_after == {
        "calendar_id": "primary",
        "sync_token": "replacement-token",
    }


def test_persistence_failure_leaves_provider_checkpoint_unchanged(
    client_factory: Callable[[dict[str, Any]], TestClient],
    db_session: Session,
) -> None:
    old_checkpoint = {"watermark": "old-watermark", "overlap_seconds": 120}
    SyncCheckpointRepository(db_session).upsert("hubspot", old_checkpoint)
    db_session.commit()
    invalid_record = NormalizedRecordInput(
        provider="not_allowed",
        entity_type="contact",
        external_id="contact-1",
        source_updated_at=NOW,
        canonical_data={"email": "bad@example.com"},
        raw_payload={"id": "contact-1"},
    )
    client = client_factory(
        {
            "hubspot": StaticResultAdapter(
                provider_result(
                    "hubspot",
                    [invalid_record],
                    {"watermark": "new-watermark", "overlap_seconds": 120},
                )
            )
        }
    )

    response = client.post("/api/v1/sync/hubspot", json={"mode": "full"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["provider_results"][0]["status"] == "failed"
    assert body["provider_results"][0]["error_summary"] == "database persistence failed"
    assert normalized_count(db_session) == 0
    assert checkpoint_data(db_session, "hubspot") == old_checkpoint

    run = load_run(db_session, body["id"])
    source = source_results(run)["hubspot"]
    assert run.status == "failed"
    assert source.status == "failed"
    assert source.records_fetched == 1
    assert source.records_upserted == 0
    assert source.records_rejected == 0
    assert source.checkpoint_before == old_checkpoint
    assert source.checkpoint_after is None


def test_one_provider_failure_still_persists_other_two_and_marks_partial_success(
    client_factory: Callable[[dict[str, Any]], TestClient],
    db_session: Session,
) -> None:
    adapters = valid_all_provider_adapters()
    adapters["hubspot"] = hubspot_adapter(FailingHubSpotClient())
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

    records = records_by_key(db_session)
    assert set(records) == {
        ("google_calendar", "calendar_event", "primary:event-1"),
        ("stripe", "payment_intent", "pi_1"),
    }
    assert records[
        ("google_calendar", "calendar_event", "primary:event-1")
    ].canonical_data["title"] == "Planning"
    assert records[("stripe", "payment_intent", "pi_1")].canonical_data["status"] == "succeeded"
    assert checkpoint_data(db_session, "hubspot") is None
    assert checkpoint_data(db_session, "google_calendar") == {
        "calendar_id": "primary",
        "sync_token": "google-sync-token",
    }
    assert checkpoint_data(db_session, "stripe") == {
        "event_watermark": "2026-06-18T08:30:00Z",
        "overlap_seconds": 120,
    }

    run = load_run(db_session, body["id"])
    sources = source_results(run)
    assert run.status == "completed_with_errors"
    assert sources["hubspot"].status == "failed"
    assert sources["hubspot"].records_upserted == 0
    assert sources["hubspot"].checkpoint_after is None
    assert sources["google_calendar"].status == "succeeded"
    assert sources["google_calendar"].records_upserted == 1
    assert sources["stripe"].status == "succeeded"
    assert sources["stripe"].records_upserted == 1


def test_malformed_record_rejected_while_valid_records_continue(
    client_factory: Callable[[dict[str, Any]], TestClient],
    db_session: Session,
) -> None:
    adapters = valid_all_provider_adapters()
    adapters["hubspot"] = hubspot_adapter(
        SequencedHubSpotClient(
            [
                [
                    hubspot_contact(
                        "contact-1",
                        email="valid@example.com",
                        updated="2026-06-18T08:10:00Z",
                    ),
                    {"id": "broken-contact"},
                ]
            ]
        )
    )
    client = client_factory(adapters)

    response = client.post("/api/v1/sync", json={"mode": "full"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    results = {result["provider"]: result for result in body["provider_results"]}
    assert results["hubspot"]["status"] == "success"
    assert results["hubspot"]["fetched_count"] == 2
    assert results["hubspot"]["upserted_count"] == 1
    assert results["hubspot"]["rejected_count"] == 1
    assert results["google_calendar"]["status"] == "success"
    assert results["stripe"]["status"] == "success"

    records = records_by_key(db_session)
    assert set(records) == {
        ("hubspot", "contact", "contact-1"),
        ("google_calendar", "calendar_event", "primary:event-1"),
        ("stripe", "payment_intent", "pi_1"),
    }
    assert records[("hubspot", "contact", "contact-1")].canonical_data["email"] == (
        "valid@example.com"
    )
    assert records[
        ("google_calendar", "calendar_event", "primary:event-1")
    ].canonical_data["title"] == "Planning"
    assert records[("stripe", "payment_intent", "pi_1")].canonical_data["status"] == "succeeded"

    run = load_run(db_session, body["id"])
    sources = source_results(run)
    assert run.status == "succeeded"
    assert sources["hubspot"].records_fetched == 2
    assert sources["hubspot"].records_upserted == 1
    assert sources["hubspot"].records_rejected == 1
    assert sources["hubspot"].status == "succeeded"
    assert sources["google_calendar"].records_upserted == 1
    assert sources["stripe"].records_upserted == 1
