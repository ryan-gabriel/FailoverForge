from __future__ import annotations

import asyncio
import uuid

import httpx
import pytest

from app.eventstore import AMBIENT_INCIDENT_ID
from app.loadgen import (
    LoadAlreadyRunning,
    LoadRunner,
    Pacer,
    pacing_interval,
    pick_request,
    slot_count,
)
from tests.conftest import FakePool


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.t += seconds


async def test_pacer_yields_rps_times_duration() -> None:
    clock = FakeClock()
    pacer = Pacer(rps=10, duration_s=1.0, clock=clock, sleep=clock.sleep)
    ticks = 0
    async for _ in pacer.ticks():
        ticks += 1
    assert ticks == 10
    assert clock.t == pytest.approx(0.9)  # 10th slot fires at start + 0.9s
    assert clock.t < 1.0  # never paces past the duration


async def test_pacer_single_request_slot() -> None:
    clock = FakeClock()
    pacer = Pacer(rps=1, duration_s=1.0, clock=clock, sleep=clock.sleep)
    ticks = 0
    async for _ in pacer.ticks():
        ticks += 1
    assert ticks == 1


def test_pacing_interval() -> None:
    assert pacing_interval(10) == 0.1
    assert pacing_interval(4) == 0.25
    with pytest.raises(ValueError):
        pacing_interval(0)


def test_slot_count_rounds_whole_slots() -> None:
    assert slot_count(10, 1.0) == 10  # exact division, no float over-yield
    assert slot_count(1, 1.0) == 1
    assert slot_count(10, 0.05) == 1  # first slot at t=0 always fits
    assert slot_count(10, 1.05) == 11  # slot at t=1.0 still inside window
    assert slot_count(10, 0.0) == 0


def test_pick_request_write_profile() -> None:
    for _ in range(20):
        method, path, body = pick_request("write", ["x"], rand=lambda: 0.99)
        assert method == "POST" and path == "/orders"
        assert body is not None and body["qty"] == 1


def test_pick_request_read_uses_known_ids() -> None:
    method, path, _ = pick_request("read", ["a", "b", "c"], rand=lambda: 0.0)
    assert method == "GET" and path == "/orders/a"


def test_pick_request_read_warmup_without_ids() -> None:
    method, _path, body = pick_request("read", [], rand=lambda: 0.0)
    assert method == "POST" and body is not None


def test_pick_request_mixed_ratio() -> None:
    posts = sum(
        1
        for i in range(1000)
        if pick_request("mixed", ["id-1"], rand=lambda i=i: (i % 100) / 100.0)[0]
        == "POST"
    )
    assert 250 <= posts <= 350  # ~30% POST


class FakeIncidents:
    def __init__(self) -> None:
        self.current_id = None


def make_runner(monkeypatch: pytest.MonkeyPatch, responses: list[httpx.Response]):
    events: list[tuple] = []

    async def fake_append(pool, incident_id, event_type, payload):
        events.append((incident_id, event_type, payload))
        return {}

    class InstantPacer:
        def __init__(self, rps, duration_s) -> None:
            pass

        async def ticks(self):
            for _ in range(3):
                yield None

    monkeypatch.setattr("app.loadgen.Pacer", InstantPacer)
    monkeypatch.setattr("app.loadgen.eventstore.append", fake_append)

    def handler(request: httpx.Request) -> httpx.Response:
        return (
            responses.pop(0)
            if responses
            else httpx.Response(201, json={"order_id": "oid"})
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    incidents = FakeIncidents()
    runner = LoadRunner(http, incidents, FakePool(), "http://checkout")  # type: ignore[arg-type]
    runner._events = events  # type: ignore[attr-defined]
    return runner


async def test_loadrunner_counts_and_emits_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = make_runner(monkeypatch, [])
    status = await runner.start("write", rps=100, duration_s=1)
    assert status["status"] == "running"
    await asyncio.wait_for(runner.task, timeout=2)
    final = runner.status
    assert final["status"] == "stopped"
    assert final["completed_requests"] == 3
    assert final["errors"] == 0
    types = [e[1] for e in runner._events]  # type: ignore[attr-defined]
    assert types == ["LOAD_STARTED", "LOAD_STOPPED"]
    # no open incident → ambient stream
    assert runner._events[0][0] == AMBIENT_INCIDENT_ID  # type: ignore[attr-defined]


async def test_loadrunner_counts_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = [
        httpx.Response(503, json={"detail": "injected error"}),
        httpx.Response(503, json={"detail": "injected error"}),
        httpx.Response(201, json={"order_id": "o1"}),
    ]
    runner = make_runner(monkeypatch, responses)
    await runner.start("write", rps=100, duration_s=1)
    await asyncio.wait_for(runner.task, timeout=2)
    assert runner.status["completed_requests"] == 3
    assert runner.status["errors"] == 2


async def test_loadrunner_second_start_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SlowPacer:
        def __init__(self, rps, duration_s) -> None:
            pass

        async def ticks(self):
            await asyncio.sleep(30)

    monkeypatch.setattr("app.loadgen.Pacer", SlowPacer)

    async def fake_append(pool, incident_id, event_type, payload):
        return {}

    monkeypatch.setattr("app.loadgen.eventstore.append", fake_append)
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(201, json={}))
    )
    runner = LoadRunner(http, FakeIncidents(), FakePool(), "http://checkout")  # type: ignore[arg-type]
    await runner.start("mixed", rps=5, duration_s=30)
    with pytest.raises(LoadAlreadyRunning):
        await runner.start("mixed", rps=5, duration_s=30)
    stopped = await runner.stop()
    assert stopped["status"] == "stopped"


async def test_loadrunner_rejects_unknown_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = make_runner(monkeypatch, [])
    with pytest.raises(ValueError):
        await runner.start("bogus", rps=1, duration_s=1)


async def test_load_events_attach_to_open_incident(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = make_runner(monkeypatch, [])
    open_id = uuid.uuid4()
    runner._incidents.current_id = open_id  # type: ignore[attr-defined]
    await runner.start("write", rps=100, duration_s=1)
    await asyncio.wait_for(runner.task, timeout=2)
    assert runner._events[0][0] == open_id  # type: ignore[attr-defined]
