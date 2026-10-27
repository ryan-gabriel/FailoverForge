from __future__ import annotations

from app.aggregator import derive_service_state, p95_from_buckets, window_metrics


def test_p95_from_buckets_empty() -> None:
    assert p95_from_buckets([0, 10, 100], [0, 0, 0]) is None


def test_p95_from_buckets_mid() -> None:
    buckets = [0, 10, 25, 50, 100]
    counts = [50, 40, 5, 3, 2]  # 100 total; p95 → 50th? cum: 50,90,95 → 25
    assert p95_from_buckets(buckets, counts) == 25.0


def test_p95_from_buckets_overflow_falls_back_to_max() -> None:
    buckets = [0, 10]
    counts = [1, 1]  # never reaches 0.95*2=1.9 → cum 1 then 2 → reaches at 10
    assert p95_from_buckets(buckets, counts) == 10.0


def test_window_metrics_first_tick_is_baseline() -> None:
    # First tick has no previous sample: rps/error_rate report 0 (no fake
    # spike from lifetime counters) while p95 uses the cumulative window.
    cur = {
        "requests_total": 100,
        "requests_error": 5,
        "in_flight": 2,
        "latency_ms": {"buckets": [0, 10, 100], "counts": [50, 40, 10], "count": 100},
    }
    m = window_metrics(None, cur, dt=1.0)
    assert m["rps"] == 0.0
    assert m["error_rate"] == 0.0
    assert m["p95_ms"] == 100.0
    assert m["in_flight"] == 2


def test_window_metrics_deltas() -> None:
    prev = {
        "requests_total": 100,
        "requests_error": 5,
        "in_flight": 0,
        "latency_ms": {"buckets": [0, 10, 100], "counts": [50, 40, 10], "count": 100},
    }
    cur = {
        "requests_total": 130,
        "requests_error": 11,
        "in_flight": 1,
        "latency_ms": {"buckets": [0, 10, 100], "counts": [55, 45, 20], "count": 120},
    }
    m = window_metrics(prev, cur, dt=2.0)
    assert m["rps"] == 15.0
    assert m["error_rate"] == 0.2
    # window counts [5,5,10] → cum 5,10,20 → p95 at 100
    assert m["p95_ms"] == 100.0


def test_derive_service_state_transitions() -> None:
    ok = {"error_rate": 0.0, "p95_ms": 20.0}
    assert derive_service_state(True, None, "healthy") == "down"
    assert derive_service_state(False, ok, "healthy") == "healthy"
    assert derive_service_state(False, ok, "down") == "recovering"
    bad_err = {"error_rate": 0.5, "p95_ms": 20.0}
    bad_lat = {"error_rate": 0.0, "p95_ms": 2000.0}
    assert derive_service_state(False, bad_err, "healthy") == "degraded"
    assert derive_service_state(False, bad_lat, "healthy") == "degraded"
