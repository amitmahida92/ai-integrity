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
    to_unix_seconds,
    utc_now,
)
from app.sources.types import FetchResult, ProviderPage

STRIPE_PROVIDER = "stripe"
PAYMENT_INTENT_ENTITY_TYPE = "payment_intent"
DEFAULT_OVERLAP_SECONDS = 120
PAYMENT_INTENT_EVENT_TYPES = {
    "payment_intent.created",
    "payment_intent.processing",
    "payment_intent.succeeded",
    "payment_intent.payment_failed",
    "payment_intent.canceled",
    "payment_intent.requires_action",
}


class StripePaymentIntentsClient(Protocol):
    def fetch_payment_intents_page(self, *, starting_after: str | None, limit: int) -> ProviderPage:
        ...

    def fetch_events_page(
        self,
        *,
        created_gte: datetime,
        starting_after: str | None,
        limit: int,
    ) -> ProviderPage:
        ...

    def retrieve_payment_intent(self, payment_intent_id: str) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class StripePaymentIntentsAdapter:
    client: StripePaymentIntentsClient
    page_size: int = 100
    retry_policy: RetryPolicy = RetryPolicy()
    now: Callable[[], datetime] = utc_now
    sleep: Callable[[float], None] = lambda _: None

    def fetch_full(self, checkpoint_data: dict[str, Any] | None = None) -> FetchResult:
        started_at = self.now()
        starting_after: str | None = None
        records: list[NormalizedRecordInput] = []
        records_fetched = 0
        rejected_records = 0
        pages_fetched = 0

        while True:
            page = call_with_retry(
                lambda starting_after=starting_after: self.client.fetch_payment_intents_page(
                    starting_after=starting_after,
                    limit=self.page_size,
                ),
                retry_policy=self.retry_policy,
                sleep=self.sleep,
            )
            pages_fetched += 1
            records_fetched += len(page.items)

            for raw_payment_intent in page.items:
                if not isinstance(raw_payment_intent, dict):
                    rejected_records += 1
                    continue
                try:
                    records.append(normalize_payment_intent(raw_payment_intent))
                except RecordRejectedError:
                    rejected_records += 1

            if not page.has_more:
                break
            starting_after = page.next_cursor or _last_item_id(page.items)
            if not starting_after:
                raise ProviderResponseError("Stripe page has_more without a cursor")

        overlap_seconds = int(
            (checkpoint_data or {}).get("overlap_seconds", DEFAULT_OVERLAP_SECONDS)
        )
        return FetchResult(
            provider=STRIPE_PROVIDER,
            effective_mode="full",
            records=records,
            records_fetched=records_fetched,
            rejected_records=rejected_records,
            pages_fetched=pages_fetched,
            next_checkpoint_data={
                "event_watermark": format_provider_datetime(started_at),
                "overlap_seconds": overlap_seconds,
            },
            started_at=started_at,
        )

    def fetch_incremental(self, checkpoint_data: dict[str, Any]) -> FetchResult:
        watermark = _checkpoint_watermark(checkpoint_data)
        if watermark is None:
            return self.fetch_full(checkpoint_data)

        started_at = self.now()
        overlap_seconds = int(checkpoint_data.get("overlap_seconds", DEFAULT_OVERLAP_SECONDS))
        created_gte = watermark - timedelta(seconds=overlap_seconds)
        starting_after: str | None = None
        events_fetched = 0
        rejected_records = 0
        pages_fetched = 0
        last_event_id: str | None = checkpoint_data.get("last_event_id")
        payment_intent_event: dict[str, dict[str, Any]] = {}

        while True:
            page = call_with_retry(
                lambda starting_after=starting_after: self.client.fetch_events_page(
                    created_gte=created_gte,
                    starting_after=starting_after,
                    limit=self.page_size,
                ),
                retry_policy=self.retry_policy,
                sleep=self.sleep,
            )
            pages_fetched += 1
            events_fetched += len(page.items)

            for raw_event in page.items:
                if not isinstance(raw_event, dict):
                    rejected_records += 1
                    continue
                if raw_event.get("id"):
                    last_event_id = str(raw_event["id"])
                if raw_event.get("type") not in PAYMENT_INTENT_EVENT_TYPES:
                    continue
                try:
                    payment_intent_id = _payment_intent_id_from_event(raw_event)
                    event_created_at = _event_created_at(raw_event)
                except RecordRejectedError:
                    rejected_records += 1
                    continue
                existing_event = payment_intent_event.get(payment_intent_id)
                if existing_event is None or event_created_at >= _event_created_at(existing_event):
                    payment_intent_event[payment_intent_id] = raw_event

            if not page.has_more:
                break
            starting_after = page.next_cursor or _last_item_id(page.items)
            if not starting_after:
                raise ProviderResponseError("Stripe events page has_more without a cursor")

        records: list[NormalizedRecordInput] = []
        for payment_intent_id, raw_event in payment_intent_event.items():
            try:
                payment_intent = call_with_retry(
                    lambda payment_intent_id=payment_intent_id: (
                        self.client.retrieve_payment_intent(payment_intent_id)
                    ),
                    retry_policy=self.retry_policy,
                    sleep=self.sleep,
                )
                records.append(normalize_payment_intent(payment_intent, source_event=raw_event))
            except RecordRejectedError:
                rejected_records += 1

        return FetchResult(
            provider=STRIPE_PROVIDER,
            effective_mode="incremental",
            records=records,
            records_fetched=events_fetched,
            rejected_records=rejected_records,
            pages_fetched=pages_fetched,
            next_checkpoint_data={
                "event_watermark": format_provider_datetime(started_at),
                "overlap_seconds": overlap_seconds,
                "last_event_id": last_event_id,
            },
            started_at=started_at,
            metadata={
                "event_query_start": format_provider_datetime(created_gte),
                "payment_intents_retrieved": len(payment_intent_event),
            },
        )


def normalize_payment_intent(
    raw_payment_intent: dict[str, Any],
    *,
    source_event: dict[str, Any] | None = None,
) -> NormalizedRecordInput:
    payment_intent_id = raw_payment_intent.get("id")
    if not payment_intent_id:
        raise RecordRejectedError("Stripe PaymentIntent must include id")

    try:
        provider_created_at = parse_provider_datetime(raw_payment_intent.get("created"))
    except ValueError as exc:
        raise RecordRejectedError("Stripe PaymentIntent has invalid created timestamp") from exc

    last_event_created_at = _stripe_event_created_at(source_event) or provider_created_at
    canonical_data = {
        "amount": raw_payment_intent.get("amount"),
        "amount_received": raw_payment_intent.get("amount_received"),
        "currency": raw_payment_intent.get("currency"),
        "status": raw_payment_intent.get("status"),
        "customer_id": raw_payment_intent.get("customer"),
        "provider_created_at": format_provider_datetime(provider_created_at),
        "last_event_created_at": format_provider_datetime(last_event_created_at),
    }

    raw_payload: dict[str, Any]
    if source_event is None:
        raw_payload = raw_payment_intent
    else:
        raw_payload = {
            "payment_intent": raw_payment_intent,
            "source_event": source_event,
        }

    return NormalizedRecordInput(
        provider=STRIPE_PROVIDER,
        entity_type=PAYMENT_INTENT_ENTITY_TYPE,
        external_id=str(payment_intent_id),
        source_updated_at=last_event_created_at,
        canonical_data=canonical_data,
        raw_payload=raw_payload,
    )


def _payment_intent_id_from_event(raw_event: dict[str, Any]) -> str:
    data = raw_event.get("data")
    event_object = data.get("object") if isinstance(data, dict) else None
    if not isinstance(event_object, dict):
        raise RecordRejectedError("Stripe event data.object must be an object")
    payment_intent_id = event_object.get("id")
    if not payment_intent_id:
        raise RecordRejectedError("Stripe PaymentIntent event is missing object id")
    return str(payment_intent_id)


def _stripe_event_created_at(source_event: dict[str, Any] | None) -> datetime | None:
    if source_event is None:
        return None
    created = source_event.get("created")
    if created is None:
        return None
    try:
        return parse_provider_datetime(created)
    except ValueError as exc:
        raise RecordRejectedError("Stripe event has invalid created timestamp") from exc


def _event_created_at(raw_event: dict[str, Any]) -> datetime:
    try:
        return parse_provider_datetime(raw_event.get("created"))
    except ValueError as exc:
        raise RecordRejectedError("Stripe event has invalid created timestamp") from exc


def _checkpoint_watermark(checkpoint_data: dict[str, Any]) -> datetime | None:
    watermark = checkpoint_data.get("event_watermark")
    if watermark is None:
        return None
    try:
        return parse_provider_datetime(watermark)
    except ValueError as exc:
        raise ProviderResponseError("Stripe checkpoint event_watermark is invalid") from exc


def _last_item_id(items: list[Any]) -> str | None:
    for item in reversed(items):
        if not isinstance(item, dict):
            continue
        value = item.get("id")
        if value is not None:
            return str(value)
    return None


class StripeHttpClient:
    def __init__(
        self,
        *,
        secret_key: str,
        base_url: str = "https://api.stripe.com/v1",
        timeout_seconds: float = 10.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._secret_key = secret_key
        self._client = http_client or httpx.Client(base_url=base_url, timeout=timeout_seconds)

    def fetch_payment_intents_page(self, *, starting_after: str | None, limit: int) -> ProviderPage:
        params: dict[str, Any] = {"limit": limit}
        if starting_after is not None:
            params["starting_after"] = starting_after
        data = self._request_json("GET", "/payment_intents", params=params)
        return _stripe_list_page_from_response(data)

    def fetch_events_page(
        self,
        *,
        created_gte: datetime,
        starting_after: str | None,
        limit: int,
    ) -> ProviderPage:
        params: dict[str, Any] = {
            "limit": limit,
            "created[gte]": to_unix_seconds(created_gte),
        }
        if starting_after is not None:
            params["starting_after"] = starting_after
        data = self._request_json("GET", "/events", params=params)
        return _stripe_list_page_from_response(data)

    def retrieve_payment_intent(self, payment_intent_id: str) -> dict[str, Any]:
        data = self._request_json("GET", f"/payment_intents/{payment_intent_id}")
        if not isinstance(data, dict):
            raise ProviderResponseError("Stripe PaymentIntent response JSON must be an object")
        return data

    def _request_json(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self._client.request(
                method,
                path,
                headers={"Authorization": f"Bearer {self._secret_key}"},
                **kwargs,
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("Stripe request timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderTransientError("Stripe HTTP client failed") from exc

        if response.status_code == 429:
            raise ProviderRateLimitError(
                "Stripe rate limit exceeded",
                retry_after_seconds=_retry_after(response),
            )
        if 500 <= response.status_code <= 599:
            raise ProviderTransientError(f"Stripe transient HTTP {response.status_code}")
        if response.status_code >= 400:
            raise ProviderResponseError(f"Stripe non-retryable HTTP {response.status_code}")

        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderResponseError("Stripe response was not valid JSON") from exc
        if not isinstance(data, dict):
            raise ProviderResponseError("Stripe response JSON must be an object")
        return data


def _stripe_list_page_from_response(data: dict[str, Any]) -> ProviderPage:
    items = data.get("data")
    if not isinstance(items, list):
        raise ProviderResponseError("Stripe list response is missing data list")
    has_more = bool(data.get("has_more"))
    return ProviderPage(
        items=items,
        has_more=has_more,
        next_cursor=_last_item_id(items) if has_more else None,
    )


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None
