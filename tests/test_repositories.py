from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import NormalizedRecord, SyncCheckpoint
from app.repositories.checkpoints import SyncCheckpointRepository
from app.repositories.normalized_records import NormalizedRecordInput, NormalizedRecordRepository
from app.repositories.sync_runs import derive_run_status


def test_normalized_record_upsert_is_idempotent(db_session: Session) -> None:
    repository = NormalizedRecordRepository(db_session)
    source_updated_at = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    record = NormalizedRecordInput(
        provider="hubspot",
        entity_type="contact",
        external_id="contact-1",
        source_updated_at=source_updated_at,
        canonical_data={"email": "first@example.com"},
        raw_payload={"id": "contact-1", "email": "first@example.com"},
    )

    repository.upsert(record)
    repository.upsert(record)
    db_session.commit()

    count = db_session.scalar(select(func.count()).select_from(NormalizedRecord))
    stored = repository.get("hubspot", "contact", "contact-1")

    assert count == 1
    assert stored is not None
    assert stored.canonical_data == {"email": "first@example.com"}


def test_normalized_record_upsert_updates_newer_provider_versions(db_session: Session) -> None:
    repository = NormalizedRecordRepository(db_session)
    source_updated_at = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)

    repository.upsert(
        NormalizedRecordInput(
            provider="google_calendar",
            entity_type="calendar_event",
            external_id="event-1",
            source_updated_at=source_updated_at,
            canonical_data={"title": "Original"},
            raw_payload={"id": "event-1", "summary": "Original"},
        )
    )
    repository.upsert(
        NormalizedRecordInput(
            provider="google_calendar",
            entity_type="calendar_event",
            external_id="event-1",
            source_updated_at=source_updated_at + timedelta(minutes=5),
            canonical_data={"title": "Updated"},
            raw_payload={"id": "event-1", "summary": "Updated"},
        )
    )
    db_session.commit()

    stored = repository.get("google_calendar", "calendar_event", "event-1")

    assert repository.count() == 1
    assert stored is not None
    assert stored.source_updated_at == source_updated_at + timedelta(minutes=5)
    assert stored.canonical_data == {"title": "Updated"}


def test_older_provider_version_does_not_overwrite_newer_row(db_session: Session) -> None:
    repository = NormalizedRecordRepository(db_session)
    newer_timestamp = datetime(2026, 6, 17, 12, 5, tzinfo=UTC)

    repository.upsert(
        NormalizedRecordInput(
            provider="stripe",
            entity_type="payment_intent",
            external_id="pi_1",
            source_updated_at=newer_timestamp,
            canonical_data={"status": "succeeded"},
            raw_payload={"id": "pi_1", "status": "succeeded"},
        )
    )
    repository.upsert(
        NormalizedRecordInput(
            provider="stripe",
            entity_type="payment_intent",
            external_id="pi_1",
            source_updated_at=newer_timestamp - timedelta(minutes=5),
            canonical_data={"status": "requires_payment_method"},
            raw_payload={"id": "pi_1", "status": "requires_payment_method"},
        )
    )
    db_session.commit()

    stored = repository.get("stripe", "payment_intent", "pi_1")

    assert repository.count() == 1
    assert stored is not None
    assert stored.canonical_data == {"status": "succeeded"}
    assert stored.raw_payload == {"id": "pi_1", "status": "succeeded"}


def test_checkpoint_upsert_replaces_provider_checkpoint_data(db_session: Session) -> None:
    repository = SyncCheckpointRepository(db_session)

    repository.upsert("hubspot", {"watermark": "2026-06-17T12:00:00Z"})
    repository.upsert("hubspot", {"watermark": "2026-06-17T12:05:00Z"})
    db_session.commit()

    count = db_session.scalar(select(func.count()).select_from(SyncCheckpoint))
    stored = repository.get("hubspot")

    assert count == 1
    assert stored is not None
    assert stored.checkpoint_data == {"watermark": "2026-06-17T12:05:00Z"}


def test_sync_run_status_derivation() -> None:
    assert derive_run_status(["succeeded", "succeeded"]) == "succeeded"
    assert derive_run_status(["succeeded", "failed"]) == "completed_with_errors"
    assert derive_run_status(["failed", "failed"]) == "failed"
