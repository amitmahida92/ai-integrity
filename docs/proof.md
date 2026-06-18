amitm@Amits-MacBook-Pro ai-integrity % curl -sS -X POST \
  "$BASE_URL/api/v1/sync" \
  -H "Authorization: Bearer $ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"mode":"full"}' \
  | jq
{
  "id": "ae07a60c-3da5-4f15-9d30-dcbbb9de3b00",
  "requested_mode": "full",
  "requested_providers": [
    "hubspot",
    "google_calendar",
    "stripe"
  ],
  "status": "success",
  "started_at": "2026-06-18T17:34:34.802514Z",
  "completed_at": "2026-06-18T17:34:39.527156Z",
  "provider_results": [
    {
      "provider": "hubspot",
      "requested_mode": "full",
      "effective_mode": "full",
      "status": "success",
      "fetched_count": 5,
      "upserted_count": 5,
      "rejected_count": 0,
      "pages_fetched": 1,
      "fallback_full_sync": false,
      "started_at": "2026-06-18T17:34:34.828333Z",
      "completed_at": "2026-06-18T17:34:35.814984Z",
      "error_type": null,
      "error_summary": null
    },
    {
      "provider": "google_calendar",
      "requested_mode": "full",
      "effective_mode": "full",
      "status": "success",
      "fetched_count": 120,
      "upserted_count": 120,
      "rejected_count": 0,
      "pages_fetched": 1,
      "fallback_full_sync": false,
      "started_at": "2026-06-18T17:34:35.825248Z",
      "completed_at": "2026-06-18T17:34:39.012769Z",
      "error_type": null,
      "error_summary": null
    },
    {
      "provider": "stripe",
      "requested_mode": "full",
      "effective_mode": "full",
      "status": "success",
      "fetched_count": 3,
      "upserted_count": 3,
      "rejected_count": 0,
      "pages_fetched": 1,
      "fallback_full_sync": false,
      "started_at": "2026-06-18T17:34:39.022299Z",
      "completed_at": "2026-06-18T17:34:39.500936Z",
      "error_type": null,
      "error_summary": null
    }
  ]
}
amitm@Amits-MacBook-Pro ai-integrity % curl -sS \
  "$BASE_URL/api/v1/records/counts" \
  -H "Authorization: Bearer $ADMIN_API_KEY" \
  | jq
{
  "total": 128,
  "counts": [
    {
      "provider": "google_calendar",
      "entity_type": "calendar_event",
      "count": 120
    },
    {
      "provider": "hubspot",
      "entity_type": "contact",
      "count": 5
    },
    {
      "provider": "stripe",
      "entity_type": "payment_intent",
      "count": 3
    }
  ]
}