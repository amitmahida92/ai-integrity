# Final 5-Minute Demo Script

Goal: show the deployed Problem Statement 1 API working live, with observable
evidence for readiness, full sync, idempotency, run history, and one controlled
failure/edge case.

Use this command before recording:

```bash
export BASE_URL=https://ai-integrity.onrender.com
export ADMIN_API_KEY=...
./scripts/demo_live.sh
```

The script never prints `ADMIN_API_KEY`. For a shorter pass without the optional
failure step:

```bash
RUN_DEMO_FAILURE=0 ./scripts/demo_live.sh
```

## 0:00-0:30 - Setup and Scope

Narration:

> This is the Problem Statement 1 backend. It is a synchronous FastAPI service
> deployed on Render with hosted PostgreSQL. It syncs three provider scopes:
> HubSpot Contacts, Google Calendar Events, and Stripe PaymentIntents. I did not
> implement Problem Statement 2, workers, queues, webhooks, Redis, or
> provider-specific tables.

On screen:

- Show the terminal with `BASE_URL` set.
- Run `./scripts/demo_live.sh`.
- Point out that the script confirms the API key is set but does not print it.

## 0:30-1:00 - Health and Readiness

Script sections:

- `1. Lightweight health check`
- `2. Readiness check: database and required tables`

Narration:

> `/health` is intentionally lightweight. `/ready` goes deeper: it verifies the
> database is reachable and that the required tables exist:
> `normalized_records`, `sync_checkpoints`, `sync_runs`, and
> `sync_source_results`.

Expected result:

```json
{"status":"ok","database":"reachable"}
```

```json
{"status":"ready","database":"reachable","tables":"ready"}
```

## 1:00-2:00 - First Full Sync

Script section:

- `3. First full sync across HubSpot, Google Calendar, and Stripe`

Narration:

> This calls `POST /api/v1/sync` with `{"mode":"full"}`. The API runs the three
> providers in one synchronous request. Each provider produces its own source
> result with fetched, upserted, rejected, and fallback fields.

What to point out:

- `requested_providers` includes `hubspot`, `google_calendar`, and `stripe`.
- Each provider has `status: "success"` in the normal path.
- Google events and Stripe PaymentIntents are included in the same normalized
  record design as HubSpot contacts.

## 2:00-2:45 - Counts and Normalized Schema

Script section:

- `4. Record counts after first full sync`

Narration:

> The count endpoint reads from one normalized table. The uniqueness key is
> provider, entity type, and external ID. Provider-specific data is stored in
> `canonical_data`, and the original source object is retained in `raw_payload`.

Expected live shape:

```json
{
  "total": 128,
  "counts": [
    {"provider": "google_calendar", "entity_type": "calendar_event", "count": 120},
    {"provider": "hubspot", "entity_type": "contact", "count": 5},
    {"provider": "stripe", "entity_type": "payment_intent", "count": 3}
  ]
}
```

Counts can change if the seeded provider accounts change. The important point
is that the second count section should match the first count section.

## 2:45-3:30 - Idempotency

Script sections:

- `5. Second full sync to show idempotency`
- `6. Record counts after second full sync`

Narration:

> The second full sync intentionally repeats the same operation. PostgreSQL
> upserts on `provider + entity_type + external_id`, so reruns update existing
> rows instead of creating duplicates. The counts after the second full sync
> should match the counts after the first full sync.

What to point out:

- The second sync creates a new sync-run audit record.
- The normalized row counts remain stable.
- This is the core duplicate-protection behavior for reruns.

## 3:30-4:05 - Sync Run History

Script section:

- `7. Recent sync runs`

Narration:

> Sync runs and per-provider source results are persisted. This gives an audit
> trail of requested mode, providers, per-source status, effective mode, fetched
> count, upserted count, rejected count, fallback flags, and sanitized errors.

What to point out:

- Recent runs appear without querying provider-specific tables.
- `effective_mode` can show `full`, `incremental`, or `recovery_full`.
- Error summaries are sanitized and do not expose tokens or secrets.

## 4:05-4:45 - Controlled Failure / Edge Case

Script section:

- `8. Optional demo failure injection: HubSpot fails, other providers continue`

Narration if enabled:

> This is a controlled demo failure. The request injects a HubSpot failure while
> still requesting all three providers. The run becomes a partial success:
> HubSpot fails, but Google Calendar and Stripe continue and persist their
> records. This demonstrates source-level failure isolation.

Narration if disabled:

> If demo failure injection is disabled in the environment, the API rejects the
> debug request with validation feedback. That is also safe behavior: production
> can disable demo-only failure controls.

What to point out:

- A provider failure does not wedge the entire run.
- The failed provider has an error type and sanitized summary.
- Successful providers still show normal fetched/upserted counts.

Optional alternate edge case to mention:

> The Google Calendar adapter also handles HTTP 410 expired sync tokens by
> falling back to a recovery full sync and only checkpointing the replacement
> token after records are written.

## 4:45-5:00 - Close

Narration:

> The demo covered the required Problem Statement 1 behaviors: normalized
> ingestion from three sources, idempotent writes, provider checkpoints,
> Google Calendar stale-token fallback, partial failure isolation, run history,
> Render startup readiness, and safe secret handling. The implementation is
> deliberately scoped to the assignment and avoids extra infrastructure.

## API Calls Used

The demo uses only the supported API surface:

```http
GET /health
GET /ready
POST /api/v1/sync
GET /api/v1/records/counts
GET /api/v1/sync-runs
```

The full-sync request body is:

```json
{"mode":"full"}
```
