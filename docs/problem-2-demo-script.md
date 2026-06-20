# Problem Statement 2 Demo Script

Target length: 4-5 minutes.

Do not show `.env`, Render environment variables, provider secrets, API keys, or
shell history containing secret values. Use `Authorization: Bearer $ADMIN_API_KEY`
in commands, but never print the key value.

## Setup Before Recording

Open a terminal with:

```bash
export BASE_URL=https://ai-integrity.onrender.com
export ADMIN_API_KEY=...
```

Use this live script for the terminal portion:

```bash
./scripts/demo_problem2_live.sh
```

The script uses the fixed date range `2026-06-01` through `2026-06-30`, which
matches the deterministic seed records.

## 0:00-0:30 - Frame The Metric

What to show on screen:

- `README.md` Problem Statement 2 section, or this file.
- Avoid any secret-bearing environment view.

Narration:

> Problem Statement 2 asks for one revenue number that does not drift between
> views. I implemented collected revenue as a normalized financial-record table
> plus a status allow-list. The important point is that the metric includes
> statuses by explicit allow-list join, not by trying to exclude every bad
> status.

Expected highlight:

- Metric name: `collected_revenue`
- Metric version: `v1_allowlist`
- Included statuses are explicitly allow-listed.

## 0:30-1:00 - Health And Readiness

Exact commands:

```bash
curl "$BASE_URL/health"
curl "$BASE_URL/ready"
```

Expected output highlights:

- `/health` returns `status=ok`.
- `/ready` returns `status=ready` and confirms required tables exist.

Narration:

> I am starting with the live Render service. Health is intentionally light, and
> readiness verifies the database and required tables, including the Problem 2
> tables.

## 1:00-1:45 - Seed Deterministic Financial Data

Exact command:

```bash
curl -X POST "$BASE_URL/api/v1/problem-2/seed" \
  -H "Authorization: Bearer $ADMIN_API_KEY"
```

Expected output highlights:

- `status=seeded`
- `allowlist_rows=3`
- `financial_records=7`

Narration:

> The seed endpoint is deterministic and idempotent. It inserts the allow-list
> rows and seven mock finance records. The records include paid, completed,
> pending, failed, voided, refunded, and unknown statuses. Only paid and
> completed are counted by default because they are the only mock statuses in
> the allow-list.

## 1:45-2:15 - Import Stripe PaymentIntents

Exact command:

```bash
curl -X POST "$BASE_URL/api/v1/problem-2/import-stripe" \
  -H "Authorization: Bearer $ADMIN_API_KEY"
```

Expected output highlights if available:

- `status=imported`
- `imported_records`
- `rejected_records`
- `pages_fetched`

Fallback narration if Stripe import is slow or unavailable:

> Stripe is the real test-mode finance source. If the live provider import is
> unavailable during the demo, the deterministic mock finance source still
> demonstrates the metric invariant and the multi-status allow-list behavior.
> The implementation keeps Stripe import idempotent and uses the same normalized
> financial schema.

## 2:15-3:15 - Revenue Summary

Exact command:

```bash
curl "$BASE_URL/api/v1/metrics/revenue/summary?from_date=2026-06-01&to_date=2026-06-30" \
  -H "Authorization: Bearer $ADMIN_API_KEY"
```

Expected output highlights:

- `metric_name=collected_revenue`
- `metric_version=v1_allowlist`
- `totals_by_currency`

Narration:

> This is the headline collected-revenue number. The query joins
> `normalized_financial_records` to `revenue_status_allowlist`, so unknown,
> pending, failed, voided, and refunded statuses are excluded by default rather
> than accidentally counted.

## 3:15-4:00 - Revenue Breakdown And Invariant

Exact command:

```bash
curl "$BASE_URL/api/v1/metrics/revenue/breakdown?from_date=2026-06-01&to_date=2026-06-30&grain=day" \
  -H "Authorization: Bearer $ADMIN_API_KEY"
```

Expected output highlights:

- `buckets`
- `aggregate_totals_by_currency`
- `aggregate_totals_by_currency` equals summary `totals_by_currency`

Narration:

> The daily breakdown uses the same service-level collected-revenue query. The
> aggregate total in this response must match the summary total for the exact
> same date range. The live script checks that equality and prints PASS or FAIL.

## 4:00-4:30 - Tests

Exact commands:

```bash
.venv/bin/python -m ruff check .
.venv/bin/python -m pytest -v
```

Expected output highlights:

- Ruff passes.
- Pytest passes.
- Current local result: `45 passed, 1 warning`.

Narration:

> The tests cover seed idempotency, default exclusion of pending, failed, voided,
> refunded, and unknown statuses, summary-to-breakdown agreement, and behavior
> when a new source status is added to the allow-list.

## 4:30-5:00 - Trade-Offs

Narration:

> The trade-off is intentionally narrow. Stripe is the real test-mode finance
> source. Mock finance is a deterministic second status vocabulary source so I
> can demonstrate multi-source status mapping within the interview time. I did
> not add FX conversion, refund ledgering, webhooks, workers, queues, Redis, or
> cron because those are outside this focused vertical slice.
