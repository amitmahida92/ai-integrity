from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from app.repositories.normalized_records import NormalizedRecordInput
from app.sources.exceptions import (
    GoogleSyncTokenExpired,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderTransientError,
    RecordRejectedError,
)
from app.sources.retry import RetryPolicy, call_with_retry
from app.sources.time import format_provider_datetime, parse_provider_datetime, utc_now
from app.sources.types import FetchResult, ProviderPage

GOOGLE_PROVIDER = "google_calendar"
EVENT_ENTITY_TYPE = "calendar_event"
DEFAULT_CALENDAR_ID = "primary"


class GoogleCalendarEventsClient(Protocol):
    def fetch_events_page(
        self,
        *,
        calendar_id: str,
        page_token: str | None,
        sync_token: str | None,
    ) -> ProviderPage:
        ...


@dataclass(frozen=True)
class GoogleCalendarEventsAdapter:
    client: GoogleCalendarEventsClient
    default_calendar_id: str = DEFAULT_CALENDAR_ID
    retry_policy: RetryPolicy = RetryPolicy()
    now: Callable[[], datetime] = utc_now
    sleep: Callable[[float], None] = lambda _: None

    def fetch_full(
        self,
        checkpoint_data: dict[str, Any] | None = None,
        *,
        effective_mode: str = "full",
    ) -> FetchResult:
        started_at = self.now()
        calendar_id = str((checkpoint_data or {}).get("calendar_id") or self.default_calendar_id)
        return self._fetch_pages(
            calendar_id=calendar_id,
            sync_token=None,
            started_at=started_at,
            effective_mode=effective_mode,
        )

    def fetch_incremental(self, checkpoint_data: dict[str, Any]) -> FetchResult:
        sync_token = checkpoint_data.get("sync_token")
        if not sync_token:
            return self.fetch_full(checkpoint_data)

        calendar_id = str(checkpoint_data.get("calendar_id") or self.default_calendar_id)
        started_at = self.now()
        try:
            return self._fetch_pages(
                calendar_id=calendar_id,
                sync_token=str(sync_token),
                started_at=started_at,
                effective_mode="incremental",
            )
        except GoogleSyncTokenExpired:
            return self.fetch_full(checkpoint_data, effective_mode="recovery_full")

    def _fetch_pages(
        self,
        *,
        calendar_id: str,
        sync_token: str | None,
        started_at: datetime,
        effective_mode: str,
    ) -> FetchResult:
        page_token: str | None = None
        records: list[NormalizedRecordInput] = []
        records_fetched = 0
        rejected_records = 0
        pages_fetched = 0
        next_sync_token: str | None = None

        while True:
            page = call_with_retry(
                lambda page_token=page_token: self.client.fetch_events_page(
                    calendar_id=calendar_id,
                    page_token=page_token,
                    sync_token=sync_token,
                ),
                retry_policy=self.retry_policy,
                sleep=self.sleep,
            )
            pages_fetched += 1
            records_fetched += len(page.items)

            for raw_event in page.items:
                if not isinstance(raw_event, dict):
                    rejected_records += 1
                    continue
                try:
                    records.append(normalize_google_event(raw_event, calendar_id=calendar_id))
                except RecordRejectedError:
                    rejected_records += 1

            next_sync_token = page.next_sync_token or next_sync_token
            if not page.next_cursor:
                break
            page_token = page.next_cursor

        if not next_sync_token:
            raise ProviderResponseError("Google Calendar response did not include nextSyncToken")

        return FetchResult(
            provider=GOOGLE_PROVIDER,
            effective_mode=effective_mode,  # type: ignore[arg-type]
            records=records,
            records_fetched=records_fetched,
            rejected_records=rejected_records,
            pages_fetched=pages_fetched,
            next_checkpoint_data={
                "calendar_id": calendar_id,
                "sync_token": next_sync_token,
            },
            started_at=started_at,
            metadata={"sync_token_used": sync_token},
        )


def normalize_google_event(raw_event: dict[str, Any], *, calendar_id: str) -> NormalizedRecordInput:
    event_id = raw_event.get("id")
    if not event_id:
        raise RecordRejectedError("Google event must include id")

    status = raw_event.get("status") or "confirmed"
    is_deleted = status == "cancelled"
    try:
        provider_updated_at = parse_provider_datetime(raw_event.get("updated"))
    except ValueError as exc:
        raise RecordRejectedError("Google event has invalid updated timestamp") from exc

    canonical_data = {
        "calendar_id": calendar_id,
        "provider_event_id": str(event_id),
        "title": raw_event.get("summary"),
        "description": raw_event.get("description"),
        "status": status,
        "start_at": _event_boundary(raw_event.get("start")),
        "end_at": _event_boundary(raw_event.get("end")),
        "is_deleted": is_deleted,
        "provider_updated_at": format_provider_datetime(provider_updated_at),
    }

    return NormalizedRecordInput(
        provider=GOOGLE_PROVIDER,
        entity_type=EVENT_ENTITY_TYPE,
        external_id=f"{calendar_id}:{event_id}",
        source_updated_at=provider_updated_at,
        canonical_data=canonical_data,
        raw_payload=raw_event,
    )


def _event_boundary(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    raw_datetime = value.get("dateTime") or value.get("date")
    if raw_datetime is None:
        return None
    try:
        return format_provider_datetime(parse_provider_datetime(raw_datetime))
    except ValueError:
        return None


class GoogleCalendarHttpClient:
    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        base_url: str = "https://www.googleapis.com/calendar/v3",
        token_url: str = "https://oauth2.googleapis.com/token",
        timeout_seconds: float = 10.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._access_token: str | None = None
        self._access_token_expires_at: datetime | None = None
        self._token_url = token_url
        self._client = http_client or httpx.Client(base_url=base_url, timeout=timeout_seconds)

    def fetch_events_page(
        self,
        *,
        calendar_id: str,
        page_token: str | None,
        sync_token: str | None,
    ) -> ProviderPage:
        params: dict[str, Any] = {"showDeleted": "true"}
        if page_token is not None:
            params["pageToken"] = page_token
        if sync_token is not None:
            params["syncToken"] = sync_token
        data = self._request_json(
            "GET",
            f"/calendars/{quote(calendar_id, safe='')}/events",
            params=params,
        )
        items = data.get("items")
        if not isinstance(items, list):
            raise ProviderResponseError("Google Calendar page is missing items list")
        return ProviderPage(
            items=items,
            next_cursor=data.get("nextPageToken"),
            next_sync_token=data.get("nextSyncToken"),
        )

    def _request_json(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self._send_authorized_request(method, path, **kwargs)
            if response.status_code == 401:
                self._access_token = None
                self._access_token_expires_at = None
                response = self._send_authorized_request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("Google Calendar request timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderTransientError("Google Calendar HTTP client failed") from exc

        if response.status_code == 410:
            raise GoogleSyncTokenExpired("Google Calendar sync token expired")
        if response.status_code == 429:
            raise ProviderRateLimitError(
                "Google Calendar rate limit exceeded",
                retry_after_seconds=_retry_after(response),
            )
        if 500 <= response.status_code <= 599:
            raise ProviderTransientError(f"Google Calendar transient HTTP {response.status_code}")
        if response.status_code >= 400:
            raise ProviderResponseError(
                f"Google Calendar non-retryable HTTP {response.status_code}"
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderResponseError("Google Calendar response was not valid JSON") from exc
        if not isinstance(data, dict):
            raise ProviderResponseError("Google Calendar response JSON must be an object")
        return data

    def _send_authorized_request(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> httpx.Response:
        return self._client.request(
            method,
            path,
            headers={"Authorization": f"Bearer {self._get_access_token()}"},
            **kwargs,
        )

    def _get_access_token(self) -> str:
        if self._access_token and self._access_token_expires_at:
            if self._access_token_expires_at > datetime.now(UTC) + timedelta(seconds=60):
                return self._access_token

        try:
            response = self._client.post(
                self._token_url,
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "refresh_token": self._refresh_token,
                    "grant_type": "refresh_token",
                },
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("Google OAuth token refresh timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderTransientError("Google OAuth token refresh failed") from exc

        if response.status_code >= 400:
            raise ProviderResponseError("Google OAuth token refresh failed")

        try:
            token_data = response.json()
        except ValueError as exc:
            raise ProviderResponseError("Google OAuth token response was not valid JSON") from exc
        if not isinstance(token_data, dict):
            raise ProviderResponseError("Google OAuth token response JSON must be an object")

        access_token = token_data.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise ProviderResponseError("Google OAuth token response did not include access_token")

        expires_in = token_data.get("expires_in", 3600)
        if not isinstance(expires_in, int):
            expires_in = 3600
        self._access_token = access_token
        self._access_token_expires_at = datetime.now(UTC) + timedelta(seconds=max(expires_in, 0))
        return access_token


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None
