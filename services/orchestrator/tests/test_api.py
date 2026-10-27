from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.injector import Injection, InjectionRejected
from app.main import app


class FakeInjector:
    def __init__(self) -> None:
        self.active: dict[str, Injection] = {}
        self.raise_rejected: str | None = None

    async def inject(self, service, failure_type, intensity, ttl_s=None) -> Injection:
        if self.raise_rejected:
            raise InjectionRejected(self.raise_rejected)
        inj = Injection(
            injection_id="inj_test",
            service=service,
            failure_type=failure_type,
            intensity=intensity,
            ttl_s=ttl_s or 300,
            created_at="2026-10-20T12:00:00Z",
        )
        self.active[inj.injection_id] = inj
        return inj

    async def clear(self, injection_id: str) -> Injection:
        return self.active.pop(injection_id)

    async def clear_all(self) -> int:
        n = len(self.active)
        self.active.clear()
        return n


class FakeLoadgen:
    def __init__(self) -> None:
        self.running = False
        self.status = {
            "load_id": None,
            "status": "stopped",
            "profile": None,
            "rps": None,
            "started_at": None,
            "completed_requests": 0,
            "errors": 0,
        }
        self.raise_conflict = False

    async def start(self, profile, rps, duration_s):
        if self.raise_conflict:
            from app.loadgen import LoadAlreadyRunning

            raise LoadAlreadyRunning()
        self.status.update(
            {"load_id": "l1", "status": "running", "profile": profile, "rps": rps}
        )
        return dict(self.status)

    async def stop(self):
        self.status["status"] = "stopped"
        return dict(self.status)


class FakeAggregator:
    def __init__(self) -> None:
        self.latest: dict | None = {
            "ts": "2026-10-20T12:00:00Z",
            "services": {},
            "cache": {"hit_rate": 0.0, "hits": 0, "misses": 0},
            "queue": {"depth": 3, "consumers": 1, "dlq_depth": 0},
            "node_states": {"inventory-api": "degraded"},
        }


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch):
    app.state.settings = Settings(require_chaos_token=False)
    app.state.injector = FakeInjector()
    app.state.loadgen = FakeLoadgen()
    app.state.aggregator = FakeAggregator()
    app.state.pool = object()
    app.state.http = object()
    return TestClient(app)


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_injection_roundtrip(client: TestClient) -> None:
    resp = client.post(
        "/api/injections",
        json={"service": "inventory-api", "failure_type": "latency", "intensity": 400},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["injection_id"] == "inj_test"
    assert client.get("/api/injections").json()[0]["failure_type"] == "latency"
    assert client.delete("/api/injections/inj_test").status_code == 200
    assert client.get("/api/injections").json() == []


def test_injection_validation_422(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    app.state.injector.raise_rejected = "cache_outage is only valid on checkout-api"
    resp = client.post(
        "/api/injections",
        json={"service": "inventory-api", "failure_type": "cache_outage"},
    )
    assert resp.status_code == 422


def test_clear_all(client: TestClient) -> None:
    client.post(
        "/api/injections",
        json={"service": "checkout-api", "failure_type": "latency", "intensity": 100},
    )
    resp = client.delete("/api/injections")
    assert resp.json() == {"cleared": 1}


def test_chaos_token_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    app.state.settings = Settings(require_chaos_token=True, chaos_token="sekrit")
    app.state.injector = FakeInjector()
    app.state.loadgen = FakeLoadgen()
    app.state.aggregator = FakeAggregator()
    client = TestClient(app)

    gated = client.post(
        "/api/injections",
        json={"service": "checkout-api", "failure_type": "latency", "intensity": 100},
    )
    assert gated.status_code == 403

    ok = client.post(
        "/api/injections",
        json={"service": "checkout-api", "failure_type": "latency", "intensity": 100},
        headers={"X-Chaos-Token": "sekrit"},
    )
    assert ok.status_code == 201


def test_load_start_stop_status(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = client.post(
        "/api/load", json={"profile": "mixed", "rps": 10, "duration_s": 60}
    )
    assert started.status_code == 202
    assert started.json() == {"load_id": "l1", "status": "running"}
    status = client.get("/api/load").json()
    assert status["status"] == "running"
    stopped = client.post("/api/load/stop")
    assert stopped.json() == {"status": "stopped"}


def test_load_conflict_409(client: TestClient) -> None:
    app.state.loadgen.raise_conflict = True
    resp = client.post(
        "/api/load", json={"profile": "mixed", "rps": 10, "duration_s": 60}
    )
    assert resp.status_code == 409
    assert resp.json() == {"detail": "load already running"}


def test_load_profile_validation(client: TestClient) -> None:
    resp = client.post(
        "/api/load", json={"profile": "bogus", "rps": 10, "duration_s": 60}
    )
    assert resp.status_code == 422


def test_topology_merges_health(client: TestClient) -> None:
    body = client.get("/api/topology").json()
    ids = {n["id"]: n["health"] for n in body["nodes"]}
    assert ids["inventory-api"] == "degraded"
    assert ids["checkout-api"] == "healthy"
    assert {"from": "inventory-api", "to": "checkout-api", "kind": "http"} in body[
        "edges"
    ]


def test_metrics_latest(client: TestClient) -> None:
    body = client.get("/api/metrics/latest").json()
    assert body["ts"] == "2026-10-20T12:00:00Z"


def test_metrics_latest_503_before_first_tick(monkeypatch: pytest.MonkeyPatch) -> None:
    app.state.settings = Settings(require_chaos_token=False)
    app.state.injector = FakeInjector()
    app.state.loadgen = FakeLoadgen()
    app.state.aggregator = FakeAggregator()
    app.state.aggregator.latest = None
    client = TestClient(app)
    assert client.get("/api/metrics/latest").status_code == 503


def test_incidents_list_and_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    incident_id = uuid.uuid4()

    async def fake_list(pool, limit):
        return [
            {
                "incident_id": str(incident_id),
                "status": "closed",
                "opened_at": "2026-10-20T12:00:00Z",
                "closed_at": "2026-10-20T12:01:00Z",
                "title": "latency on inventory-api",
                "event_count": 2,
            }
        ]

    async def fake_projection(pool, iid):
        return {
            "incident_id": str(incident_id),
            "title": "latency on inventory-api",
            "status": "closed",
            "opened_at": "2026-10-20T12:00:00Z",
            "closed_at": "2026-10-20T12:01:00Z",
            "mttr_s": 60.0,
        }

    async def fake_events(pool, iid):
        return [
            {
                "id": 1,
                "seq": 1,
                "event_type": "INCIDENT_OPENED",
                "ts": "t",
                "payload": {},
            },
            {
                "id": 2,
                "seq": 2,
                "event_type": "FAILURE_INJECTED",
                "ts": "t",
                "payload": {},
            },
        ]

    monkeypatch.setattr("app.eventstore.list_incidents", fake_list)
    monkeypatch.setattr("app.eventstore.get_incident_projection", fake_projection)
    monkeypatch.setattr("app.eventstore.list_events", fake_events)

    app.state.settings = Settings(require_chaos_token=False)
    app.state.injector = FakeInjector()
    app.state.loadgen = FakeLoadgen()
    app.state.aggregator = FakeAggregator()
    app.state.pool = object()
    client = TestClient(app)

    listing = client.get("/api/incidents").json()
    assert listing[0]["event_count"] == 2

    detail = client.get(f"/api/incidents/{incident_id}").json()
    assert detail["mttr_s"] == 60.0
    assert [e["seq"] for e in detail["events"]] == [1, 2]

    assert client.get("/api/incidents/not-a-uuid").status_code == 404
    assert (
        client.get("/api/incidents/00000000-0000-0000-0000-000000000000").status_code
        == 404
    )


def test_incident_report_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    incident_id = uuid.uuid4()

    async def fake_projection(pool, iid):
        return {
            "incident_id": str(incident_id),
            "title": "latency on inventory-api",
            "status": "closed",
            "opened_at": "2026-10-20T12:00:00Z",
            "closed_at": "2026-10-20T12:01:00Z",
            "mttr_s": 60.0,
        }

    async def fake_events(pool, iid):
        return [
            {
                "id": 1,
                "seq": 1,
                "event_type": "INCIDENT_OPENED",
                "ts": "t1",
                "payload": {
                    "trigger": "injection",
                    "title": "latency on inventory-api",
                },
            },
            {
                "id": 2,
                "seq": 2,
                "event_type": "FAILURE_INJECTED",
                "ts": "t2",
                "payload": {
                    "service": "inventory-api",
                    "failure_type": "latency",
                    "intensity": 400,
                    "ttl_s": 300,
                },
            },
            {
                "id": 3,
                "seq": 3,
                "event_type": "INCIDENT_CLOSED",
                "ts": "t3",
                "payload": {"mttr_s": 60.0, "summary": "cleared"},
            },
        ]

    monkeypatch.setattr("app.eventstore.get_incident_projection", fake_projection)
    monkeypatch.setattr("app.eventstore.list_events", fake_events)

    app.state.settings = Settings(require_chaos_token=False)
    app.state.injector = FakeInjector()
    app.state.loadgen = FakeLoadgen()
    app.state.aggregator = FakeAggregator()
    app.state.pool = object()
    client = TestClient(app)

    report = client.get(f"/api/incidents/{incident_id}/report").json()
    assert report["root_cause"]["service"] == "inventory-api"
    assert report["blast_radius"] == ["inventory-api"]
    assert report["mttr_s"] == 60.0
    assert report["markdown"].startswith("# Incident report:")

    assert client.get("/api/incidents/not-a-uuid/report").status_code == 404
    assert (
        client.get(
            "/api/incidents/00000000-0000-0000-0000-000000000000/report"
        ).status_code
        == 404
    )
