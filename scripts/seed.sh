#!/usr/bin/env bash
set -euo pipefail

INVENTORY_URL="${INVENTORY_URL:-http://localhost:8002}"

echo "waiting for inventory-api (${INVENTORY_URL}) ..."
for _ in $(seq 1 90); do
  if curl -fsS "${INVENTORY_URL}/health" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

curl -fsS -X POST "${INVENTORY_URL}/admin/seed" | python3 -m json.tool
echo "SEED OK"
