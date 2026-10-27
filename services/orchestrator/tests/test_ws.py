from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import eventstore
from app.main import app
from app.ws import Hub
from tests.conftest import FakePool


class FakeWS:
    def __init__(self, fail: bool = False, fail_close: bool = False) -> None:
        self.fail = fail
        self.fail_close = fail_close
        self.sent: list[str] = []
        self.closed_with: int | None = None

    async def send_text(self, text: str) -> None:
        if self.fail:
            raise RuntimeError("connection gone")
        self.sent.append(text)

    async def close(self, code: int = 1000) -> None:
        if self.fail_close:
            raise RuntimeError("already closed")
        self.closed_with = code


async def test_broadcast_envelope_shape() -> None:
    hub = Hub()
    ws = FakeWS()
    await hub.connect(ws)  # type: ignore[arg-type]
    await hub.broadcast("metrics.tick", {"a": 1})
    assert len(ws.sent) == 1
    envelope = json.loads(ws.sent[0])
    assert envelope["type"] == "metrics.tick"
    assert envelope["data"] == {"a": 1}
    assert envelope["ts"].endswith("Z")


async def test_broadcast_without_clients_is_noop() -> None:
    hub = Hub()
    await hub.broadcast("metrics.tick", {})  # must not raise


async def test_broadcast_drops_dead_clients() -> None:
    hub = Hub()
    dead, alive = FakeWS(fail=True), FakeWS()
    await hub.connect(dead)  # type: ignore[arg-type]
    await hub.connect(alive)  # type: ignore[arg-type]
    assert hub.client_count == 2
    await hub.broadcast("injection.status", [])
    assert hub.client_count == 1
    assert dead.sent == []
    assert len(alive.sent) == 1


async def test_connect_disconnect_roundtrip() -> None:
    hub = Hub()
    ws = FakeWS()
    await hub.connect(ws)  # type: ignore[arg-type]
    await hub.disconnect(ws)  # type: ignore[arg-type]
    assert hub.client_count == 0


async def test_close_all_drains_clients_with_going_away_code() -> None:
    hub = Hub()
    a, b = FakeWS(), FakeWS()
    await hub.connect(a)  # type: ignore[arg-type]
    await hub.connect(b)  # type: ignore[arg-type]
    await hub.close_all()
    assert hub.client_count == 0
    assert a.closed_with == 1001
    assert b.closed_with == 1001


async def test_close_all_tolerates_dead_socket() -> None:
    hub = Hub()
    dead, alive = FakeWS(fail_close=True), FakeWS()
    await hub.connect(dead)  # type: ignore[arg-type]
    await hub.connect(alive)  # type: ignore[arg-type]
    await hub.close_all()  # must not raise
    assert hub.client_count == 0
    assert alive.closed_with == 1001


def test_ws_endpoint_registers_client() -> None:
    app.state.ws_hub = Hub()
    client = TestClient(app)
    with client.websocket_connect("/ws"):
        assert app.state.ws_hub.client_count == 1
        # MVP protocol: no client→server messages; server pushes only.
    assert app.state.ws_hub.client_count == 0


async def test_eventstore_listener_receives_appends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received: list[dict] = []

    async def listener(event: dict) -> None:
        received.append(event)

    pool = FakePool()
    eventstore.set_listener(listener)
    try:
        await eventstore.append(
            pool, eventstore.AMBIENT_INCIDENT_ID, "LOAD_STARTED", {"profile": "mixed"}
        )  # type: ignore[arg-type]
    finally:
        eventstore.set_listener(None)
    assert received[0]["event_type"] == "LOAD_STARTED"
    assert received[0]["incident_id"] == str(eventstore.AMBIENT_INCIDENT_ID)
    assert received[0]["payload"] == {"profile": "mixed"}


async def test_eventstore_listener_failure_is_swallowed() -> None:
    async def bad_listener(event: dict) -> None:
        raise RuntimeError("hub down")

    pool = FakePool()
    eventstore.set_listener(bad_listener)
    try:
        event = await eventstore.append(
            pool, eventstore.AMBIENT_INCIDENT_ID, "LOAD_STARTED", {}
        )  # type: ignore[arg-type]
    finally:
        eventstore.set_listener(None)
    assert event["event_type"] == "LOAD_STARTED"  # append still succeeds


async def test_injector_on_change_serializes_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.config import Settings
    from app.eventstore import IncidentState
    from app.injector import Injector

    async def fake_append(pool, incident_id, event_type, payload):
        return {}

    monkeypatch.setattr("app.eventstore.append", fake_append)

    batches: list[list[dict]] = []

    async def on_change(injections: list[dict]) -> None:
        batches.append(injections)

    class FakeRedis:
        async def set(self, *a, **k) -> None:
            pass

        async def delete(self, *a, **k) -> None:
            pass

    incidents = IncidentState(FakePool())  # type: ignore[arg-type]
    inj = Injector(
        FakeRedis(),  # type: ignore[arg-type]
        incidents._pool,
        incidents,
        None,  # type: ignore[arg-type]
        None,
        Settings(require_chaos_token=False),
        on_change=on_change,
    )
    created = await inj.inject("inventory-api", "latency", 250)
    assert batches[0][0]["injection_id"] == created.injection_id
    assert batches[0][0]["failure_type"] == "latency"
    await inj.clear(created.injection_id)
    assert batches[1] == []
