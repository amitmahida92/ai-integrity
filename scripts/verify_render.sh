#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "Usage: $0 https://service-name.onrender.com ADMIN_API_KEY" >&2
  exit 2
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "curl is required" >&2
  exit 2
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "jq is required" >&2
  exit 2
fi

base_url="${1%/}"
admin_api_key="$2"

curl_json() {
  local method="$1"
  local path="$2"
  local data="${3:-}"

  if [ -n "$data" ]; then
    curl --fail --silent --show-error \
      --request "$method" \
      --url "${base_url}${path}" \
      --header "Content-Type: application/json" \
      --header "Authorization: Bearer ${admin_api_key}" \
      --header "X-Admin-API-Key: ${admin_api_key}" \
      --data "$data"
  else
    curl --fail --silent --show-error \
      --request "$method" \
      --url "${base_url}${path}" \
      --header "Authorization: Bearer ${admin_api_key}" \
      --header "X-Admin-API-Key: ${admin_api_key}"
  fi
}

echo "Checking /health"
curl_json GET /health | jq .

echo "Checking /ready"
curl_json GET /ready | jq .

echo "Running first full sync"
first_sync="$(curl_json POST /api/v1/sync '{"mode":"full"}')"
echo "$first_sync" | jq '{id, status, requested_mode, requested_providers}'

first_counts="$(curl_json GET /api/v1/records/counts)"
echo "Counts after first full sync"
echo "$first_counts" | jq .

echo "Running second full sync"
second_sync="$(curl_json POST /api/v1/sync '{"mode":"full"}')"
echo "$second_sync" | jq '{id, status, requested_mode, requested_providers}'

second_counts="$(curl_json GET /api/v1/records/counts)"
echo "Counts after second full sync"
echo "$second_counts" | jq .

echo "Recent sync runs"
curl_json GET /api/v1/sync-runs | jq .
