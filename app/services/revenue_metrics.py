from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Protocol

from sqlalchemy import Select, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import NormalizedFinancialRecord, RevenueStatusAllowlist
from app.sources.types import ProviderPage

ALLOWLIST_SEED_ROWS: tuple[dict[str, Any], ...] = (
    {
        "source_name": "stripe",
        "source_entity_type": "payment_intent",
        "raw_status": "succeeded",
        "canonical_status": "collected",
        "counts_as_collected": True,
    },
    {
        "source_name": "mock_finance",
        "source_entity_type": "invoice",
        "raw_status": "paid",
        "canonical_status": "collected",
        "counts_as_collected": True,
    },
    {
        "source_name": "mock_finance",
        "source_entity_type": "payment",
        "raw_status": "completed",
        "canonical_status": "collected",
        "counts_as_collected": True,
    },
)

MOCK_FINANCIAL_RECORDS: tuple[dict[str, Any], ...] = (
    {
        "source_name": "mock_finance",
        "source_entity_type": "invoice",
        "external_id": "mock-invoice-paid-1",
        "amount_minor": 10000,
        "currency": "USD",
        "raw_status": "paid",
        "occurred_at": datetime(2026, 6, 1, tzinfo=UTC),
    },
    {
        "source_name": "mock_finance",
        "source_entity_type": "payment",
        "external_id": "mock-payment-completed-1",
        "amount_minor": 5000,
        "currency": "USD",
        "raw_status": "completed",
        "occurred_at": datetime(2026, 6, 2, tzinfo=UTC),
    },
    {
        "source_name": "mock_finance",
        "source_entity_type": "invoice",
        "external_id": "mock-invoice-pending-1",
        "amount_minor": 7000,
        "currency": "USD",
        "raw_status": "pending",
        "occurred_at": datetime(2026, 6, 3, tzinfo=UTC),
    },
    {
        "source_name": "mock_finance",
        "source_entity_type": "invoice",
        "external_id": "mock-invoice-failed-1",
        "amount_minor": 8000,
        "currency": "USD",
        "raw_status": "failed",
        "occurred_at": datetime(2026, 6, 4, tzinfo=UTC),
    },
    {
        "source_name": "mock_finance",
        "source_entity_type": "invoice",
        "external_id": "mock-invoice-voided-1",
        "amount_minor": 9000,
        "currency": "USD",
        "raw_status": "voided",
        "occurred_at": datetime(2026, 6, 5, tzinfo=UTC),
    },
    {
        "source_name": "mock_finance",
        "source_entity_type": "payment",
        "external_id": "mock-payment-refunded-1",
        "amount_minor": 4000,
        "currency": "USD",
        "raw_status": "refunded",
        "occurred_at": datetime(2026, 6, 6, tzinfo=UTC),
    },
    {
        "source_name": "mock_finance",
        "source_entity_type": "invoice",
        "external_id": "mock-invoice-unknown-1",
        "amount_minor": 999999,
        "currency": "USD",
        "raw_status": "new_unknown_status",
        "occurred_at": datetime(2026, 6, 7, tzinfo=UTC),
    },
)


class StripeFinancialClient(Protocol):
    def fetch_payment_intents_page(self, *, starting_after: str | None, limit: int) -> ProviderPage:
        ...


class RevenueMetricsService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def seed_demo_data(self) -> dict[str, int]:
        self._upsert_allowlist_rows(list(ALLOWLIST_SEED_ROWS))
        self._upsert_financial_records(
            [
                {
                    **record,
                    "customer_reference": None,
                    "raw_payload": _seed_raw_payload(record),
                }
                for record in MOCK_FINANCIAL_RECORDS
            ]
        )
        self.session.commit()
        return {
            "allowlist_rows": len(ALLOWLIST_SEED_ROWS),
            "financial_records": len(MOCK_FINANCIAL_RECORDS),
        }

    def import_stripe_payment_intents(
        self,
        client: StripeFinancialClient,
        *,
        page_size: int = 100,
    ) -> dict[str, int]:
        starting_after: str | None = None
        imported_records = 0
        rejected_records = 0
        pages_fetched = 0

        while True:
            page = client.fetch_payment_intents_page(
                starting_after=starting_after,
                limit=page_size,
            )
            pages_fetched += 1
            rows: list[dict[str, Any]] = []
            for raw_payment_intent in page.items:
                if not isinstance(raw_payment_intent, dict):
                    rejected_records += 1
                    continue
                try:
                    rows.append(stripe_payment_intent_to_financial_record(raw_payment_intent))
                except ValueError:
                    rejected_records += 1

            if rows:
                self._upsert_financial_records(rows)
                imported_records += len(rows)

            if not page.has_more:
                break
            starting_after = page.next_cursor or _last_dict_item_id(page.items)
            if not starting_after:
                raise ValueError("Stripe page has_more without a cursor")

        self.session.commit()
        return {
            "imported_records": imported_records,
            "rejected_records": rejected_records,
            "pages_fetched": pages_fetched,
        }

    def collected_revenue_summary(
        self,
        *,
        from_date: date,
        to_date: date,
    ) -> dict[str, int]:
        rows = self.session.execute(
            self._collected_revenue_query(from_date=from_date, to_date=to_date)
        ).all()
        return {currency: int(total or 0) for currency, total in rows}

    def collected_revenue_breakdown(
        self,
        *,
        from_date: date,
        to_date: date,
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        bucket_expr = func.date_trunc("day", NormalizedFinancialRecord.occurred_at).label(
            "bucket_start"
        )
        rows = self.session.execute(
            self._collected_revenue_query(
                from_date=from_date,
                to_date=to_date,
                bucket_expr=bucket_expr,
            )
        ).all()

        bucket_map: dict[str, dict[str, int]] = {}
        aggregate_totals: dict[str, int] = {}
        for bucket_start, currency, total in rows:
            bucket_key = bucket_start.date().isoformat()
            amount = int(total or 0)
            bucket_map.setdefault(bucket_key, {})[currency] = amount
            aggregate_totals[currency] = aggregate_totals.get(currency, 0) + amount

        buckets = [
            {"date": bucket_date, "totals_by_currency": bucket_map[bucket_date]}
            for bucket_date in sorted(bucket_map)
        ]
        return buckets, aggregate_totals

    def _collected_revenue_query(
        self,
        *,
        from_date: date,
        to_date: date,
        bucket_expr: Any | None = None,
    ) -> Select[tuple[Any, ...]]:
        start_at, end_at = _date_range_to_datetimes(from_date, to_date)
        selected_columns: list[Any] = []
        group_by_columns: list[Any] = []
        if bucket_expr is not None:
            selected_columns.append(bucket_expr)
            group_by_columns.append(bucket_expr)
        selected_columns.extend(
            [
                NormalizedFinancialRecord.currency,
                func.sum(NormalizedFinancialRecord.amount_minor).label("amount_minor"),
            ]
        )
        group_by_columns.append(NormalizedFinancialRecord.currency)

        return (
            select(*selected_columns)
            .join(
                RevenueStatusAllowlist,
                (NormalizedFinancialRecord.source_name == RevenueStatusAllowlist.source_name)
                & (
                    NormalizedFinancialRecord.source_entity_type
                    == RevenueStatusAllowlist.source_entity_type
                )
                & (NormalizedFinancialRecord.raw_status == RevenueStatusAllowlist.raw_status),
            )
            .where(
                RevenueStatusAllowlist.counts_as_collected.is_(True),
                NormalizedFinancialRecord.occurred_at >= start_at,
                NormalizedFinancialRecord.occurred_at < end_at,
            )
            .group_by(*group_by_columns)
            .order_by(*group_by_columns)
        )

    def _upsert_allowlist_rows(self, rows: list[dict[str, Any]]) -> None:
        statement = insert(RevenueStatusAllowlist).values(rows)
        statement = statement.on_conflict_do_update(
            index_elements=["source_name", "source_entity_type", "raw_status"],
            set_={
                "canonical_status": statement.excluded.canonical_status,
                "counts_as_collected": statement.excluded.counts_as_collected,
            },
        )
        self.session.execute(statement)

    def _upsert_financial_records(self, rows: list[dict[str, Any]]) -> None:
        statement = insert(NormalizedFinancialRecord).values(rows)
        statement = statement.on_conflict_do_update(
            index_elements=["source_name", "source_entity_type", "external_id"],
            set_={
                "amount_minor": statement.excluded.amount_minor,
                "currency": statement.excluded.currency,
                "raw_status": statement.excluded.raw_status,
                "occurred_at": statement.excluded.occurred_at,
                "customer_reference": statement.excluded.customer_reference,
                "raw_payload": statement.excluded.raw_payload,
                "updated_at": func.now(),
            },
        )
        self.session.execute(statement)


def stripe_payment_intent_to_financial_record(raw_payment_intent: dict[str, Any]) -> dict[str, Any]:
    external_id = raw_payment_intent.get("id")
    raw_status = raw_payment_intent.get("status")
    currency = raw_payment_intent.get("currency")
    created = raw_payment_intent.get("created")
    if not external_id or not raw_status or not currency or created is None:
        raise ValueError("Stripe PaymentIntent is missing required fields")

    amount_received = raw_payment_intent.get("amount_received")
    amount = raw_payment_intent.get("amount")
    amount_minor = (
        amount_received if isinstance(amount_received, int) and amount_received > 0 else amount
    )
    if not isinstance(amount_minor, int):
        raise ValueError("Stripe PaymentIntent amount is invalid")

    return {
        "source_name": "stripe",
        "source_entity_type": "payment_intent",
        "external_id": str(external_id),
        "amount_minor": amount_minor,
        "currency": str(currency).upper(),
        "raw_status": str(raw_status),
        "occurred_at": _stripe_created_to_datetime(created),
        "customer_reference": _optional_string(raw_payment_intent.get("customer")),
        "raw_payload": raw_payment_intent,
    }


def _date_range_to_datetimes(from_date: date, to_date: date) -> tuple[datetime, datetime]:
    return (
        datetime.combine(from_date, time.min, tzinfo=UTC),
        datetime.combine(to_date + timedelta(days=1), time.min, tzinfo=UTC),
    )


def _stripe_created_to_datetime(created: Any) -> datetime:
    if isinstance(created, int | float):
        return datetime.fromtimestamp(created, tz=UTC)
    raise ValueError("Stripe PaymentIntent created timestamp is invalid")


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _seed_raw_payload(record: dict[str, Any]) -> dict[str, Any]:
    occurred_at = record["occurred_at"]
    return {
        **record,
        "seed_source": "problem_2_demo",
        "occurred_at": (
            occurred_at.isoformat() if isinstance(occurred_at, datetime) else occurred_at
        ),
    }


def _last_dict_item_id(items: list[Any]) -> str | None:
    for item in reversed(items):
        if isinstance(item, dict) and item.get("id") is not None:
            return str(item["id"])
    return None
