# Problem Statement 2 Evidence

## Implemented Scope

This repository implements the minimum collected-revenue vertical slice:

- `normalized_financial_records` for normalized money-bearing records.
- `revenue_status_allowlist` for source/status mapping.
- `POST /api/v1/problem-2/seed` for idempotent allow-list and mock record seed data.
- `POST /api/v1/problem-2/import-stripe` for idempotent Stripe PaymentIntent import.
- `GET /api/v1/metrics/revenue/summary`.
- `GET /api/v1/metrics/revenue/breakdown`.
- `/ready` table checks now include the two Problem Statement 2 tables.

The collected-revenue rule is implemented in `RevenueMetricsService`. API
endpoints do not implement metric SQL directly.

## Metric Definition

Collected revenue is the sum of `amount_minor` grouped by currency for records
whose `(source_name, source_entity_type, raw_status)` joins to
`revenue_status_allowlist` with `counts_as_collected=true`.

The date window is inclusive of `from_date` and inclusive of `to_date` by
querying:

```text
occurred_at >= from_date at 00:00:00 UTC
occurred_at < to_date + 1 day at 00:00:00 UTC
```

Unknown statuses do not count until a row is added to the allow-list.

## Test Evidence

Local verification:

```text
.venv/bin/python -m ruff check .  # passed
.venv/bin/python -m pytest -v     # 45 passed, 1 warning
```

Added high-value tests cover:

- Seed endpoint idempotency.
- Exclusion of pending, failed, voided, refunded, and unknown statuses.
- Summary and breakdown aggregate agreement.
- Unknown new source/status not counting until allow-listed.
- Summary and breakdown both changing consistently after allow-list insertion.
- Revenue API endpoints delegating to `RevenueMetricsService`.

## Live Verification Commands

Set:

```bash
BASE_URL=https://ai-integrity.onrender.com
```

Seed demo financial data:

```bash
curl -X POST "$BASE_URL/api/v1/problem-2/seed" \
  -H "Authorization: Bearer $ADMIN_API_KEY"
```

Get summary:

```bash
curl "$BASE_URL/api/v1/metrics/revenue/summary?from_date=2026-06-01&to_date=2026-06-30" \
  -H "Authorization: Bearer $ADMIN_API_KEY"
```

Get daily breakdown:

```bash
curl "$BASE_URL/api/v1/metrics/revenue/breakdown?from_date=2026-06-01&to_date=2026-06-30&grain=day" \
  -H "Authorization: Bearer $ADMIN_API_KEY"
```

Import Stripe PaymentIntents:

```bash
curl -X POST "$BASE_URL/api/v1/problem-2/import-stripe" \
  -H "Authorization: Bearer $ADMIN_API_KEY"
```

Re-run summary and breakdown after import to confirm both expose matching
aggregate totals by currency.

## Known Trade-offs

- Revenue is reported in source currencies only; there is no FX conversion.
- Refunds and disputes are not netted into a ledger.
- The implementation imports current Stripe PaymentIntent state and does not add
  webhooks, workers, queues, or scheduled jobs.
- Breakdown currently supports `grain=day` only.
- The mock seed exists to make the allow-list behavior demonstrable without
  relying on live provider data.
