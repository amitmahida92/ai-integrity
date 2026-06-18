# ai-integrity

Problem Statement 1 foundation for a synchronous data sync API. The project is intentionally
small: FastAPI, SQLAlchemy 2, Alembic, PostgreSQL, Pydantic Settings, Pytest, Ruff, and Docker
Compose for local PostgreSQL.

This pass does not implement workers, queues, Redis, an outbox, DLQ, webhooks, auth UI, or Problem
Statement 2 metrics.

## Local setup

1. Copy environment defaults:

   ```bash
   cp .env.example .env
   ```

2. Fill local provider credentials in `.env` when you want live provider calls:

   ```bash
   HUBSPOT_ACCESS_TOKEN=...
   GOOGLE_CLIENT_ID=...
   GOOGLE_CLIENT_SECRET=...
   GOOGLE_REFRESH_TOKEN=...
   GOOGLE_CALENDAR_ID=...
   STRIPE_SECRET_KEY=...
   ```

   The Google Calendar runtime uses the client id, client secret and refresh
   token to obtain an access token before Calendar API calls. Do not use a
   static Google access token or API key for private calendar data.
   `scripts/generate_google_token.py` writes `GOOGLE_REFRESH_TOKEN` to ignored
   `.env` without printing the token.

3. Start PostgreSQL:

   ```bash
   docker compose up -d db
   ```

   Compose maps Postgres to `localhost:5433` by default to avoid colliding with a local
   Postgres on `5432`.

4. Run the API:

   ```bash
   docker compose up --build api
   ```

   The container runs `alembic upgrade head` before starting Uvicorn. If
   migrations fail, startup fails.

5. Verify service health and readiness:

   ```bash
   curl http://localhost:8000/health
   curl http://localhost:8000/ready
   ```

## Render deployment

Deploy the Docker image as a Render Web Service and point `DATABASE_URL` at the
Supabase Postgres database using the SQLAlchemy psycopg driver form:

```text
postgresql+psycopg://USER:PASSWORD@HOST:PORT/DATABASE
```

Required Render environment variables:

```text
APP_ENV=production
DATABASE_URL=postgresql+psycopg://...
DEBUG_SYNC_TOOLS_ENABLED=false
DEMO_FAILURE_INJECTION_ENABLED=false
HUBSPOT_ACCESS_TOKEN=...
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
GOOGLE_REFRESH_TOKEN=...
GOOGLE_CALENDAR_ID=...
STRIPE_SECRET_KEY=...
```

Render supplies `PORT`. The Docker command binds Uvicorn to
`0.0.0.0:${PORT:-8000}`, runs `alembic upgrade head` first, and exits without
starting the API if migrations fail. `/ready` checks that the database is
reachable and that `normalized_records`, `sync_checkpoints`, `sync_runs` and
`sync_source_results` exist.

## Verification

Run linting and tests inside the Python 3.12 API image:

```bash
docker compose run --rm api ruff check .
docker compose run --rm api pytest
```

The tests use PostgreSQL because JSONB and `INSERT ... ON CONFLICT DO UPDATE` behavior are part of
the implementation contract.

## Data model

The foundation migration creates:

- `normalized_records`
- `sync_checkpoints`
- `sync_runs`
- `sync_source_results`

`normalized_records` enforces uniqueness on `provider + entity_type + external_id`. Repository
upserts keep repeated full syncs and incremental overlap windows idempotent, and older provider
versions cannot overwrite newer canonical state.

The normalized-record design is intentional. One table stores all synced
entities with `provider`, `entity_type`, `external_id`, `canonical_data` and
`raw_payload`; provider-specific facts live in `canonical_data` while the source
object remains available in `raw_payload`.
