from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import injection, main


class FakeRedis:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = values or {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)


class StubFlags:
    def __init__(self, latency: int = 0, error: float = 0.0) -> None:
        self._latency = latency
        self._error = error

    async def latency_ms(self) -> int:
        return self._latency

    async def error_ratio(self) -> float:
        return self._error

    async def active_injections(self) -> list[str]:
        active = []
        if self._latency > 0:
            active.append("latency")
        if self._error > 0:
            active.append("error")
        return active


async def test_flagstore_latency_roundtrip() -> None:
    redis = FakeRedis({"inj:latency:inventory-api": "250"})
    store = injection.FlagStore(redis, "inventory-api")  # type: ignore[arg-type]
    assert await store.latency_ms() == 250
    assert await store.error_ratio() == 0.0
    assert await store.active_injections() == ["latency"]


async def test_flagstore_fail_open_on_redis_error() -> None:
    class BrokenRedis:
        async def get(self, key: str):
            raise ConnectionError("redis down")

    store = injection.FlagStore(BrokenRedis(), "inventory-api")  # type: ignore[arg-type]
    assert await store.latency_ms() == 0
    assert await store.error_ratio() == 0.0
    assert await store.active_injections() == []


async def test_flagstore_garbage_values_fail_open() -> None:
    redis = FakeRedis(
        {"inj:latency:inventory-api": "not-a-number", "inj:error:inventory-api": "xx"}
    )
    store = injection.FlagStore(redis, "inventory-api")  # type: ignore[arg-type]
    assert await store.latency_ms() == 0
    assert await store.error_ratio() == 0.0


def test_middleware_latency_and_error(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("app.main.asyncio.sleep", fake_sleep)
    monkeypatch.setattr(injection, "flags", StubFlags(latency=200, error=1.0))
    client = TestClient(main.app)
    resp = client.post("/stock/SKU-1001/reserve", json={"qty": 1})
    assert resp.status_code == 503
    assert resp.headers["x-injection"] == "error"
    assert slept == [0.2]


def test_middleware_injection_counted_in_stats(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(injection, "flags", StubFlags(error=1.0))
    client = TestClient(main.app)
    client.post("/stock/SKU-1001/reserve", json={"qty": 1})
    body = client.get("/stats").json()
    assert body["requests_error"] >= 1
    assert body["injections_active"] == ["error"]


def test_middleware_skips_health_and_stats(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("app.main.asyncio.sleep", fake_sleep)
    monkeypatch.setattr(injection, "flags", StubFlags(latency=500))
    client = TestClient(main.app)
    client.get("/health")
    client.get("/stats")
    assert slept == []


def test_middleware_no_flags_is_transparent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(injection, "flags", None)
    client = TestClient(main.app)
    resp = client.get("/stock/SKU-1001")
    assert resp.status_code == 200
    assert "x-injection" not in resp.headers
