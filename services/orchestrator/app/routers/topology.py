from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()

# Static dependency topology (API_SPEC §2.1) — health is dynamic.
NODES = [
    {"id": "checkout-api", "kind": "service"},
    {"id": "inventory-api", "kind": "service"},
    {"id": "fulfillment-worker", "kind": "service"},
    {"id": "postgres", "kind": "data"},
    {"id": "redis", "kind": "data"},
    {"id": "rabbitmq", "kind": "data"},
]

EDGES = [
    {"from": "inventory-api", "to": "checkout-api", "kind": "http"},
    {"from": "checkout-api", "to": "rabbitmq", "kind": "queue"},
    {"from": "rabbitmq", "to": "fulfillment-worker", "kind": "queue"},
    {"from": "checkout-api", "to": "redis", "kind": "cache"},
    {"from": "checkout-api", "to": "postgres", "kind": "db"},
    {"from": "fulfillment-worker", "to": "postgres", "kind": "db"},
]


@router.get("/api/topology")
async def topology(request: Request) -> dict:
    latest = request.app.state.aggregator.latest
    states: dict[str, str] = latest["node_states"] if latest else {}
    nodes = [{**node, "health": states.get(node["id"], "healthy")} for node in NODES]
    return {"nodes": nodes, "edges": EDGES}
