from __future__ import annotations

import asyncio

from app import injection, stats
from app.consumer import FulfillmentConsumer
from app.main import pause_watcher


class FakeRedis:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = values or {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)


class FakeQueue:
    def __init__(self) -> None:
        self.consumers: list[str] = []
        self.cancelled: list[str] = []
        self._n = 0

    async def consume(self, callback) -> str:
        self._n += 1
        tag = f"ctag-{self._n}"
        self.consumers.append(tag)
        return tag

    async def cancel(self, tag: str) -> None:
        self.cancelled.append(tag)


async def test_pause_flag_roundtrip() -> None:
    store = injection.FlagStore(
        FakeRedis({"inj:pause_worker": "1"}), "fulfillment-worker"
    )  # type: ignore[arg-type]
    assert await store.pause_worker() is True
    assert await store.active_injections() == ["pause_worker"]

    store2 = injection.FlagStore(FakeRedis(), "fulfillment-worker")  # type: ignore[arg-type]
    assert await store2.pause_worker() is False
    assert await store2.active_injections() == []


async def test_pause_watcher_toggles_consumer(monkeypatch) -> None:
    redis = FakeRedis({"inj:pause_worker": "1"})
    flags = injection.FlagStore(redis, "fulfillment-worker")  # type: ignore[arg-type]
    monkeypatch.setattr(injection, "flags", flags)

    consumer = FulfillmentConsumer()
    consumer.queue = FakeQueue()  # type: ignore[assignment]
    consumer.is_consuming = True
    consumer.consumer_tag = "ctag-0"

    watcher = asyncio.create_task(pause_watcher(consumer))
    await asyncio.sleep(0.05)
    assert consumer.is_consuming is False
    assert consumer.queue.cancelled == ["ctag-0"]  # type: ignore[attr-defined]
    assert stats.stats.paused is True

    redis.values["inj:pause_worker"] = "0"  # any non-"1" value counts as clear
    await asyncio.sleep(0.8)  # next poll cycle (flag cache TTL 0.25s + poll 0.5s)
    assert consumer.is_consuming is True
    assert stats.stats.paused is False

    watcher.cancel()
    try:
        await watcher
    except asyncio.CancelledError:
        pass


async def test_pause_watcher_fails_open(monkeypatch) -> None:
    class BrokenRedis:
        async def get(self, key: str):
            raise ConnectionError("redis down")

    flags = injection.FlagStore(BrokenRedis(), "fulfillment-worker")  # type: ignore[arg-type]
    monkeypatch.setattr(injection, "flags", flags)

    consumer = FulfillmentConsumer()
    consumer.queue = FakeQueue()  # type: ignore[assignment]
    consumer.is_consuming = True
    consumer.consumer_tag = "ctag-0"

    watcher = asyncio.create_task(pause_watcher(consumer))
    await asyncio.sleep(0.6)
    assert consumer.is_consuming is True  # fail-open: no flag → keep consuming
    assert stats.stats.paused is False
    watcher.cancel()
    try:
        await watcher
    except asyncio.CancelledError:
        pass
