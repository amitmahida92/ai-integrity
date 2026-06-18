# Problem Statement 1 Test Evidence

These acceptance tests run through the FastAPI sync API, the real orchestrator,
the PostgreSQL-backed repositories, and the real PostgreSQL unique constraint /
`ON CONFLICT` upsert path. Provider network calls are replaced with deterministic
fake provider clients.

| Requirement | Test | Evidence asserted |
| --- | --- | --- |
| Repeating a full sync does not increase normalized row count. | `tests/test_sync_acceptance.py::test_repeating_full_sync_does_not_increase_normalized_row_count` | Runs the HubSpot full sync twice against PostgreSQL, verifies row count remains one, verifies stored canonical/raw content, verifies two succeeded sync runs and checkpoint before/after audit data. |
| Repeating an incremental sync does not create duplicate rows. | `tests/test_sync_acceptance.py::test_repeating_incremental_sync_does_not_create_duplicate_rows` | Seeds a Stripe checkpoint, runs incremental sync twice, verifies one normalized row, verifies current stored PaymentIntent/event content, verifies source-result modes and checkpoint audit records. |
| A changed source object with the same provider, entity type and external ID updates the existing row. | `tests/test_sync_acceptance.py::test_changed_source_object_updates_existing_normalized_row` | Runs three HubSpot full syncs for the same key, verifies the database row ID stays stable, newer canonical/raw content replaces older content, and a stale older source version does not overwrite the newer stored content. |
| A Google Calendar expired sync token causes full-sync fallback and replacement token persistence after fallback records are stored. | `tests/test_sync_acceptance.py::test_google_expired_token_fallback_persists_replacement_after_records` | Seeds a stale Google checkpoint, fake Google client raises the 410-equivalent exception, verifies incremental then full client calls, persisted fallback record content, replacement checkpoint, and `recovery_full` source-result audit data. |
| If persistence fails, the provider checkpoint remains unchanged. | `tests/test_sync_acceptance.py::test_persistence_failure_leaves_provider_checkpoint_unchanged` | Seeds an old checkpoint, returns a record that violates the PostgreSQL provider check constraint, verifies no normalized rows commit, checkpoint remains old, and failed source-result audit has no checkpoint-after value. |
| If one provider fails, the other two still persist records and the run is `partial_success`. | `tests/test_sync_acceptance.py::test_one_provider_failure_still_persists_other_two_and_marks_partial_success` | HubSpot fake client fails while Google and Stripe fake clients succeed, verifies API `partial_success`, database `completed_with_errors`, only the two valid records exist, successful checkpoints persist, and failed source audit is isolated. |
| A malformed provider record is rejected and counted while valid records continue. | `tests/test_sync_acceptance.py::test_malformed_record_rejected_while_valid_records_continue` plus provider adapter non-dict tests in `tests/sources/` | HubSpot fake client returns one valid and one malformed contact while Google and Stripe succeed, verifies successful overall run, three valid rows, HubSpot fetched/upserted/rejected counts, and all source-result audit records. Source adapter tests also prove non-object garbage returned by HubSpot, Google Calendar and Stripe is counted as rejected instead of silently dropped. |

## Database Coverage

The `tests/conftest.py` database fixture fails when configured with SQLite. The
acceptance tests therefore require PostgreSQL and exercise the actual database
schema, unique key on `normalized_records(provider, entity_type, external_id)`,
check constraints, JSONB checkpoint storage, and repository upsert logic.
