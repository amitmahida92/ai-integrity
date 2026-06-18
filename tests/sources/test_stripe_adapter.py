from datetime import UTC, datetime
from typing import Any

from app.sources.exceptions import ProviderTransientError
from app.sources.retry import RetryPolicy
from app.sources.stripe import StripePaymentIntentsAdapter
from app.sources.types import ProviderPage


def payment_intent(payment_intent_id: str, *, status: str = "succeeded") -> dict[str, Any]:
    return {
        "id": payment_intent_id,
        "amount": 2500,
        "amount_received": 2500 if status == "succeeded" else 0,
        "currency": "usd",
        "status": status,
        "customer": "cus_123",
        "created": 1_781_697_600,
    }


def stripe_event(
    event_id: str,
    payment_intent_id: str,
    *,
    created: int,
    event_type: str = "payment_intent.succeeded",
) -> dict[str, Any]:
    return {
        "id": event_id,
        "type": event_type,
        "created": created,
        "data": {"object": {"id": payment_intent_id, "object": "payment_intent"}},
    }


def test_stripe_full_fetch_paginates_and_sets_event_watermark_to_run_start() -> None:
    class FakeStripeClient:
        def __init__(self) -> None:
            self.calls: list[str | None] = []

        def fetch_payment_intents_page(
            self,
            *,
            starting_after: str | None,
            limit: int,
        ) -> ProviderPage:
            self.calls.append(starting_after)
            if starting_after is None:
                return ProviderPage(
                    items=[payment_intent("pi_1"), "not-a-payment-intent"],
                    has_more=True,
                    next_cursor="pi_1",
                )
            return ProviderPage(items=[payment_intent("pi_2", status="requires_action")])

        def fetch_events_page(
            self,
            *,
            created_gte: datetime,
            starting_after: str | None,
            limit: int,
        ) -> ProviderPage:
            raise AssertionError("full fetch should not query events")

        def retrieve_payment_intent(self, payment_intent_id: str) -> dict[str, Any]:
            raise AssertionError("full fetch should not retrieve individual PaymentIntents")

    client = FakeStripeClient()
    adapter = StripePaymentIntentsAdapter(
        client=client,
        now=lambda: datetime(2026, 6, 17, 12, 30, tzinfo=UTC),
    )

    result = adapter.fetch_full()

    assert client.calls == [None, "pi_1"]
    assert result.records_fetched == 3
    assert result.rejected_records == 1
    assert [record.external_id for record in result.records] == ["pi_1", "pi_2"]
    assert result.records[0].canonical_data["amount"] == 2500
    assert result.records[0].raw_payload["id"] == "pi_1"
    assert result.next_checkpoint_data == {
        "event_watermark": "2026-06-17T12:30:00Z",
        "overlap_seconds": 120,
    }


def test_stripe_incremental_uses_events_deduplicates_and_retrieves_current_state() -> None:
    class FakeStripeClient:
        def __init__(self) -> None:
            self.event_queries: list[tuple[datetime, str | None]] = []
            self.retrieve_calls: list[str] = []

        def fetch_payment_intents_page(
            self,
            *,
            starting_after: str | None,
            limit: int,
        ) -> ProviderPage:
            raise AssertionError("incremental fetch should not list PaymentIntents directly")

        def fetch_events_page(
            self,
            *,
            created_gte: datetime,
            starting_after: str | None,
            limit: int,
        ) -> ProviderPage:
            self.event_queries.append((created_gte, starting_after))
            return ProviderPage(
                items=[
                    stripe_event("evt_1", "pi_1", created=1_781_697_660),
                    stripe_event("evt_2", "pi_1", created=1_781_697_720),
                    stripe_event(
                        "evt_3",
                        "pi_irrelevant",
                        created=1_781_697_780,
                        event_type="charge.succeeded",
                    ),
                    "not-an-event",
                    {"id": "evt_4", "type": "payment_intent.succeeded", "created": 1_781_697_840},
                ],
            )

        def retrieve_payment_intent(self, payment_intent_id: str) -> dict[str, Any]:
            self.retrieve_calls.append(payment_intent_id)
            return payment_intent(payment_intent_id, status="succeeded")

    client = FakeStripeClient()
    adapter = StripePaymentIntentsAdapter(
        client=client,
        now=lambda: datetime(2026, 6, 17, 12, 30, tzinfo=UTC),
    )

    result = adapter.fetch_incremental(
        {"event_watermark": "2026-06-17T12:10:00Z", "overlap_seconds": 120}
    )

    assert client.event_queries == [(datetime(2026, 6, 17, 12, 8, tzinfo=UTC), None)]
    assert client.retrieve_calls == ["pi_1"]
    assert result.records_fetched == 5
    assert result.rejected_records == 2
    assert result.records[0].external_id == "pi_1"
    assert result.records[0].source_updated_at == datetime(2026, 6, 17, 12, 2, tzinfo=UTC)
    assert result.records[0].raw_payload["source_event"]["id"] == "evt_2"
    assert result.next_checkpoint_data == {
        "event_watermark": "2026-06-17T12:30:00Z",
        "overlap_seconds": 120,
        "last_event_id": "evt_4",
    }
    assert result.metadata == {
        "event_query_start": "2026-06-17T12:08:00Z",
        "payment_intents_retrieved": 1,
    }


def test_stripe_full_fetch_retries_transient_errors_with_bound() -> None:
    class FlakyStripeClient:
        def __init__(self) -> None:
            self.calls = 0

        def fetch_payment_intents_page(
            self,
            *,
            starting_after: str | None,
            limit: int,
        ) -> ProviderPage:
            self.calls += 1
            if self.calls < 3:
                raise ProviderTransientError("temporary Stripe outage")
            return ProviderPage(items=[payment_intent("pi_1")])

        def fetch_events_page(
            self,
            *,
            created_gte: datetime,
            starting_after: str | None,
            limit: int,
        ) -> ProviderPage:
            raise AssertionError("full fetch should not query events")

        def retrieve_payment_intent(self, payment_intent_id: str) -> dict[str, Any]:
            raise AssertionError("full fetch should not retrieve individual PaymentIntents")

    client = FlakyStripeClient()
    adapter = StripePaymentIntentsAdapter(
        client=client,
        retry_policy=RetryPolicy(max_attempts=3, base_delay_seconds=0),
    )

    result = adapter.fetch_full()

    assert client.calls == 3
    assert [record.external_id for record in result.records] == ["pi_1"]
