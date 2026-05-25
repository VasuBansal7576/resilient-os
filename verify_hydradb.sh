#!/usr/bin/env bash
set -euo pipefail

HYDRA_KEY="${HYDRADB_API_KEY:-${HYDRA_KEY:-}}"
TENANT="${HYDRADB_TENANT_ID:-${TENANT:-resilient-os}}"
SUB_TENANT="${HYDRADB_SUB_TENANT_ID:-resilient-os}"
BASE_URL="${HYDRADB_BASE_URL:-https://api.hydradb.com}"

if [[ -z "$HYDRA_KEY" ]]; then
  echo "HYDRADB_API_KEY or HYDRA_KEY is required."
  echo "Example: HYDRADB_API_KEY=... HYDRADB_TENANT_ID=$TENANT bash verify_hydradb.sh"
  exit 2
fi

pretty_json() {
  python3 -m json.tool 2>/dev/null || cat
}

echo "=== 0. Available tenants ==="
curl -sS "$BASE_URL/tenants/tenant_ids" \
  -H "Authorization: Bearer $HYDRA_KEY" | pretty_json

echo "=== 1. Tenant status ==="
curl -sS "$BASE_URL/tenants/infra/status?tenant_id=$TENANT" \
  -H "Authorization: Bearer $HYDRA_KEY" | pretty_json

echo "=== 2. Add memory ==="
ADD_RESPONSE="$(curl -sS -X POST "$BASE_URL/memories/add_memory" \
  -H "Authorization: Bearer $HYDRA_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"tenant_id\":\"$TENANT\",\"sub_tenant_id\":\"$SUB_TENANT\",\"memories\":[{\"text\":\"test failure: rate limit on scraper\",\"infer\":false,\"metadata\":{\"tool\":\"scraper\",\"status\":\"error\",\"type\":\"tool_call\"}}]}")"
printf '%s\n' "$ADD_RESPONSE" | pretty_json

SOURCE_ID="$(printf '%s\n' "$ADD_RESPONSE" | python3 -c 'import json,sys; payload=json.load(sys.stdin); results=payload.get("results") or [{}]; print(results[0].get("source_id", ""))' 2>/dev/null || true)"
if [[ -n "$SOURCE_ID" ]]; then
  for attempt in 1 2 3 4 5; do
    echo "=== 2.$attempt Processing status ==="
    STATUS_RESPONSE="$(curl -sS -X POST "$BASE_URL/ingestion/verify_processing?file_ids=$SOURCE_ID&tenant_id=$TENANT" \
      -H "Authorization: Bearer $HYDRA_KEY")"
    printf '%s\n' "$STATUS_RESPONSE" | pretty_json
    INDEX_STATUS="$(printf '%s\n' "$STATUS_RESPONSE" | python3 -c 'import json,sys; payload=json.load(sys.stdin); statuses=payload.get("statuses") or [{}]; print(statuses[0].get("indexing_status", ""))' 2>/dev/null || true)"
    [[ "$INDEX_STATUS" == "completed" ]] && break
    sleep 3
  done
fi

echo "=== 3. Recall ==="
curl -sS -X POST "$BASE_URL/recall/recall_preferences" \
  -H "Authorization: Bearer $HYDRA_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"tenant_id\":\"$TENANT\",\"sub_tenant_id\":\"$SUB_TENANT\",\"query\":\"rate limit recovery\",\"max_results\":3,\"graph_context\":true}" | pretty_json
