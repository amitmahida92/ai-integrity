from datetime import UTC, datetime
from typing import Any

from app.sources.exceptions import GoogleSyncTokenExpired
from app.sources.google_calendar import GoogleCalendarEventsAdapter
from app.sources.types import ProviderPage


def event(event_id: str, updated: str, **overrides: Any) -> dict[str, Any]:
    payload = {
        "id": event_id,
        "updated": updated,
        "summary": overrides.get("summary", "Event"),
        "description": overrides.get("description"),
        "status": overrides.get("status", "confirmed"),
        "start": overrides.get("start", {"dateTime": "2026-06-17T13:00:00Z"}),
        "end": overrides.get("end", {"dateTime": "2026-06-17T14:00:00Z"}),
    }
    payload.update(overrides.get("extra", {}))
    return payload


def test_google_incremental_uses_sync_token_paginates_and_preserves_cancellations() -> None:
    class FakeGoogleClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str | None, str | None]] = []

        def fetch_events_page(
            self,
            *,
            calendar_id: str,
            page_token: str | None,
            sync_token: str | None,
        ) -> ProviderPage:
            self.calls.append((calendar_id, page_token, sync_token))
            if page_token is None:
                return ProviderPage(
                    items=[
                        event("event-1", "2026-06-17T12:00:00Z", summary="Confirmed"),
                        {"id": "bad-event", "status": "confirmed"},
                    ],
                    next_cursor="page-2",
                )
            return ProviderPage(
                items=[
                    event(
                        "event-2",
                        "2026-06-17T12:05:00Z",
                        status="cancelled",
                        summary=None,
                        start={},
                        end={},
                    )
                ],
                next_sync_token="new-sync-token",
            )

    client = FakeGoogleClient()
    adapter = GoogleCalendarEventsAdapter(
        client=client,
        now=lambda: datetime(2026, 6, 17, 12, 10, tzinfo=UTC),
    )

    result = adapter.fetch_incremental(
        {"calendar_id": "primary", "sync_token": "old-sync-token"}
    )

    assert client.calls == [
        ("primary", None, "old-sync-token"),
        ("primary", "page-2", "old-sync-token"),
    ]
    assert result.effective_mode == "incremental"
    assert result.pages_fetched == 2
    assert result.records_fetched == 3
    assert result.rejected_records == 1
    assert [record.external_id for record in result.records] == [
        "primary:event-1",
        "primary:event-2",
    ]
    assert result.records[1].canonical_data["is_deleted"] is True
    assert result.records[1].canonical_data["status"] == "cancelled"
    assert result.next_checkpoint_data == {
        "calendar_id": "primary",
        "sync_token": "new-sync-token",
    }


def test_google_410_triggers_recovery_full_sync() -> None:
    class FakeGoogleClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str | None, str | None]] = []

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
            return ProviderPage(
                items=[event("event-1", "2026-06-17T12:00:00Z")],
                next_sync_token="replacement-token",
            )

    client = FakeGoogleClient()
    adapter = GoogleCalendarEventsAdapter(client=client)

    result = adapter.fetch_incremental(
        {"calendar_id": "primary", "sync_token": "stale-token"}
    )

    assert client.calls == [(None, "stale-token"), (None, None)]
    assert result.effective_mode == "recovery_full"
    assert len(result.records) == 1
    assert result.next_checkpoint_data == {
        "calendar_id": "primary",
        "sync_token": "replacement-token",
    }
