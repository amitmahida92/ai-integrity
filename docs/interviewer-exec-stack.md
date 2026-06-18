# Interviewer Execution Stack

This document is the interviewer-facing walkthrough for Problem Statement 1:
a synchronous sync pipeline that ingests HubSpot Contacts, Google Calendar
Events, and Stripe PaymentIntents into a normalized PostgreSQL store without
duplicating data or losing checkpoints on failure.

## Scope

Implemented:

- Python 3.12 FastAPI API.
- SQLAlchemy 2 repositories and PostgreSQL persistence.
- Alembic migrations.
- Pydantic Settings configuration.
- Docker Compose local PostgreSQL.
- Provider adapters for HubSpot, Google Calendar, and Stripe.
- Sync orchestration with per-provider failure isolation.
- Idempotent normalized record upserts.
- Per-provider JSONB checkpoints.
- Sync-run and per-source audit history.
- Demo-only failure injection for the required edge cases.
- PostgreSQL-backed automated tests proving the main correctness guarantees.

Intentionally not implemented:

- Problem Statement 2 metrics.
- Workers, queues, Redis, outbox, DLQ, webhooks, auth UI, or cross-source
  identity resolution.

## Commit Stack

Current local commit stack:

```text
d73c4fd test: prove sync correctness and failure isolation
c7dee86 feat: add isolated sync orchestration and APIs
72c51b8 feat: add HubSpot Calendar and Stripe adapters
3f42570 chore: bootstrap sync pipeline foundation
69729c2 Initial commit
```

## Runtime Stack

The service runs as a small synchronous backend:

```text
HTTP request
  -> FastAPI route
  -> SyncOrchestrator
  -> provider adapter
  -> provider client
  -> normalized records
  -> SQLAlchemy repository
  -> PostgreSQL JSONB tables and upserts
```

Local runtime pieces:

- `Dockerfile`: Python 3.12 production-compatible container.
- `docker-compose.yml`: local PostgreSQL 16 plus API container.
- `.env.example`: local settings template.
- `app/core/config.py`: Pydantic Settings.
- `app/db/session.py`: SQLAlchemy engine and session lifecycle.
- `alembic/versions/*`: database schema migrations.

## API Surface

Health and readiness:

- `GET /health`
- `GET /ready`

Sync API:

- `POST /api/v1/sync`
- `POST /api/v1/sync/{provider}`
- `GET /api/v1/sync-runs`
- `GET /api/v1/sync-runs/{run_id}`
- `GET /api/v1/records`
- `GET /api/v1/records/counts`

Supported providers:

- `hubspot`
- `google_calendar`
- `stripe`

Supported sync modes:

- `full`
- `incremental`

## Data Model

The normalized ingestion surface is centered on:

- `normalized_records`
- `sync_checkpoints`
- `sync_runs`
- `sync_source_results`

`normalized_records` stores:

- `provider`
- `entity_type`
- `external_id`
- `source_updated_at`
- `canonical_data`
- `raw_payload`
- `payload_hash`
- `first_seen_at`
- `last_seen_at`

The database enforces uniqueness on:

```text
provider + entity_type + external_id
```

That constraint is important because the sync pipeline can safely reprocess
overlap windows or repeated full syncs without creating duplicate normalized
rows.

`sync_checkpoints.checkpoint_data` is JSONB so each provider can keep the
checkpoint shape it actually needs:

- HubSpot: timestamp watermark plus overlap window.
- Google Calendar: calendar ID plus `nextSyncToken`.
- Stripe: event watermark, overlap window, and last event ID.

## Provider Adapter Behavior

### HubSpot Contacts

Implemented in `app/sources/hubspot.py`.

Behavior:

- Full fetch uses HubSpot contact pagination.
- Incremental fetch uses `lastmodifieddate`.
- Incremental fetch applies an overlap window to avoid boundary and indexing
  delay loss.
- Durable checkpoint is based on the maximum successfully persisted provider
  modification timestamp.
- Page cursors are only used inside a single run and are not persisted as the
  incremental checkpoint.
- Individual malformed contacts are rejected and counted without failing the
  whole page.
- Raw payload is preserved in `normalized_records.raw_payload`.

### Google Calendar Events

Implemented in `app/sources/google_calendar.py`.

Behavior:

- Initial full sync paginates through events.
- Successful full sync persists the final `nextSyncToken`.
- Incremental sync uses `syncToken` and paginates incremental results.
- Cancelled Google events are normalized as deleted tombstones.
- HTTP 410 / expired sync token is isolated as `GoogleSyncTokenExpired`.
- Expired token automatically falls back to a full sync with effective mode
  `recovery_full`.
- Replacement sync token is persisted only after fallback records are durably
  stored.

### Stripe PaymentIntents

Implemented in `app/sources/stripe.py`.

Behavior:

- Full sync paginates PaymentIntents.
- Incremental sync reads Stripe Events instead of using PaymentIntent created
  time as an update checkpoint.
- Relevant PaymentIntent events cause the adapter to fetch the current
  PaymentIntent state.
- Event ID is preserved in checkpoint data where useful.
- PaymentIntent facts are normalized without implementing revenue rules.
- Raw payload is preserved.

## Orchestration Flow

Implemented in `app/sync/orchestrator.py`.

For each sync request:

1. Create one overall `sync_runs` row.
2. Acquire PostgreSQL advisory locks for requested providers with
   `pg_try_advisory_lock`.
3. For every requested provider, create one `sync_source_results` row.
4. Fetch provider data through the provider adapter.
5. Persist normalized records through PostgreSQL upserts.
6. Advance the provider checkpoint only in the same successful persistence
   transaction.
7. Mark the provider source result as succeeded or failed.
8. Finish the overall run as:
   - `succeeded` when all providers succeed.
   - `completed_with_errors` when some succeed and some fail.
   - `failed` when all requested providers fail.

Provider failures are caught at the provider boundary, so one failing provider
does not block the others.

## Correctness Guarantees

### Idempotency

The repository uses PostgreSQL `INSERT ... ON CONFLICT DO UPDATE` against the
unique normalized record key:

```text
provider + entity_type + external_id
```

This proves:

- Repeating a full sync does not increase row count.
- Repeating an incremental sync does not create duplicate rows.
- A changed source object with the same provider/entity/external ID updates the
  existing normalized row.

### Stale Update Protection

Upserts compare `source_updated_at` before replacing canonical/raw data. Older
provider versions cannot overwrite a newer stored version, though `last_seen_at`
still moves forward to show the object was observed again.

### Checkpoint Safety

Checkpoint updates happen in the same transaction as record persistence. If the
record write fails, the transaction rolls back and the checkpoint remains
unchanged.

### Failure Isolation

Each provider is executed independently. If one provider fails:

- Its source result is marked failed.
- Its checkpoint is not advanced.
- Other providers still fetch, persist records, and advance checkpoints.
- The overall run returns `partial_success` at the API layer.

### Malformed Record Isolation

Provider adapters validate individual records. Bad records are rejected and
counted, while valid records from the same provider and other providers continue
processing.

## Demo Failure Injection

Failure injection is guarded by:

```text
DEMO_FAILURE_INJECTION_ENABLED
```

When disabled, debug injection options are rejected safely.

When enabled, the API can simulate:

- One provider unavailable.
- Malformed records from one provider.
- Google Calendar expired sync token.

This is isolated in the orchestration layer and does not require real provider
clients to fail.

## Automated Evidence

The acceptance mapping lives in:

```text
docs/test-evidence.md
```

The strongest correctness tests are in:

```text
tests/test_sync_acceptance.py
```

Those tests run through:

- FastAPI routes.
- The real sync orchestrator.
- Real SQLAlchemy repositories.
- Real PostgreSQL schema and JSONB columns.
- Real PostgreSQL unique constraint and upsert behavior.
- Deterministic fake provider clients instead of live provider APIs.

The seven focused acceptance tests prove:

1. Repeating a full sync does not increase normalized row count.
2. Repeating an incremental sync does not create duplicate rows.
3. Changed source objects update existing normalized rows.
4. Google expired-token fallback persists replacement token only after records.
5. Persistence failure leaves checkpoints unchanged.
6. One provider failure still allows the other two providers to persist data.
7. Malformed provider records are rejected and counted without blocking valid
   records.

Latest verification:

```text
python -m ruff check .
python -m pytest -v
```

Result:

```text
36 passed, 1 warning
```

The warning is an upstream FastAPI/Starlette `TestClient` deprecation warning
about `httpx`; it is not a sync pipeline correctness failure.

## Local Runbook

Start PostgreSQL:

```bash
docker compose up -d db
```

Start the API:

```bash
docker compose up api
```

The API container runs `alembic upgrade head` before starting Uvicorn.

Health checks:

```bash
curl http://localhost:8000/health
curl http://localhost:8000/ready
```

Run linting and tests:

```bash
docker compose run --rm api ruff check .
docker compose run --rm api pytest
```

Trigger a full sync for all providers:

```bash
curl -X POST http://localhost:8000/api/v1/sync \
  -H 'Content-Type: application/json' \
  -d '{"mode":"full"}'
```

Trigger an incremental sync for one provider:

```bash
curl -X POST http://localhost:8000/api/v1/sync/google_calendar \
  -H 'Content-Type: application/json' \
  -d '{"mode":"incremental"}'
```

Review records and runs:

```bash
curl http://localhost:8000/api/v1/records/counts
curl http://localhost:8000/api/v1/sync-runs
```

## Interview Walkthrough

Suggested narrative:

1. Start with the problem: the hard part is not calling APIs, it is making the
   data pipeline truthful under retries, stale cursors, partial provider
   failure, malformed records, and repeated runs.
2. Show the normalized table and the unique key.
3. Show the PostgreSQL upsert and stale-version guard.
4. Show provider-specific checkpoint semantics:
   - HubSpot timestamp watermark.
   - Google `nextSyncToken`.
   - Stripe Events watermark.
5. Show the orchestrator:
   - one overall run,
   - one source result per provider,
   - provider boundary exception handling,
   - checkpoint advancement after durable persistence,
   - advisory lock protection.
6. Show `docs/test-evidence.md` and the acceptance tests.
7. Run `docker compose run --rm api pytest` to prove the behavior.

## Known Limits

- Live provider credential flows are intentionally minimal for the assignment.
  Google Calendar uses one configured refresh token for the seeded calendar.
- Automated tests use mocked provider clients for deterministic behavior.
- No background scheduling or async worker model is included.
- No production OAuth UI is included.
- Problem Statement 2 revenue metrics are intentionally excluded.
