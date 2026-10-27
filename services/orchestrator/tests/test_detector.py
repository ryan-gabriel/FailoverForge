from __future__ import annotations

import pytest

from app import eventstore
from app.detector import COOLDOWN_S, THRESHOLDS, Detector
from app.eventstore import IncidentState
from tests.conftest import FakePool


def make_tick(services: dict, queue_depth: int | None = 12) -> dict:
    return {
        "ts": "2026-10-20T12:00:00Z",
        "services": services,
        "cache": {"hit_rate": 0.0, "hits": 0, "misses": 0},
        "queue": {"depth": queue_depth, "consumers": 1, "dlq_depth": 0},
        "node_states": {},
    }


def healthy(service: str = "inventory-api") -> dict:
    return {service: {"rps": 1.0, "error_rate": 0.0, "p95_ms": 20.0, "in_flight": 0}}


@pytest.fixture()
def detector(monkeypatch: pytest.MonkeyPatch):
    events: list[tuple] = []

    async def fake_append(pool, incident_id, event_type, payload):
        events.append((incident_id, event_type, payload))
        return {}

    monkeypatch.setattr("app.eventstore.append", fake_append)
    incidents = IncidentState(FakePool())  # type: ignore[arg-type]
    det = Detector(FakePool(), incidents)  # type: ignore[arg-type]
    det._events = events  # type: ignore[attr-defined]
    return det


async def test_no_event_when_healthy(detector: Detector) -> None:
    await detector.evaluate(make_tick(healthy()))
    assert detector._events == []  # type: ignore[attr-defined]
    assert not detector._incidents.is_open  # type: ignore[attr-defined]


async def test_error_breach_opens_incident_and_emits(detector: Detector) -> None:
    tick = make_tick(
        {"inventory-api": {"rps": 5, "error_rate": 0.9, "p95_ms": 20.0, "in_flight": 0}}
    )
    await detector.evaluate(tick)
    events = detector._events  # type: ignore[attr-defined]
    types = [e[1] for e in events]
    assert types == ["INCIDENT_OPENED", "SYMPTOM_DETECTED"]
    opened = events[0][2]
    assert opened["trigger"] == "threshold"
    symptom = events[1][2]
    assert symptom == {
        "service": "inventory-api",
        "metric": "error_rate",
        "observed": 0.9,
        "threshold": THRESHOLDS["error_rate"],
    }


async def test_cooldown_suppresses_reemit(detector: Detector) -> None:
    services = {
        "inventory-api": {"rps": 5, "error_rate": 0.9, "p95_ms": 20.0, "in_flight": 0}
    }
    await detector.evaluate(make_tick(services))
    await detector.evaluate(make_tick(services))
    symptoms = [e for e in detector._events if e[1] == "SYMPTOM_DETECTED"]  # type: ignore[attr-defined]
    assert len(symptoms) == 1


async def test_queue_depth_symptom(detector: Detector) -> None:
    await detector.evaluate(make_tick(healthy(), queue_depth=500))
    symptoms = [e for e in detector._events if e[1] == "SYMPTOM_DETECTED"]  # type: ignore[attr-defined]
    assert symptoms[0][2]["service"] == "rabbitmq"
    assert symptoms[0][2]["metric"] == "queue_depth"


async def test_existing_incident_not_reopened(detector: Detector) -> None:
    await detector._incidents.open("injection", title="manual")  # type: ignore[attr-defined]
    events_before = len(detector._events)  # type: ignore[attr-defined]
    services = {
        "inventory-api": {"rps": 5, "error_rate": 0.9, "p95_ms": 20.0, "in_flight": 0}
    }
    await detector.evaluate(make_tick(services))
    new_types = [e[1] for e in detector._events[events_before:]]  # type: ignore[attr-defined]
    assert "INCIDENT_OPENED" not in new_types
    assert new_types == ["SYMPTOM_DETECTED"]


async def test_cooldown_constant_is_sane() -> None:
    assert COOLDOWN_S >= 5.0


def test_real_incident_state_still_compatible() -> None:
    state = IncidentState(FakePool())  # type: ignore[arg-type]
    assert not state.is_open
    assert isinstance(state, IncidentState)
    assert eventstore.AMBIENT_INCIDENT_ID.int == 0


async def test_threshold_incident_closes_on_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple] = []

    async def fake_append(pool, incident_id, event_type, payload):
        events.append((incident_id, event_type, payload))
        return {}

    monkeypatch.setattr("app.eventstore.append", fake_append)
    incidents = IncidentState(FakePool())  # type: ignore[arg-type]
    det = Detector(FakePool(), incidents, has_injections=lambda: False)  # type: ignore[arg-type]

    breach = make_tick(
        {"inventory-api": {"rps": 5, "error_rate": 0.9, "p95_ms": 20.0, "in_flight": 0}}
    )
    await det.evaluate(breach)
    assert incidents.is_open
    assert incidents.trigger == "threshold"

    await det.evaluate(make_tick(healthy()))
    assert not incidents.is_open
    types = [e[1] for e in events]
    assert types[-1] == "INCIDENT_CLOSED"
    assert "recovered" in events[-1][2]["summary"]


async def test_recovery_close_blocked_while_injections_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_append(pool, incident_id, event_type, payload):
        return {}

    monkeypatch.setattr("app.eventstore.append", fake_append)
    incidents = IncidentState(FakePool())  # type: ignore[arg-type]
    injection_state = {"active": True}
    det = Detector(
        FakePool(), incidents, has_injections=lambda: injection_state["active"]
    )  # type: ignore[arg-type]

    await det.evaluate(
        make_tick(
            {
                "inventory-api": {
                    "rps": 5,
                    "error_rate": 0.9,
                    "p95_ms": 20.0,
                    "in_flight": 0,
                }
            }
        )
    )
    assert incidents.is_open
    await det.evaluate(make_tick(healthy()))
    assert incidents.is_open  # injections still active: hold it open

    injection_state["active"] = False
    await det.evaluate(make_tick(healthy()))
    assert not incidents.is_open


async def test_detector_never_closes_injection_triggered_incident(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_append(pool, incident_id, event_type, payload):
        return {}

    monkeypatch.setattr("app.eventstore.append", fake_append)
    incidents = IncidentState(FakePool())  # type: ignore[arg-type]
    await incidents.open("injection", title="latency on inventory-api")
    det = Detector(FakePool(), incidents, has_injections=lambda: False)  # type: ignore[arg-type]

    await det.evaluate(make_tick(healthy()))
    assert incidents.is_open  # Injector.clear owns this close (PRD "close on clear")
    assert incidents.trigger == "injection"
