#!/usr/bin/env bash
set -euo pipefail

CHECKOUT_URL="${CHECKOUT_URL:-http://localhost:8001}"
INVENTORY_URL="${INVENTORY_URL:-http://localhost:8002}"
WORKER_URL="${WORKER_URL:-http://localhost:8003}"

wait_http() {
  local url="$1" name="$2"
  echo "waiting for ${name} (${url}) ..."
  for _ in $(seq 1 90); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  echo "ERROR: ${name} not healthy after 90s" >&2
  exit 1
}

json_field() {
  python3 -c 'import sys, json; print(json.load(sys.stdin)[sys.argv[1]])' "$1"
}

wait_http "${INVENTORY_URL}/health" inventory-api
wait_http "${CHECKOUT_URL}/health" checkout-api
wait_http "${WORKER_URL}/health" fulfillment-worker

echo "seeding stock ..."
curl -fsS -X POST "${INVENTORY_URL}/admin/seed" >/dev/null

echo "creating order ..."
order_id="$(curl -fsS -X POST "${CHECKOUT_URL}/orders" \
  -H 'Content-Type: application/json' \
  -d '{"sku":"SKU-1001","qty":1,"customer_ref":"smoke"}' | json_field order_id)"
echo "order_id=${order_id}"

echo "polling for fulfillment ..."
for _ in $(seq 1 30); do
  status="$(curl -fsS "${CHECKOUT_URL}/orders/${order_id}" | json_field status)"
  echo "  status=${status}"
  if [ "${status}" = "fulfilled" ]; then
    echo "SMOKE OK"
    exit 0
  fi
  sleep 1
done

echo "ERROR: order not fulfilled within 30s" >&2
exit 1
