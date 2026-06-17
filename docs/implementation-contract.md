# Problem Statement 1 — Codex Implementation Contract

## Objective

Implement and deploy a synchronous backend sync pipeline for:

* HubSpot Contacts
* Google Calendar Events
* Stripe PaymentIntents

The system must support:

* full and incremental sync
* stale Google Calendar cursor recovery
* idempotent PostgreSQL writes
* per-source checkpoints
* source-level failure isolation
* persisted sync-run history
* debug failure injection for demonstration

Do not add workers, queues, Redis, outbox, DLQ, HubSpot Deals, Stripe Invoices, cross-source identity resolution or production-scale OAuth.

---

## 1. Minimum database tables

Use UUID primary keys, timezone-aware timestamps and PostgreSQL JSONB.

### `hubspot_contacts`

Required columns:

* `id`
* `external_id`
* `email`
* `first_name`
* `last_name`
* `phone`
* `provider_updated_at`
* `raw_payload`
* `created_at`
* `updated_at`

Constraint:

```text
UNIQUE(external_id)
```

Upsert key:

```text
external_id
```

---

### `google_calendar_events`

Required columns:

* `id`
* `calendar_id`
* `external_id`
* `title`
* `description`
* `status`
* `start_at`
* `end_at`
* `is_deleted`
* `provider_updated_at`
* `raw_payload`
* `created_at`
* `updated_at`

Constraint:

```text
UNIQUE(calendar_id, external_id)
```

Canceled/deleted Google events must be stored as tombstones:

```text
is_deleted = true
```

---

### `stripe_payment_intents`

Required columns:

* `id`
* `external_id`
* `amount`
* `amount_received`
* `currency`
* `status`
* `customer_id`
* `provider_created_at`
* `last_event_created_at`
* `raw_payload`
* `created_at`
* `updated_at`

Constraint:

```text
UNIQUE(external_id)
```

Amounts must remain in Stripe minor units. Do not convert cents into decimal currency values during ingestion.

---

### `sync_checkpoints`

Required columns:

* `id`
* `source`
* `checkpoint_data`
* `created_at`
* `updated_at`

Constraint:

```text
UNIQUE(source)
```

Allowed source values:

```text
hubspot
google_calendar
stripe
```

`checkpoint_data` is JSONB because each provider has different cursor semantics.

---

### `sync_runs`

Represents one API-triggered orchestration run.

Required columns:

* `id`
* `requested_mode`
* `requested_sources`
* `status`
* `started_at`
* `finished_at`

Allowed statuses:

```text
running
succeeded
completed_with_errors
failed
```

Status rules:

* all sources succeeded → `succeeded`
* some sources succeeded and some failed → `completed_with_errors`
* all requested sources failed → `failed`

---

### `sync_source_runs`

Represents one provider execution within a sync run.

Required columns:

* `id`
* `sync_run_id`
* `source`
* `requested_mode`
* `effective_mode`
* `status`
* `records_fetched`
* `records_upserted`
* `pages_fetched`
* `checkpoint_before`
* `checkpoint_after`
* `error_type`
* `error_message`
* `started_at`
* `finished_at`

Allowed effective modes:

```text
full
incremental
recovery_full
```

Allowed statuses:

```text
running
succeeded
failed
```

Relationship:

```text
sync_source_runs.sync_run_id → sync_runs.id
```

No additional database tables are required.

---

## 2. Required endpoints

All API routes should use an `/api/v1` prefix.

### Health check

```http
GET /healthz
```

Response:

```json
{
  "status": "ok",
  "database": "reachable"
}
```

---

### Trigger a synchronous sync run

```http
POST /api/v1/sync-runs
```

Request:

```json
{
  "mode": "incremental",
  "sources": [
    "hubspot",
    "google_calendar",
    "stripe"
  ],
  "debug": {
    "fail_source": null,
    "stale_google_cursor": false
  }
}
```

Rules:

* `mode` must be `full` or `incremental`.
* `sources` must contain one or more supported sources.
* Sources execute independently.
* A failed source must not prevent later sources from running.
* The HTTP request remains open until all requested sources finish.
* Return the persisted run summary.
* A partially failed run still returns HTTP `200`.
* Invalid input returns HTTP `422`.

Example response:

```json
{
  "id": "uuid",
  "requested_mode": "incremental",
  "status": "completed_with_errors",
  "sources": [
    {
      "source": "hubspot",
      "effective_mode": "incremental",
      "status": "failed",
      "records_upserted": 0,
      "error_type": "InjectedFailure"
    },
    {
      "source": "google_calendar",
      "effective_mode": "incremental",
      "status": "succeeded",
      "records_upserted": 2
    },
    {
      "source": "stripe",
      "effective_mode": "incremental",
      "status": "succeeded",
      "records_upserted": 1
    }
  ]
}
```

Debug options must only work when:

```text
DEBUG_SYNC_TOOLS_ENABLED=true
```

`fail_source` must throw a controlled exception inside the selected source before its database transaction commits.

`stale_google_cursor=true` must replace the current Google sync token in memory with an invalid token for that execution, causing the actual stale-token recovery path to run.

---

### Read one sync run

```http
GET /api/v1/sync-runs/{run_id}
```

Return the parent run and all source-run results.

---

### List recent sync runs

```http
GET /api/v1/sync-runs?limit=20
```

Return newest runs first.

---

### Inspect checkpoints

```http
GET /api/v1/sync-checkpoints
```

Return the current checkpoint for each provider.

Do not expose provider access tokens or OAuth credentials.

---

### Inspect normalized data

```http
GET /api/v1/contacts
GET /api/v1/calendar-events
GET /api/v1/payment-intents
```

Requirements:

* default limit: 50
* maximum limit: 100
* newest provider-updated records first
* sufficient fields must be returned to demonstrate normalization
* raw payload may be excluded from list responses

No create, update or delete endpoints are required for normalized records.

---

## 3. Provider-specific checkpoint behaviour

### HubSpot Contacts

Checkpoint shape:

```json
{
  "watermark": "2026-06-17T12:30:00Z",
  "overlap_seconds": 120
}
```

#### Full sync

1. Record `sync_started_at`.
2. Fetch all HubSpot Contacts using pagination.
3. Normalize every contact.
4. Determine the maximum `provider_updated_at` received.
5. Upsert all records.
6. Persist the new watermark in the same database transaction.

New watermark:

```text
maximum provider_updated_at observed
```

When no contacts exist:

```text
sync_started_at
```

#### Incremental sync

Query contacts modified since:

```text
stored watermark - 120 seconds
```

The overlap is intentional. Reprocessed records must remain safe because writes are idempotent.

After success, persist the maximum provider update timestamp observed. When no changed records are returned, retain the existing watermark or advance it to `sync_started_at`; choose one approach and document it consistently.

---

### Google Calendar Events

Checkpoint shape:

```json
{
  "calendar_id": "primary",
  "sync_token": "provider-token"
}
```

#### Full sync

1. Call Google Calendar `events.list` without `syncToken`.
2. Follow every `nextPageToken`.
3. Preserve canceled events as tombstones.
4. Capture the final `nextSyncToken`.
5. Upsert events and save `nextSyncToken` in one database transaction.

#### Incremental sync

1. Call `events.list` with the stored `syncToken`.
2. Follow every page.
3. Apply changed and canceled events.
4. Save the newly returned `nextSyncToken` only after all pages succeed.

#### Stale cursor recovery

When Google returns HTTP `410` for the sync token:

1. Do not fail immediately.
2. Change `effective_mode` to `recovery_full`.
3. Execute a full Google Calendar fetch in the same source run.
4. Upsert the full result.
5. Save the replacement `nextSyncToken`.
6. Mark the source run as succeeded if recovery succeeds.

If the recovery full sync fails:

* mark the Google source run as failed
* leave the previously persisted checkpoint unchanged
* allow HubSpot and Stripe to continue

---

### Stripe PaymentIntents

Checkpoint shape:

```json
{
  "event_watermark": "2026-06-17T12:30:00Z",
  "overlap_seconds": 120
}
```

#### Full sync

1. Record `sync_started_at`.
2. Fetch all PaymentIntents using Stripe pagination.
3. Normalize and upsert them.
4. Store `sync_started_at` as the event watermark.

The overlap used by the next incremental sync must cover events created during the full fetch.

#### Incremental sync

1. Query Stripe Events from:

```text
stored event watermark - 120 seconds
```

2. Process only PaymentIntent event types:

```text
payment_intent.created
payment_intent.processing
payment_intent.succeeded
payment_intent.payment_failed
payment_intent.canceled
payment_intent.requires_action
```

Processing additional `payment_intent.*` events is acceptable, but do not process unrelated object types.

3. Extract the PaymentIntent ID from each relevant event.
4. Deduplicate IDs within the run.
5. Retrieve the latest PaymentIntent object from Stripe.
6. Upsert the current PaymentIntent state.
7. Save the source-run start time as the next event watermark after success.

Repeated events and the overlap window must not create duplicate rows.

Do not use `PaymentIntent.created` alone as the incremental cursor because existing PaymentIntents can change status after creation.

---

## 4. Transaction and cursor-update rules

### Source isolation

Each provider must run inside its own error boundary.

Conceptually:

```python
for source in requested_sources:
    try:
        run_source(source)
    except Exception:
        record_source_failure()
        continue
```

Never use one transaction for all three providers.

---

### Fetch before database transaction

For this assignment’s small seeded datasets:

1. Fetch and normalize all pages for one source.
2. Only then open the source database transaction.
3. Upsert all normalized records.
4. Update the provider checkpoint.
5. Mark the source run succeeded.
6. Commit.

This deliberately favours correctness and simple rollback over production-scale memory efficiency.

---

### Atomic source commit

The following must commit atomically for each source:

* normalized record upserts
* checkpoint update
* successful `sync_source_runs` result

If an upsert or checkpoint update fails:

* roll back all writes for that source execution
* leave the old checkpoint unchanged
* mark the source run failed using a separate transaction
* continue with the next source

---

### Failure before commit

Any of the following must leave the checkpoint unchanged:

* provider authentication error
* timeout
* malformed provider response
* pagination failure
* normalization failure
* database write failure
* injected debug failure
* failed Google recovery full sync

---

### Upsert behaviour

Use PostgreSQL `INSERT ... ON CONFLICT DO UPDATE`.

Required conflict keys:

```text
HubSpot: external_id
Google: calendar_id + external_id
Stripe: external_id
```

A repeated full sync, incremental overlap or duplicate provider event must update the existing row rather than insert another row.

Raw provider payloads must be retained in JSONB for debugging and evidence.

---

### Older data protection

Where a reliable provider update timestamp exists, an older provider version must not overwrite a newer stored version.

At minimum, enforce this for:

* HubSpot using `provider_updated_at`
* Google Calendar using `provider_updated_at`
* Stripe using `last_event_created_at`

A repeated event with the same timestamp may safely overwrite with identical current provider state.

---

## 5. Exact acceptance tests

Use `pytest`.

Provider clients must be mockable. Database integration tests must run against PostgreSQL, not SQLite, because PostgreSQL upsert and JSONB behaviour are part of the implementation.

### `test_full_sync_all_sources_succeeds`

Given seeded records from all three providers, when a full sync runs:

* all three source runs succeed
* normalized rows are created
* three checkpoints are created
* overall run status is `succeeded`

---

### `test_repeated_full_sync_does_not_duplicate_rows`

Given one completed full sync, when the identical full sync runs again:

* row counts remain unchanged
* existing rows are updated
* no unique-constraint error occurs
* second run succeeds

---

### `test_incremental_sync_inserts_new_and_updates_existing_records`

Given an existing checkpoint and stored records, when providers return one new record and one modified record:

* one row is inserted
* one row is updated
* no duplicate row is created
* checkpoint advances only after success

---

### `test_hubspot_overlap_reprocessing_is_idempotent`

Given a HubSpot watermark with a 120-second overlap, when the same contacts are returned again:

* no duplicate contacts are inserted
* the run succeeds
* the checkpoint remains valid

---

### `test_google_incremental_uses_and_replaces_sync_token`

Given a valid stored Google sync token, when incremental sync succeeds:

* the stored token is sent to Google
* all pages are processed
* the final `nextSyncToken` replaces the old token
* the token is updated only after event writes succeed

---

### `test_google_410_triggers_recovery_full_sync`

Given a stale Google token, when Google returns `410`:

* a full fetch is automatically executed
* `effective_mode` becomes `recovery_full`
* normalized events are upserted
* a new sync token is stored
* the Google source run succeeds

---

### `test_google_recovery_failure_preserves_old_checkpoint`

Given a stale Google token and a failing recovery full fetch:

* the Google source run fails
* the old checkpoint remains unchanged
* no partial Google data is committed

---

### `test_stripe_event_overlap_does_not_duplicate_payment_intents`

Given Stripe Events are returned more than once because of the overlap window:

* each PaymentIntent ID is fetched at most once within that run
* only one database row exists per PaymentIntent
* the current Stripe state is stored
* the event watermark advances after success

---

### `test_source_failure_does_not_block_other_sources`

Given `debug.fail_source` is `hubspot`, when all three sources are requested:

* HubSpot fails
* HubSpot checkpoint remains unchanged
* Google succeeds
* Stripe succeeds
* Google and Stripe data are committed
* overall status is `completed_with_errors`

---

### `test_malformed_source_response_is_isolated`

Given one provider returns an invalid payload:

* that source run fails with an actionable error
* its checkpoint does not advance
* the other requested sources still run and commit
* overall run records the partial failure

---

### `test_database_failure_rolls_back_records_and_checkpoint`

Given a database error occurs during a source upsert:

* all writes from that source execution are rolled back
* its checkpoint remains unchanged
* its source run is marked failed
* later sources still execute

---

### `test_older_provider_version_does_not_overwrite_newer_row`

Given a newer row is already stored, when an older overlapping provider record is processed:

* the newer canonical state remains unchanged
* no duplicate row is created

---

### `test_sync_run_status_is_derived_from_source_results`

Verify:

```text
all success → succeeded
mixed results → completed_with_errors
all failed → failed
```

---

### Live deployment smoke tests

After deployment to Render, verify:

```text
GET /healthz
POST /api/v1/sync-runs with mode=full
POST /api/v1/sync-runs with mode=incremental
GET /api/v1/sync-runs/{id}
GET /api/v1/sync-checkpoints
GET /api/v1/contacts
GET /api/v1/calendar-events
GET /api/v1/payment-intents
```

---

## 6. Five-minute demo requirements

The demo must show observable evidence, not only explain code.

### 0:00–0:30 — Architecture

Show one diagram or README section containing:

```text
HubSpot ─────────────┐
Google Calendar ─────┼── FastAPI sync orchestration
Stripe ──────────────┘          │
                                ▼
                      Supabase PostgreSQL
```

Mention:

* provider-specific checkpoints
* one transaction per source
* PostgreSQL upserts
* source-level failure isolation

---

### 0:30–1:30 — Full sync

Trigger:

```http
POST /api/v1/sync-runs
```

with:

```json
{
  "mode": "full",
  "sources": [
    "hubspot",
    "google_calendar",
    "stripe"
  ]
}
```

Show:

* overall status `succeeded`
* all three source results
* fetched/upserted counts
* normalized contacts
* normalized calendar events
* normalized PaymentIntents
* stored checkpoints

---

### 1:30–2:15 — Idempotency

Immediately run the same full sync again.

Show:

* the second run succeeds
* database row counts do not increase
* no duplicates exist
* unique constraints and PostgreSQL upserts are responsible

---

### 2:15–3:15 — Incremental change

Before recording, create or modify at least one source record.

Preferred example:

* modify a HubSpot contact, or
* add/update a Google Calendar event, or
* change a Stripe PaymentIntent status in test mode

Run incremental sync.

Show:

* only changed provider data is applied
* the existing row is updated rather than duplicated
* the provider checkpoint changes

---

### 3:15–4:00 — Stale Google cursor recovery

Trigger incremental sync with:

```json
{
  "mode": "incremental",
  "sources": ["google_calendar"],
  "debug": {
    "stale_google_cursor": true
  }
}
```

Show:

* Google rejects the invalid token
* the implementation automatically performs a full recovery
* `effective_mode` is `recovery_full`
* the run succeeds
* a replacement sync token is stored

---

### 4:00–4:45 — Partial failure isolation

Trigger:

```json
{
  "mode": "incremental",
  "sources": [
    "hubspot",
    "google_calendar",
    "stripe"
  ],
  "debug": {
    "fail_source": "hubspot"
  }
}
```

Show:

* HubSpot is failed
* Google and Stripe are succeeded
* overall status is `completed_with_errors`
* Google and Stripe data were committed
* the HubSpot checkpoint did not advance

---

### 4:45–5:00 — Repository evidence

Show:

* passing test suite
* Alembic migrations
* Render deployment URL
* README tradeoffs
* AI usage disclosure
* references used

The final evidence should establish:

```text
No duplicate rows
No silently skipped cursor recovery
No checkpoint advancement after failure
No single-source failure wedging the whole run
```

---

## Completion boundary

The implementation is complete when:

* all listed endpoints work
* all migrations apply to a clean Supabase database
* all acceptance tests pass
* Render can trigger real full and incremental runs
* the stale cursor and partial failure demos work
* the README documents local setup, deployment, tradeoffs, references and AI usage

Do not implement anything outside this contract until Problem Statement 1 has been reviewed, tested and deployed.
