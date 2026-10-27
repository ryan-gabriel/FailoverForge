from __future__ import annotations

import base64
import json
from typing import Any

import httpx

MAIN_QUEUE = "fulfillment"
DLQ = "fulfillment.dlq"
VHOST = "%2F"


async def fetch_depths(http: httpx.AsyncClient, mgmt_url: str) -> dict:
    """Lightweight depth poll for the aggregator (no message payloads)."""
    main = await http.get(f"{mgmt_url}/api/queues/{VHOST}/{MAIN_QUEUE}")
    main.raise_for_status()
    data = main.json()
    dlq_depth = 0
    try:
        dlq = await http.get(f"{mgmt_url}/api/queues/{VHOST}/{DLQ}")
        dlq.raise_for_status()
        dlq_depth = dlq.json().get("messages", 0)
    except httpx.HTTPError:
        pass
    return {
        "depth": data.get("messages", 0),
        "consumers": data.get("consumers", 0),
        "dlq_depth": dlq_depth,
    }


def _decode_payload(payload: str, encoding: str) -> dict[str, Any] | None:
    try:
        raw = base64.b64decode(payload) if encoding == "base64" else payload.encode()
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except Exception:  # noqa: BLE001 — sample decoding must never fail the endpoint
        return None


async def fetch_queue_snapshot(
    http: httpx.AsyncClient, mgmt_url: str, samples: bool = False
) -> dict:
    depths = await fetch_depths(http, mgmt_url)
    dlq_samples: list[dict] = []
    if samples and depths["dlq_depth"] > 0:
        try:
            resp = await http.post(
                f"{mgmt_url}/api/queues/{VHOST}/{DLQ}/get",
                json={
                    "count": 5,
                    "ackmode": "ack_requeue_true",
                    "encoding": "auto",
                    "truncate": 50000,
                },
            )
            resp.raise_for_status()
            for msg in resp.json():
                payload = _decode_payload(
                    msg.get("payload", ""), msg.get("encoding", "string")
                )
                headers = (msg.get("properties") or {}).get("headers") or {}
                dlq_samples.append(
                    {
                        "order_id": (payload or {}).get("order_id"),
                        "reason": headers.get("x-reason", "unknown"),
                    }
                )
        except httpx.HTTPError:
            pass
    return {**depths, "dlq_samples": dlq_samples}
