from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

import httpx

from app.repositories.normalized_records import NormalizedRecordInput
from app.sources.exceptions import (
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderTransientError,
    RecordRejectedError,
)
from app.sources.retry import RetryPolicy, call_with_retry
from app.sources.time import (
    format_provider_datetime,
    parse_provider_datetime,
    to_unix_millis,
    utc_now,
)
from app.sources.types import FetchResult, ProviderPage

HUBSPOT_PROVIDER = "hubspot"
CONTACT_ENTITY_TYPE = "contact"
DEFAULT_OVERLAP_SECONDS = 120


class HubSpotContactsClient(Protocol):
    def fetch_contacts_page(self, *, after: str | None, limit: int) -> ProviderPage:
        ...

    def search_contacts_modified_since_page(
        self,
        *,
        modified_since: datetime,
        after: str | None,
        limit: int,
    ) -> ProviderPage:
        ...


@dataclass(frozen=True)
class HubSpotContactsAdapter:
    client: HubSpotContactsClient
    page_size: int = 100
    retry_policy: RetryPolicy = RetryPolicy()
    now: Callable[[], datetime] = utc_now
    sleep: Callable[[float], None] = lambda _: None

    def fetch_full(self, checkpoint_data: dict[str, Any] | None = None) -> FetchResult:
        started_at = self.now()
        return self._fetch_pages(
            effective_mode="full",
            started_at=started_at,
            checkpoint_data=checkpoint_data or {},
            page_fetcher=lambda after: self.client.fetch_contacts_page(
                after=after,
                limit=self.page_size,
            ),
        )

    def fetch_incremental(self, checkpoint_data: dict[str, Any]) -> FetchResult:
        watermark = _checkpoint_watermark(checkpoint_data)
        if watermark is None:
            return self.fetch_full(checkpoint_data)

        overlap_seconds = int(checkpoint_data.get("overlap_seconds", DEFAULT_OVERLAP_SECONDS))
        modified_since = watermark - timedelta(seconds=overlap_seconds)
        started_at = self.now()
        return self._fetch_pages(
            effective_mode="incremental",
            started_at=started_at,
            checkpoint_data=checkpoint_data,
            existing_watermark=watermark,
            page_fetcher=lambda after: self.client.search_contacts_modified_since_page(
                modified_since=modified_since,
                after=after,
                limit=self.page_size,
            ),
            metadata={"modified_since": format_provider_datetime(modified_since)},
        )

    def _fetch_pages(
        self,
        *,
        effective_mode: str,
        started_at: datetime,
        checkpoint_data: dict[str, Any],
        page_fetcher: Callable[[str | None], ProviderPage],
        existing_watermark: datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> FetchResult:
        after: str | None = None
        records: list[NormalizedRecordInput] = []
        records_fetched = 0
        rejected_records = 0
        pages_fetched = 0

        while True:
            page = call_with_retry(
                lambda after=after: page_fetcher(after),
                retry_policy=self.retry_policy,
                sleep=self.sleep,
            )
            pages_fetched += 1
            records_fetched += len(page.items)

            for raw_contact in page.items:
                try:
                    records.append(normalize_hubspot_contact(raw_contact))
                except RecordRejectedError:
                    rejected_records += 1

            if not page.next_cursor:
                break
            after = page.next_cursor

        max_timestamp = _max_source_updated_at(records)
        next_watermark = max_timestamp or existing_watermark or started_at
        overlap_seconds = int(checkpoint_data.get("overlap_seconds", DEFAULT_OVERLAP_SECONDS))

        return FetchResult(
            provider=HUBSPOT_PROVIDER,
            effective_mode=effective_mode,  # type: ignore[arg-type]
            records=records,
            records_fetched=records_fetched,
            rejected_records=rejected_records,
            pages_fetched=pages_fetched,
            next_checkpoint_data={
                "watermark": format_provider_datetime(next_watermark),
                "overlap_seconds": overlap_seconds,
            },
            started_at=started_at,
            metadata=metadata or {},
        )


def normalize_hubspot_contact(raw_contact: dict[str, Any]) -> NormalizedRecordInput:
    external_id = raw_contact.get("id")
    properties = raw_contact.get("properties")
    if not external_id or not isinstance(properties, dict):
        raise RecordRejectedError("HubSpot contact must include id and properties")

    provider_updated_at = _contact_updated_at(raw_contact, properties)
    if provider_updated_at is None:
        raise RecordRejectedError("HubSpot contact is missing lastmodifieddate")

    canonical_data = {
        "email": properties.get("email"),
        "first_name": properties.get("firstname"),
        "last_name": properties.get("lastname"),
        "phone": properties.get("phone"),
        "provider_updated_at": format_provider_datetime(provider_updated_at),
    }

    return NormalizedRecordInput(
        provider=HUBSPOT_PROVIDER,
        entity_type=CONTACT_ENTITY_TYPE,
        external_id=str(external_id),
        source_updated_at=provider_updated_at,
        canonical_data=canonical_data,
        raw_payload=raw_contact,
    )


def _contact_updated_at(raw_contact: dict[str, Any], properties: dict[str, Any]) -> datetime | None:
    for value in (
        properties.get("lastmodifieddate"),
        raw_contact.get("updatedAt"),
        raw_contact.get("archivedAt"),
    ):
        if value is None:
            continue
        try:
            return parse_provider_datetime(value)
        except ValueError as exc:
            raise RecordRejectedError("HubSpot contact has invalid updated timestamp") from exc
    return None


def _checkpoint_watermark(checkpoint_data: dict[str, Any]) -> datetime | None:
    watermark = checkpoint_data.get("watermark")
    if watermark is None:
        return None
    try:
        return parse_provider_datetime(watermark)
    except ValueError as exc:
        raise ProviderResponseError("HubSpot checkpoint watermark is invalid") from exc


def _max_source_updated_at(records: list[NormalizedRecordInput]) -> datetime | None:
    timestamps = [
        record.source_updated_at
        for record in records
        if record.source_updated_at is not None
    ]
    return max(timestamps) if timestamps else None


class HubSpotHttpClient:
    def __init__(
        self,
        *,
        access_token: str,
        base_url: str = "https://api.hubapi.com",
        timeout_seconds: float = 10.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._access_token = access_token
        self._client = http_client or httpx.Client(base_url=base_url, timeout=timeout_seconds)

    def fetch_contacts_page(self, *, after: str | None, limit: int) -> ProviderPage:
        params: dict[str, Any] = {
            "limit": limit,
            "properties": "email,firstname,lastname,phone,lastmodifieddate",
        }
        if after is not None:
            params["after"] = after
        data = self._request_json("GET", "/crm/v3/objects/contacts", params=params)
        return _hubspot_page_from_response(data)

    def search_contacts_modified_since_page(
        self,
        *,
        modified_since: datetime,
        after: str | None,
        limit: int,
    ) -> ProviderPage:
        payload: dict[str, Any] = {
            "limit": limit,
            "properties": ["email", "firstname", "lastname", "phone", "lastmodifieddate"],
            "filterGroups": [
                {
                    "filters": [
                        {
                            "propertyName": "lastmodifieddate",
                            "operator": "GTE",
                            "value": str(to_unix_millis(modified_since)),
                        }
                    ]
                }
            ],
            "sorts": [{"propertyName": "lastmodifieddate", "direction": "ASCENDING"}],
        }
        if after is not None:
            payload["after"] = after
        data = self._request_json("POST", "/crm/v3/objects/contacts/search", json=payload)
        return _hubspot_page_from_response(data)

    def _request_json(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self._client.request(
                method,
                path,
                headers={"Authorization": f"Bearer {self._access_token}"},
                **kwargs,
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("HubSpot request timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderTransientError("HubSpot HTTP client failed") from exc

        if response.status_code == 429:
            raise ProviderRateLimitError(
                "HubSpot rate limit exceeded",
                retry_after_seconds=_retry_after(response),
            )
        if 500 <= response.status_code <= 599:
            raise ProviderTransientError(f"HubSpot transient HTTP {response.status_code}")
        if response.status_code >= 400:
            raise ProviderResponseError(f"HubSpot non-retryable HTTP {response.status_code}")

        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderResponseError("HubSpot response was not valid JSON") from exc
        if not isinstance(data, dict):
            raise ProviderResponseError("HubSpot response JSON must be an object")
        return data


def _hubspot_page_from_response(data: dict[str, Any]) -> ProviderPage:
    results = data.get("results")
    if not isinstance(results, list):
        raise ProviderResponseError("HubSpot page is missing results list")
    paging = data.get("paging") or {}
    next_cursor = None
    if isinstance(paging, dict):
        next_data = paging.get("next") or {}
        if isinstance(next_data, dict):
            next_cursor = next_data.get("after")
    return ProviderPage(
        items=[item for item in results if isinstance(item, dict)],
        next_cursor=next_cursor,
    )


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None
