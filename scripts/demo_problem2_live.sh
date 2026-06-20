#!/usr/bin/env bash
set -euo pipefail

FROM_DATE="2026-06-01"
TO_DATE="2026-06-30"
GRAIN="day"

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

request_json() {
  local method="$1"
  local path="$2"
  local data="${3:-}"
  local body_file
  body_file="$(mktemp)"

  local curl_args=(
    --silent
    --show-error
    --request "$method"
    --url "${base_url}${path}"
    --header "Authorization: Bearer ${ADMIN_API_KEY}"
    --output "$body_file"
    --write-out "%{http_code}"
  )

  if [ -n "$data" ]; then
    curl_args+=(--header "Content-Type: application/json" --data "$data")
  fi

  local status
  if ! status="$(curl "${curl_args[@]}")"; then
    rm -f "$body_file"
    echo "Request failed: ${method} ${path}" >&2
    exit 1
  fi

  if ! jq empty "$body_file" >/dev/null; then
    echo "Response was not valid JSON: ${method} ${path} HTTP ${status}" >&2
    cat "$body_file" >&2
    rm -f "$body_file"
    exit 1
  fi

  printf '%s\n' "$status"
  cat "$body_file"
  rm -f "$body_file"
}

require_success_json() {
  local method="$1"
  local path="$2"
  local data="${3:-}"
  local output
  output="$(request_json "$method" "$path" "$data")"
  local status
  status="$(printf '%s\n' "$output" | sed -n '1p')"
  local body
  body="$(printf '%s\n' "$output" | sed '1d')"

  if [ "$status" -lt 200 ] || [ "$status" -ge 300 ]; then
    echo "Unexpected HTTP ${status}: ${method} ${path}" >&2
    printf '%s\n' "$body" | jq . >&2
    exit 1
  fi

  printf '%s\n' "$body"
}

canonical_json() {
  jq -S -c .
}

require_env BASE_URL
require_env ADMIN_API_KEY
require_command curl
require_command jq

base_url="${BASE_URL%/}"

section "Demo target"
printf 'BASE_URL=%s\n' "$base_url"
printf 'ADMIN_API_KEY is set and will not be printed.\n'
printf 'Date range: %s through %s\n' "$FROM_DATE" "$TO_DATE"

section "1. Live Render health"
require_success_json GET /health | jq .

section "2. Live Render readiness"
require_success_json GET /ready | jq .

section "3. Seed deterministic Problem 2 financial data"
seed_response="$(require_success_json POST /api/v1/problem-2/seed)"
printf '%s\n' "$seed_response" | jq .
printf 'Seed includes paid, completed, pending, failed, voided, refunded, and unknown statuses.\n'
printf 'Only allow-listed collected statuses count by default.\n'

section "4. Import Stripe test-mode PaymentIntents if available"
stripe_output="$(request_json POST /api/v1/problem-2/import-stripe)"
stripe_status="$(printf '%s\n' "$stripe_output" | sed -n '1p')"
stripe_body="$(printf '%s\n' "$stripe_output" | sed '1d')"

if [ "$stripe_status" -ge 200 ] && [ "$stripe_status" -lt 300 ]; then
  printf '%s\n' "$stripe_body" | jq '{status, imported_records, rejected_records, pages_fetched}'
elif [ "$stripe_status" = "404" ] || [ "$stripe_status" = "405" ]; then
  printf 'Stripe import endpoint is not implemented on this deployment; continuing with deterministic seed data.\n'
elif [ "$stripe_status" = "502" ] || [ "$stripe_status" = "503" ]; then
  printf 'Stripe import is unavailable on this deployment; continuing with deterministic seed data.\n'
  printf '%s\n' "$stripe_body" | jq '{detail}'
else
  echo "Unexpected HTTP ${stripe_status}: POST /api/v1/problem-2/import-stripe" >&2
  printf '%s\n' "$stripe_body" | jq . >&2
  exit 1
fi

section "5. Revenue summary"
summary_path="/api/v1/metrics/revenue/summary?from_date=${FROM_DATE}&to_date=${TO_DATE}"
summary_response="$(require_success_json GET "$summary_path")"
printf '%s\n' "$summary_response" | jq '{
  from_date,
  to_date,
  metric_name,
  metric_version,
  totals_by_currency
}'

section "6. Revenue breakdown for the same range"
breakdown_path="/api/v1/metrics/revenue/breakdown?from_date=${FROM_DATE}&to_date=${TO_DATE}&grain=${GRAIN}"
breakdown_response="$(require_success_json GET "$breakdown_path")"
printf '%s\n' "$breakdown_response" | jq '{
  from_date,
  to_date,
  grain,
  buckets,
  aggregate_totals_by_currency
}'

section "7. Metric invariant: summary total equals breakdown aggregate"
summary_totals="$(printf '%s\n' "$summary_response" | jq '.totals_by_currency' | canonical_json)"
breakdown_totals="$(printf '%s\n' "$breakdown_response" | jq '.aggregate_totals_by_currency' | canonical_json)"

printf 'summary totals:   %s\n' "$summary_totals"
printf 'breakdown totals: %s\n' "$breakdown_totals"

if [ "$summary_totals" = "$breakdown_totals" ]; then
  printf 'PASS: summary totals match breakdown aggregate totals.\n'
else
  printf 'FAIL: summary totals do not match breakdown aggregate totals.\n' >&2
  exit 1
fi

section "8. Latest relevant metric proof"
printf 'Collected revenue is allow-list based: source_name + source_entity_type + raw_status must join to counts_as_collected=true.\n'
printf 'Unknown, pending, failed, voided, and refunded statuses are excluded by default.\n'
printf '%s\n' "$summary_response" | jq '{metric_name, metric_version, totals_by_currency}'

section "Demo complete"
printf 'No secrets were printed.\n'
