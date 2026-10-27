from __future__ import annotations

import uuid
from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from app import cache, stats
from app.main import app
from app.state import state, to_iso


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value

    async def delete(self, key: str) -> None:
        self.store.pop(key, None)


class FakeRow(dict):
    pass


class FakePool:
    def __init__(self) -> None:
        self.orders: dict[uuid.UUID, dict] = {}

    async def fetchrow(self, query: str, *args):
        if query.strip().startswith("INSERT"):
            order_id, sku, qty, customer_ref = args
            now = datetime.now(UTC)
            row = {
                "order_id": order_id,
                "sku": sku,
                "qty": qty,
                "customer_ref": customer_ref,
                "status": "pending",
                "created_at": now,
                "fulfilled_at": None,
            }
            self.orders[order_id] = row
            return FakeRow(created_at=now)
        if query.strip().startswith("SELECT"):
            (order_id,) = args
            row = self.orders.get(order_id)
            return FakeRow(**row) if row is not None else None
        raise AssertionError(f"unexpected query: {query}")


class FakeChannel:
    def __init__(self) -> None:
        self.published: list[dict] = []


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch):
    fake_redis = FakeRedis()
    fake_pool = FakePool()
    fake_channel = FakeChannel()

    async def fake_publish(channel, order_id, sku, qty):
        channel.published.append(
            {"order_id": str(order_id), "sku": sku, "qty": qty, "attempt": 0}
        )

    inventory_responses: dict[str, httpx.Response] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sku = request.url.path.rsplit("/", 2)[-2]
        return inventory_responses.get(sku, httpx.Response(200, json={"available": 42}))

    monkeypatch.setattr(state, "redis", fake_redis)
    monkeypatch.setattr(state, "pool", fake_pool)
    monkeypatch.setattr(state, "mq_channel", fake_channel)
    monkeypatch.setattr(
        state, "http", httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    monkeypatch.setattr("app.routes.mq.publish_fulfillment", fake_publish)

    c = TestClient(app)
    c.fake_redis = fake_redis
    c.fake_pool = fake_pool
    c.fake_channel = fake_channel
    c.inventory_responses = inventory_responses
    yield c


def test_create_order_happy_path(client: TestClient) -> None:
    resp = client.post(
        "/orders", json={"sku": "SKU-1001", "qty": 2, "customer_ref": "test"}
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "pending"
    assert body["sku"] == "SKU-1001"
    assert body["qty"] == 2
    uuid.UUID(body["order_id"])
    assert body["created_at"].endswith("Z")
    assert client.fake_channel.published == [
        {"order_id": body["order_id"], "sku": "SKU-1001", "qty": 2, "attempt": 0}
    ]
    assert len(client.fake_pool.orders) == 1


def test_create_order_insufficient_stock(client: TestClient) -> None:
    client.inventory_responses["SKU-1001"] = httpx.Response(
        409, json={"detail": "insufficient stock"}
    )
    resp = client.post("/orders", json={"sku": "SKU-1001", "qty": 1})
    assert resp.status_code == 409
    assert resp.json() == {"detail": "insufficient stock for SKU-1001"}
    assert client.fake_channel.published == []
    assert client.fake_pool.orders == {}


def test_create_order_unknown_sku(client: TestClient) -> None:
    client.inventory_responses["GHOST"] = httpx.Response(
        404, json={"detail": "unknown SKU"}
    )
    resp = client.post("/orders", json={"sku": "GHOST", "qty": 1})
    assert resp.status_code == 404


def test_create_order_inventory_down(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(
        state, "http", httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    resp = client.post("/orders", json={"sku": "SKU-1001", "qty": 1})
    assert resp.status_code == 503
    assert resp.json() == {"detail": "inventory unavailable"}


def test_get_order_cache_miss_then_hit(client: TestClient) -> None:
    created = client.post("/orders", json={"sku": "SKU-1002", "qty": 1}).json()
    order_id = created["order_id"]

    first = client.get(f"/orders/{order_id}")
    assert first.status_code == 200
    assert first.headers["x-cache"] == "miss"
    assert first.json()["status"] == "pending"

    second = client.get(f"/orders/{order_id}")
    assert second.status_code == 200
    assert second.headers["x-cache"] == "hit"
    assert second.json()["order_id"] == order_id


def test_get_order_unknown_404(client: TestClient) -> None:
    resp = client.get("/orders/550e8400-e29b-41d4-a716-446655440000")
    assert resp.status_code == 404
    assert client.get("/orders/not-a-uuid").status_code == 404


def test_health_and_stats_exclusions(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}
    before = client.get("/stats").json()["requests_total"]
    client.get("/health")
    client.get("/stats")
    after = client.get("/stats").json()["requests_total"]
    assert after == before


def test_stats_shape(client: TestClient) -> None:
    client.post("/orders", json={"sku": "SKU-1001", "qty": 1})
    body = client.get("/stats").json()
    assert body["service"] == "checkout-api"
    assert body["queue_published"] >= 1
    assert set(body["cache"]) == {"hits", "misses"}
    assert body["injections_active"] == []


def test_order_create_validation(client: TestClient) -> None:
    assert client.post("/orders", json={"sku": "SKU-1001", "qty": 0}).status_code == 422


def test_cache_key_and_iso() -> None:
    assert cache.order_cache_key("abc") == "cache:order:abc"
    dt = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    assert to_iso(dt) == "2026-01-01T12:00:00Z"
    assert to_iso(None) is None


def test_histogram_buckets() -> None:
    s = stats.Stats()
    s.observe(2.0)
    s.observe(70.0)
    assert s.counts[stats.BUCKETS_MS.index(5)] == 1
    assert s.counts[stats.BUCKETS_MS.index(100)] == 1
    assert s.count == 2
