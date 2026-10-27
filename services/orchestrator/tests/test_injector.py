from __future__ import annotations

import json
import uuid

import httpx
import pytest

from app.config import Settings
from app.eventstore import IncidentState
from app.injector import InjectionRejected, Injector
from tests.conftest import FakePool


class FakeRedis:
    def __init__(self) -> None:
        self.sets: list[tuple[str, str, int | None]] = []
        self.deletes: list[str] = []

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.sets.append((key, value, ex))

    async def delete(self, key: str) -> None:
        self.deletes.append(key)


class FakeChannel:
    def __init__(self) -> None:
        self.published: list[dict] = []

    class _Exchange:
        def __init__(self, channel: FakeChannel) -> None:
            self._channel = channel

        async def publish(self, message, routing_key: str) -> None:
            self._channel.published.append(
                {"routing_key": routing_key, "body": message.body}
            )

    @property
    def default_exchange(self):
        return FakeChannel._Exchange(self)


@pytest.fixture()
def injector(monkeypatch: pytest.MonkeyPatch):
    events: list[tuple[uuid.UUID, str, dict]] = []

    async def fake_append(pool, incident_id, event_type, payload):
        events.append((incident_id, event_type, payload))
        return {
            "id": 1,
            "seq": len(events),
            "event_type": event_type,
            "ts": "t",
            "payload": payload,
        }

    monkeypatch.setattr("app.eventstore.append", fake_append)

    redis = FakeRedis()
    channel = FakeChannel()
    crash_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        crash_urls.append(str(request.url))
        return httpx.Response(200, json={"ok": True})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    incidents = IncidentState(FakePool())  # type: ignore[arg-type]
    settings = Settings(require_chaos_token=False)
    inj = Injector(redis, incidents._pool, incidents, http, channel, settings)  # type: ignore[arg-type]
    inj._events = events  # type: ignore[attr-defined]
    inj._crash_urls = crash_urls  # type: ignore[attr-defined]
    inj._redis = redis  # type: ignore[attr-defined]
    inj._channel = channel  # type: ignore[attr-defined]
    return inj


async def test_inject_latency_sets_flag_and_events(injector: Injector) -> None:
    inj = await injector.inject("inventory-api", "latency", 400)
    assert inj.injection_id.startswith("inj_")
    assert injector._redis.sets == [("inj:latency:inventory-api", "400", 300)]  # type: ignore[attr-defined]
    types = [e[1] for e in injector._events]  # type: ignore[attr-defined]
    assert types == ["INCIDENT_OPENED", "FAILURE_INJECTED"]
    assert injector._events[1][2] == {  # type: ignore[attr-defined]
        "service": "inventory-api",
        "failure_type": "latency",
        "intensity": 400,
        "ttl_s": 300,
    }


async def test_second_inject_does_not_reopen_incident(injector: Injector) -> None:
    await injector.inject("checkout-api", "error", 0.5)
    await injector.inject("inventory-api", "latency", 100)
    opened = [e for e in injector._events if e[1] == "INCIDENT_OPENED"]  # type: ignore[attr-defined]
    assert len(opened) == 1
    assert len(injector.active) == 2


async def test_clear_last_injection_closes_incident(injector: Injector) -> None:
    inj = await injector.inject("inventory-api", "latency", 400)
    await injector.clear(inj.injection_id)
    assert injector._redis.deletes == ["inj:latency:inventory-api"]  # type: ignore[attr-defined]
    types = [e[1] for e in injector._events]  # type: ignore[attr-defined]
    assert types == [
        "INCIDENT_OPENED",
        "FAILURE_INJECTED",
        "MITIGATION_APPLIED",
        "INCIDENT_CLOSED",
    ]
    assert not injector._incidents.is_open


async def test_clear_all(injector: Injector) -> None:
    await injector.inject("inventory-api", "latency", 400)
    await injector.inject("checkout-api", "error", 0.9)
    cleared = await injector.clear_all()
    assert cleared == 2
    assert injector.active == {}


async def test_cache_outage_only_checkout(injector: Injector) -> None:
    with pytest.raises(InjectionRejected):
        await injector.inject("inventory-api", "cache_outage", 0)
    inj = await injector.inject("checkout-api", "cache_outage", 0)
    assert ("inj:cache_outage", "1", 300) in injector._redis.sets  # type: ignore[attr-defined]
    await injector.clear(inj.injection_id)
    assert "inj:cache_outage" in injector._redis.deletes  # type: ignore[attr-defined]


async def test_queue_saturation_floods_and_pauses(injector: Injector) -> None:
    inj = await injector.inject("fulfillment-worker", "queue_saturation", 5)
    channel = injector._channel  # type: ignore[attr-defined]
    assert len(channel.published) == 5
    assert all(p["routing_key"] == "fulfillment" for p in channel.published)
    body = json.loads(channel.published[0]["body"])
    assert body["sku"] == "SKU-FLOOD"
    assert ("inj:pause_worker", "1", 300) in injector._redis.sets  # type: ignore[attr-defined]
    await injector.clear(inj.injection_id)
    assert "inj:pause_worker" in injector._redis.deletes  # type: ignore[attr-defined]


async def test_crash_calls_admin_endpoint(injector: Injector) -> None:
    await injector.inject("inventory-api", "crash", 0)
    urls = injector._crash_urls  # type: ignore[attr-defined]
    assert len(urls) == 1 and urls[0].endswith("/admin/crash")
    # crash keeps the entry active (it already happened; clearing is bookkeeping)
    assert len(injector.active) == 1
    assert injector._redis.sets == []  # type: ignore[attr-defined]


async def test_validation_rejections(injector: Injector) -> None:
    with pytest.raises(InjectionRejected):
        await injector.inject("nope", "latency", 100)
    with pytest.raises(InjectionRejected):
        await injector.inject("checkout-api", "wat", 1)
    with pytest.raises(InjectionRejected):
        await injector.inject("checkout-api", "latency", 0)
    with pytest.raises(InjectionRejected):
        await injector.inject("checkout-api", "latency", 100, ttl_s=999999)
    with pytest.raises(InjectionRejected):
        await injector.inject("fulfillment-worker", "queue_saturation", 99999)


async def test_clear_unknown_injection(injector: Injector) -> None:
    with pytest.raises(KeyError):
        await injector.clear("inj_missing")
