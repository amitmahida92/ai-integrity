# ai-integrity

Problem Statement 1 implementation: a synchronous FastAPI ingestion service that
syncs HubSpot Contacts, Google Calendar Events, and Stripe PaymentIntents into
one normalized PostgreSQL schema.

Problem Statement 2 adds one collected-revenue metric using normalized
financial records and an explicit status allow-list. This repository does not
add workers, Redis, queues, cron, webhooks, FX conversion, an accounting ledger,
provider-specific tables, auth UI, or broad production infrastructure outside
the assignment scope.

Live Render URL: https://ai-integrity.onrender.com

## Architecture

- FastAPI exposes the sync, records, run-history, health, and readiness APIs.
- SQLAlchemy 2 repositories persist normalized records, provider checkpoints,
  sync-run history, and per-provider source results.
- Alembic owns the PostgreSQL schema.
- Provider adapters isolate HubSpot, Google Calendar, and Stripe API semantics
  from orchestration and persistence.
- The orchestrator runs requested providers sequentially inside one API request,
  but each provider has independent error handling, checkpointing, and audit
  result persistence.
- Docker startup runs `alembic upgrade head` before Uvicorn. If migrations fail,
  the container fails before serving traffic.

## Provider Scope

- HubSpot: Contacts
- Google Calendar: Events from the configured `GOOGLE_CALENDAR_ID`
- Stripe: PaymentIntents

## API Surface

Protected `/api/v1` routes require:

```http
Authorization: Bearer $ADMIN_API_KEY
```

Implemented routes used for the submission:

- `POST /api/v1/sync`
- `GET /api/v1/records/counts`
- `GET /api/v1/sync-runs`
- `POST /api/v1/problem-2/seed`
- `POST /api/v1/problem-2/import-stripe`
- `GET /api/v1/metrics/revenue/summary`
- `GET /api/v1/metrics/revenue/breakdown`
- `GET /health`
- `GET /ready`

`/health` is lightweight and checks database reachability. `/ready` checks
database reachability and required table existence.

## Data Model

The design uses one normalized table rather than provider-specific tables.
Provider-specific details live in JSONB columns.

`normalized_records` columns:

- `id`
- `provider`
- `entity_type`
- `external_id`
- `source_updated_at`
- `canonical_data`
- `raw_payload`
- `payload_hash`
- `first_seen_at`
- `last_seen_at`

Uniqueness:

```text
UNIQUE(provider, entity_type, external_id)
```

Entity types:

- HubSpot: `provider=hubspot`, `entity_type=contact`
- Google Calendar: `provider=google_calendar`, `entity_type=calendar_event`
- Stripe: `provider=stripe`, `entity_type=payment_intent`

Supporting tables:

- `sync_checkpoints`
- `sync_runs`
- `sync_source_results`

Problem Statement 2 adds:

`normalized_financial_records`:

- `id`
- `source_name`
- `source_entity_type`
- `external_id`
- `amount_minor`
- `currency`
- `raw_status`
- `occurred_at`
- `customer_reference`
- `raw_payload`
- `created_at`
- `updated_at`

Uniqueness:

```text
UNIQUE(source_name, source_entity_type, external_id)
```

`revenue_status_allowlist`:

- `id`
- `source_name`
- `source_entity_type`
- `raw_status`
- `canonical_status`
- `counts_as_collected`
- `created_at`

Only statuses present in this allow-list with `counts_as_collected=true` are
included in collected revenue.

## Idempotency Strategy

Writes use PostgreSQL `INSERT ... ON CONFLICT DO UPDATE` on
`provider + entity_type + external_id`. Rerunning the same full or incremental
sync updates the existing row instead of inserting duplicates.

Rows keep both `canonical_data` and `raw_payload`. `source_updated_at` prevents
older provider versions from overwriting newer stored state while still updating
`last_seen_at` when a duplicate is observed again.

## Checkpoint Strategy

Checkpoints are stored per provider in `sync_checkpoints.checkpoint_data`.
Provider records and checkpoint updates are committed together only after a
provider succeeds. If persistence fails, the provider checkpoint is not advanced.

- HubSpot full sync paginates Contacts and stores a `watermark` from the maximum
  source update timestamp seen. Incremental sync searches contacts modified
  since the saved watermark minus a 120-second overlap window.
- Google Calendar full sync stores `calendar_id` and `nextSyncToken`.
  Incremental sync uses the saved `sync_token`.
- Stripe full sync paginates PaymentIntents and stores an `event_watermark`.
  Incremental sync reads Stripe Events from the watermark minus a 120-second
  overlap window, filters PaymentIntent event types, fetches current
  PaymentIntent state, and upserts that current state.

## Google Calendar 410 Fallback

If Google Calendar returns HTTP `410` for a stale sync token, the adapter treats
that as an expired cursor and runs a recovery full sync. The source result is
recorded with `effective_mode=recovery_full`, replacement records are persisted,
and the replacement `nextSyncToken` is checkpointed only after the recovery
records are durably written.

## Partial Failure Isolation

Each provider has its own `sync_source_results` row. A provider failure is
recorded for that provider and does not prevent later providers from running.

Run status is derived from source results:

- all providers succeed: `succeeded`
- some providers fail: `completed_with_errors`
- all requested providers fail: `failed`

The public sync response maps those outcomes into the API response status while
still returning HTTP `200` for a partially failed run.

Malformed provider items are counted as rejected records and valid records on
the same page continue processing.

## Problem Statement 2: Collected Revenue

Metric name: `collected_revenue`

Metric version: `v1_allowlist`

Definition:

```sql
SUM(normalized_financial_records.amount_minor)
JOIN revenue_status_allowlist
  ON source_name, source_entity_type, raw_status
WHERE counts_as_collected = true
  AND occurred_at >= from_date
  AND occurred_at < to_date + 1 day
GROUP BY currency
```

The implementation uses `RevenueMetricsService` as the single canonical place
for the collected-revenue query. The summary endpoint and breakdown endpoint
both call that service, so they expose the same aggregate number.

Seeded allow-list rows:

- `stripe/payment_intent/succeeded`
- `mock_finance/invoice/paid`
- `mock_finance/payment/completed`

The seeded mock financial data includes paid, completed, pending, failed,
voided, refunded, and unknown statuses. Only `paid` and `completed` count until
another status is explicitly allow-listed.

Endpoints:

```bash
curl -X POST "$BASE_URL/api/v1/problem-2/seed" \
  -H "Authorization: Bearer $ADMIN_API_KEY"

curl -X POST "$BASE_URL/api/v1/problem-2/import-stripe" \
  -H "Authorization: Bearer $ADMIN_API_KEY"

curl "$BASE_URL/api/v1/metrics/revenue/summary?from_date=2026-06-01&to_date=2026-06-30" \
  -H "Authorization: Bearer $ADMIN_API_KEY"

curl "$BASE_URL/api/v1/metrics/revenue/breakdown?from_date=2026-06-01&to_date=2026-06-30&grain=day" \
  -H "Authorization: Bearer $ADMIN_API_KEY"
```

The seed and Stripe import paths use PostgreSQL upserts, so reruns update the
same source records instead of duplicating them.

## Local Setup

Copy the example environment file:

```bash
cp .env.example .env
```

Set local credentials in `.env` when making live provider calls:

```bash
APP_ENV=local
DATABASE_URL=postgresql+psycopg://ai_integrity:ai_integrity@localhost:5433/ai_integrity
ADMIN_API_KEY=
DEMO_FAILURE_INJECTION_ENABLED=true
HUBSPOT_ACCESS_TOKEN=
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REFRESH_TOKEN=
GOOGLE_CALENDAR_ID=
STRIPE_SECRET_KEY=
```

Start PostgreSQL:

```bash
docker compose up -d db
```

Run the API:

```bash
docker compose up --build api
```

Check health and readiness:

```bash
curl http://localhost:8000/health
curl http://localhost:8000/ready
```

Run a full sync:

```bash
curl -X POST http://localhost:8000/api/v1/sync \
  -H "Authorization: Bearer $ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"mode":"full"}'
```

Read counts and recent runs:

```bash
curl http://localhost:8000/api/v1/records/counts \
  -H "Authorization: Bearer $ADMIN_API_KEY"

curl http://localhost:8000/api/v1/sync-runs \
  -H "Authorization: Bearer $ADMIN_API_KEY"
```

## Render Deployment

1. Create a Render Web Service.
2. Connect the GitHub repository.
3. Choose Docker deployment.
4. Create or attach Render PostgreSQL or another hosted PostgreSQL database.
5. Set `DATABASE_URL` from the hosted database.
6. Set the required provider credentials and admin key.
7. Deploy.
8. Verify `/health` and `/ready`.
9. Run one full sync and inspect record counts.

Required Render environment variables, without values:

```bash
APP_ENV=production
DATABASE_URL=
ADMIN_API_KEY=
DEMO_FAILURE_INJECTION_ENABLED=true
HUBSPOT_ACCESS_TOKEN=
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REFRESH_TOKEN=
GOOGLE_CALENDAR_ID=
STRIPE_SECRET_KEY=
```

Render supplies `PORT`. The Docker command binds Uvicorn to
`0.0.0.0:${PORT:-8000}` after `alembic upgrade head` succeeds. Plain Render
PostgreSQL URLs beginning with `postgresql://` are normalized by both the app
runtime and Alembic to use the psycopg v3 SQLAlchemy driver.

Health check path:

```text
/health
```

Readiness path:

```text
/ready
```

## Render Curl Examples

Set:

```bash
BASE_URL=https://ai-integrity.onrender.com
```

Health and readiness:

```bash
curl "$BASE_URL/health"
curl "$BASE_URL/ready"
```

Run a full sync:

```bash
curl -X POST "$BASE_URL/api/v1/sync" \
  -H "Authorization: Bearer $ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"mode":"full"}'
```

Get record counts:

```bash
curl "$BASE_URL/api/v1/records/counts" \
  -H "Authorization: Bearer $ADMIN_API_KEY"
```

List recent sync runs:

```bash
curl "$BASE_URL/api/v1/sync-runs" \
  -H "Authorization: Bearer $ADMIN_API_KEY"
```

Repeatable verification script:

```bash
./scripts/verify_render.sh "$BASE_URL" "$ADMIN_API_KEY"
```

The script calls `/health`, `/ready`, runs a full sync, gets counts, runs the
full sync again, gets counts again, and lists sync runs. It prints counts so
idempotency is visible and does not print secret values.

## Live Verification Evidence

Evidence files are stored under `docs/evidence/`.

Recorded live Render checks:

- `/health`: `{"status":"ok","database":"reachable"}`
- `/ready`: `{"status":"ready","database":"reachable","tables":"ready"}`
- `POST /api/v1/sync` with `{"mode":"full"}` succeeded for all three providers.
- Full-sync evidence: HubSpot fetched/upserted 5 contacts, Google Calendar
  fetched/upserted 120 events, Stripe fetched/upserted 3 PaymentIntents.
- `GET /api/v1/records/counts` returned total `128`:
  - `google_calendar/calendar_event`: 120
  - `hubspot/contact`: 5
  - `stripe/payment_intent`: 3

Local verification for the submission:

```text
python -m ruff check .    # passed
python -m pytest -v       # 45 passed, 1 warning
docker build              # passed
```

## Known Trade-offs

- Sync is synchronous by design for Problem Statement 1; long provider calls keep
  the API request open.
- Providers run sequentially, not in parallel.
- Google Calendar uses one configured refresh token for the seeded interview
  calendar rather than a multi-user OAuth consent flow.
- The schema intentionally uses one normalized record table rather than
  provider-specific read models.
- Problem Statement 2 does not implement FX conversion, refunds netting,
  accounting ledger semantics, workers, queues, cron jobs, Redis, or webhooks.
- The API stores raw provider payloads for review/debugging, but error summaries
  are sanitized to avoid logging tokens, secrets, API keys, or bearer values.

## AI Usage Disclosure

AI assistance was used to draft implementation code, tests, documentation, and
review checklists. The resulting behavior was verified with the repository's
lint/test suite, Docker build, local database-backed tests, and live Render API
checks captured in `docs/evidence/`.
