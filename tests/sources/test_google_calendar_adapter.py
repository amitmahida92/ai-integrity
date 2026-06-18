from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs

import httpx

from app.sources.exceptions import GoogleSyncTokenExpired
from app.sources.google_calendar import GoogleCalendarEventsAdapter, GoogleCalendarHttpClient
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
                        None,
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
    assert result.records_fetched == 4
    assert result.rejected_records == 2
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


def test_google_full_sync_uses_runtime_calendar_id_when_checkpoint_is_absent() -> None:
    class FakeGoogleClient:
        def __init__(self) -> None:
            self.calendar_ids: list[str] = []

        def fetch_events_page(
            self,
            *,
            calendar_id: str,
            page_token: str | None,
            sync_token: str | None,
        ) -> ProviderPage:
            self.calendar_ids.append(calendar_id)
            return ProviderPage(
                items=[event("event-1", "2026-06-17T12:00:00Z")],
                next_sync_token="sync-token",
            )

    client = FakeGoogleClient()
    adapter = GoogleCalendarEventsAdapter(
        client=client,
        default_calendar_id="calendar-from-env",
    )

    result = adapter.fetch_full()

    assert client.calendar_ids == ["calendar-from-env"]
    assert result.next_checkpoint_data == {
        "calendar_id": "calendar-from-env",
        "sync_token": "sync-token",
    }


def test_google_http_client_refreshes_access_token_before_calendar_request() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "oauth2.googleapis.com":
            form = parse_qs(request.content.decode())
            assert form == {
                "client_id": ["client-id"],
                "client_secret": ["client-secret"],
                "refresh_token": ["refresh-token"],
                "grant_type": ["refresh_token"],
            }
            return httpx.Response(200, json={"access_token": "access-token", "expires_in": 3600})

        assert request.headers["authorization"] == "Bearer access-token"
        assert request.url.path == "/calendar/v3/calendars/calendar-1/events"
        return httpx.Response(200, json={"items": [], "nextSyncToken": "sync-token"})

    http_client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://www.googleapis.com/calendar/v3",
    )
    client = GoogleCalendarHttpClient(
        client_id="client-id",
        client_secret="client-secret",
        refresh_token="refresh-token",
        http_client=http_client,
    )

    page = client.fetch_events_page(
        calendar_id="calendar-1",
        page_token=None,
        sync_token=None,
    )

    assert page.next_sync_token == "sync-token"
    assert [request.url.host for request in requests] == [
        "oauth2.googleapis.com",
        "www.googleapis.com",
    ]
