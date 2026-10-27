from __future__ import annotations

import uuid

from app.reports import generate_report

INCIDENT_ID = "80738d37-1ad6-4dcc-bc12-ae691316125b"


def _event(seq: int, event_type: str, payload: dict, ts: str | None = None) -> dict:
    return {
        "id": seq,
        "seq": seq,
        "event_type": event_type,
        "ts": ts or f"2026-10-20T12:00:0{seq}Z",
        "payload": payload,
    }


def injection_events() -> list[dict]:
    return [
        _event(
            1,
            "INCIDENT_OPENED",
            {"trigger": "injection", "title": "latency on inventory-api"},
        ),
        _event(
            2,
            "FAILURE_INJECTED",
            {
                "service": "inventory-api",
                "failure_type": "latency",
                "intensity": 400,
                "ttl_s": 300,
            },
        ),
        _event(
            3,
            "SYMPTOM_DETECTED",
            {
                "service": "checkout-api",
                "metric": "p95_ms",
                "observed": 1500.0,
                "threshold": 1000.0,
            },
        ),
        _event(
            4,
            "MITIGATION_APPLIED",
            {"service": "inventory-api", "failure_type": "latency"},
        ),
        _event(
            5, "INCIDENT_CLOSED", {"mttr_s": 84.0, "summary": "all injections cleared"}
        ),
    ]


def test_report_from_injection_incident() -> None:
    report = generate_report(INCIDENT_ID, injection_events())
    assert report["incident_id"] == INCIDENT_ID
    assert report["title"] == "latency on inventory-api"
    assert report["root_cause"] == {
        "service": "inventory-api",
        "failure_type": "latency",
        "intensity": 400,
    }
    assert report["blast_radius"] == ["inventory-api", "checkout-api"]
    assert report["mttr_s"] == 84.0
    assert report["timeline_summary"] == [
        "INCIDENT_OPENED",
        "FAILURE_INJECTED",
        "SYMPTOM_DETECTED",
        "MITIGATION_APPLIED",
        "INCIDENT_CLOSED",
    ]
    md = report["markdown"]
    assert md.startswith("# Incident report: latency on inventory-api")
    assert "## Root cause" in md and "latency (intensity 400)" in md
    assert "## Blast radius" in md and "`inventory-api`" in md
    assert "**MTTR:** 84s" in md
    assert "`#2`" in md and "FAILURE_INJECTED" in md


def test_report_root_cause_for_threshold_only_incident() -> None:
    events = [
        _event(
            1,
            "INCIDENT_OPENED",
            {"trigger": "threshold", "title": "queue_depth breach on rabbitmq"},
        ),
        _event(
            2,
            "SYMPTOM_DETECTED",
            {
                "service": "rabbitmq",
                "metric": "queue_depth",
                "observed": 239,
                "threshold": 100,
            },
        ),
        _event(
            3, "INCIDENT_CLOSED", {"mttr_s": 12.5, "summary": "threshold recovered"}
        ),
    ]
    report = generate_report(INCIDENT_ID, events)
    assert report["root_cause"] == {
        "service": "rabbitmq",
        "failure_type": "threshold:queue_depth",
        "intensity": 239,
    }
    assert report["blast_radius"] == ["rabbitmq"]
    assert report["mttr_s"] == 12.5


def test_report_open_incident_without_close() -> None:
    events = [
        _event(1, "INCIDENT_OPENED", {"trigger": "injection", "title": None}),
        _event(
            2,
            "FAILURE_INJECTED",
            {
                "service": "checkout-api",
                "failure_type": "error",
                "intensity": 0.5,
                "ttl_s": 60,
            },
        ),
    ]
    report = generate_report(INCIDENT_ID, events)
    assert report["mttr_s"] is None
    assert "- **MTTR:** —" in report["markdown"]
    assert "(still open)" in report["markdown"]
    # title falls back to the short id when the payload title is null
    assert report["title"] == f"incident {INCIDENT_ID[:8]}"


def test_report_with_no_events() -> None:
    report = generate_report(str(uuid.uuid4()), [])
    assert report["root_cause"] is None
    assert report["blast_radius"] == []
    assert "No injection or symptom recorded" in report["markdown"]
