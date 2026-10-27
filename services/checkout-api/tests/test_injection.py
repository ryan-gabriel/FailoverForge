from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import injection
from app.main import app


class FakeRedis:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = values or {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)


class StubFlags:
    def __init__(
        self, latency: int = 0, error: float = 0.0, outage: bool = False
    ) -> None:
        self._latency = latency
        self._error = error
        self._outage = outage

    async def latency_ms(self) -> int:
        return self._latency

    async def error_ratio(self) -> float:
        return self._error

    async def cache_outage(self) -> bool:
        return self._outage

    async def active_injections(self) -> list[str]:
        active = []
        if self._latency > 0:
            active.append("latency")
        if self._error > 0:
            active.append("error")
        if self._outage:
            active.append("cache_outage")
        return active


async def test_flagstore_cache_outage_and_active() -> None:
    redis = FakeRedis({"inj:cache_outage": "1", "inj:latency:checkout-api": "150"})
    store = injection.FlagStore(redis, "checkout-api")  # type: ignore[arg-type]
    assert await store.cache_outage() is True
    assert await store.latency_ms() == 150
    assert await store.active_injections() == ["latency", "cache_outage"]


async def test_flagstore_fail_open() -> None:
    class BrokenRedis:
        async def get(self, key: str):
            raise ConnectionError("redis down")

    store = injection.FlagStore(BrokenRedis(), "checkout-api")  # type: ignore[arg-type]
    assert await store.cache_outage() is False
    assert await store.active_injections() == []


def test_error_middleware_injects_503(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(injection, "flags", StubFlags(error=1.0))
    client = TestClient(app)
    resp = client.post("/orders", json={"sku": "SKU-1001", "qty": 1})
    assert resp.status_code == 503
    assert resp.headers["x-injection"] == "error"


def test_cache_outage_forces_miss(monkeypatch: pytest.MonkeyPatch) -> None:
    order_id = "550e8400-e29b-41d4-a716-446655440000"
    snapshot = {
        "order_id": order_id,
        "status": "pending",
        "sku": "SKU-1001",
        "qty": 1,
        "created_at": "2026-10-20T12:00:00Z",
        "fulfilled_at": None,
    }

    class FakeRedisWithOrder(FakeRedis):
        def __init__(self) -> None:
            super().__init__({f"cache:order:{order_id}": json.dumps(snapshot)})

        async def set(self, key: str, value: str, ex: int | None = None) -> None:
            self.values[key] = value

    fake_redis = FakeRedisWithOrder()
    pg_read: list[str] = []

    async def fake_get_order(pool, oid):
        pg_read.append(str(oid))
        return snapshot

    monkeypatch.setattr("app.routes.state.redis", fake_redis)
    monkeypatch.setattr("app.routes.db.get_order", fake_get_order)
    monkeypatch.setattr(injection, "flags", StubFlags(outage=True))

    client = TestClient(app)
    resp = client.get(f"/orders/{order_id}")
    assert resp.status_code == 200
    assert resp.headers["x-cache"] == "miss"  # outage → cached value ignored
    assert pg_read == [order_id]
    # outage also skips the write-through
    assert f"cache:order:{order_id}" not in fake_redis.values or fake_redis.values.get(
        f"cache:order:{order_id}"
    ) == json.dumps(snapshot)


def test_no_outage_uses_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    order_id = "550e8400-e29b-41d4-a716-446655440000"
    snapshot = {"order_id": order_id, "status": "pending"}

    class FakeRedisWithOrder(FakeRedis):
        def __init__(self) -> None:
            super().__init__({f"cache:order:{order_id}": json.dumps(snapshot)})

    monkeypatch.setattr("app.routes.state.redis", FakeRedisWithOrder())
    monkeypatch.setattr(injection, "flags", StubFlags(outage=False))
    client = TestClient(app)
    resp = client.get(f"/orders/{order_id}")
    assert resp.headers["x-cache"] == "hit"
