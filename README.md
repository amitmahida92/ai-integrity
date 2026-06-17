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

2. Start PostgreSQL:

   ```bash
   docker compose up -d db
   ```

   Compose maps Postgres to `localhost:5433` by default to avoid colliding with a local
   Postgres on `5432`.

3. Apply migrations:

   ```bash
   docker compose run --rm api alembic upgrade head
   ```

4. Run the API:

   ```bash
   docker compose up api
   ```

5. Verify service health:

   ```bash
   curl http://localhost:8000/health
   curl http://localhost:8000/ready
   ```

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
