#!/usr/bin/env bash
set -euo pipefail

require_env() {
  local name="$1"
  if [ -z "${!name:-}" ]; then
    echo "Missing required environment variable: ${name}" >&2
    exit 2
  fi
}

require_command() {
  local name="$1"
  if ! command -v "$name" >/dev/null 2>&1; then
    echo "${name} is required" >&2
    exit 2
  fi
}

section() {
  printf '\n'
  printf '============================================================\n'
  printf '%s\n' "$1"
  printf '============================================================\n'
}

curl_json() {
  local method="$1"
  local path="$2"
  local data="${3:-}"

  if [ -n "$data" ]; then
    curl --fail --silent --show-error \
      --request "$method" \
      --url "${base_url}${path}" \
      --header "Authorization: Bearer ${ADMIN_API_KEY}" \
      --header "Content-Type: application/json" \
      --data "$data"
  else
    curl --fail --silent --show-error \
      --request "$method" \
      --url "${base_url}${path}" \
      --header "Authorization: Bearer ${ADMIN_API_KEY}"
  fi
}

curl_json_with_status() {
  local method="$1"
  local path="$2"
  local data="$3"
  local body_file
  body_file="$(mktemp)"

  local status
  status="$(
    curl --silent --show-error \
      --request "$method" \
      --url "${base_url}${path}" \
      --header "Authorization: Bearer ${ADMIN_API_KEY}" \
      --header "Content-Type: application/json" \
      --data "$data" \
      --output "$body_file" \
      --write-out "%{http_code}"
  )"

  printf '%s\n' "$status"
  cat "$body_file"
  rm -f "$body_file"
}

require_env BASE_URL
require_env ADMIN_API_KEY
require_command curl
require_command jq

base_url="${BASE_URL%/}"
run_demo_failure="${RUN_DEMO_FAILURE:-1}"

section "Demo target"
printf 'BASE_URL=%s\n' "$base_url"
printf 'ADMIN_API_KEY is set and will not be printed.\n'

section "1. Lightweight health check"
curl_json GET /health | jq .

section "2. Readiness check: database and required tables"
curl_json GET /ready | jq .

section "3. First full sync across HubSpot, Google Calendar, and Stripe"
first_sync="$(curl_json POST /api/v1/sync '{"mode":"full"}')"
echo "$first_sync" | jq '{
  id,
  status,
  requested_mode,
  requested_providers,
  provider_results: [
    .provider_results[] | {
      provider,
      effective_mode,
      status,
      fetched_count,
      upserted_count,
      rejected_count,
      fallback_full_sync
    }
  ]
}'

section "4. Record counts after first full sync"
first_counts="$(curl_json GET /api/v1/records/counts)"
echo "$first_counts" | jq .

section "5. Second full sync to show idempotency"
second_sync="$(curl_json POST /api/v1/sync '{"mode":"full"}')"
echo "$second_sync" | jq '{
  id,
  status,
  requested_mode,
  requested_providers,
  provider_results: [
    .provider_results[] | {
      provider,
      effective_mode,
      status,
      fetched_count,
      upserted_count,
      rejected_count,
      fallback_full_sync
    }
  ]
}'

section "6. Record counts after second full sync"
second_counts="$(curl_json GET /api/v1/records/counts)"
echo "$second_counts" | jq .

section "7. Recent sync runs"
curl_json GET /api/v1/sync-runs | jq '{
  runs: [
    .runs[:5][] | {
      id,
      status,
      requested_mode,
      requested_providers,
      provider_results: [
        .provider_results[] | {
          provider,
          effective_mode,
          status,
          fetched_count,
          upserted_count,
          rejected_count,
          fallback_full_sync,
          error_type,
          error_summary
        }
      ]
    }
  ]
}'

if [ "$run_demo_failure" = "1" ]; then
  section "8. Optional demo failure injection: HubSpot fails, other providers continue"
  optional_output="$(curl_json_with_status POST /api/v1/sync '{"mode":"full","debug":{"fail_source":"hubspot"}}')"
  optional_status="$(printf '%s\n' "$optional_output" | sed -n '1p')"
  optional_body="$(printf '%s\n' "$optional_output" | sed '1d')"

  if [ "$optional_status" = "200" ]; then
    printf '%s\n' "$optional_body" | jq '{
      id,
      status,
      requested_mode,
      requested_providers,
      provider_results: [
        .provider_results[] | {
          provider,
          effective_mode,
          status,
          fetched_count,
          upserted_count,
          rejected_count,
          fallback_full_sync,
          error_type,
          error_summary
        }
      ]
    }'
  elif [ "$optional_status" = "422" ]; then
    printf 'Demo failure injection is not enabled on this service; skipping edge-case step.\n'
    printf '%s\n' "$optional_body" | jq '{detail}'
  else
    printf 'Optional demo failure request returned HTTP %s.\n' "$optional_status"
    printf '%s\n' "$optional_body" | jq .
    exit 1
  fi
else
  section "8. Optional demo failure injection skipped"
  printf 'Set RUN_DEMO_FAILURE=1 to include the controlled failure step.\n'
fi

section "Demo complete"
printf 'No secrets were printed. Compare the two count sections to show idempotency.\n'
