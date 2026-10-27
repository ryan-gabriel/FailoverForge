from fastapi.testclient import TestClient

from app import stats
from app.main import STOCK, app
from app.stock import DEFAULT_STOCK

client = TestClient(app)


def test_health() -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_stock_seeded_and_readable() -> None:
    client.post("/admin/seed")
    resp = client.get("/stock/SKU-1001")
    assert resp.status_code == 200
    assert resp.json() == {"sku": "SKU-1001", "available": DEFAULT_STOCK["SKU-1001"]}


def test_unknown_sku_404() -> None:
    assert client.get("/stock/NOPE").status_code == 404


def test_reserve_decrements() -> None:
    client.post("/admin/seed")
    resp = client.post("/stock/SKU-1001/reserve", json={"qty": 3})
    assert resp.status_code == 200
    assert resp.json() == {
        "sku": "SKU-1001",
        "reserved": 3,
        "available": DEFAULT_STOCK["SKU-1001"] - 3,
    }


def test_reserve_insufficient_409() -> None:
    client.post("/admin/seed")
    resp = client.post("/stock/SKU-1001/reserve", json={"qty": 10_000})
    assert resp.status_code == 409
    assert resp.json() == {"detail": "insufficient stock"}


def test_reserve_invalid_qty_422() -> None:
    assert client.post("/stock/SKU-1001/reserve", json={"qty": 0}).status_code == 422


def test_stats_shape_and_exclusions() -> None:
    client.get("/stock/SKU-1002")
    body = client.get("/stats").json()
    assert body["service"] == "inventory-api"
    assert set(body) == {
        "service",
        "uptime_s",
        "requests_total",
        "requests_error",
        "in_flight",
        "latency_ms",
        "injections_active",
    }
    assert body["requests_total"] >= 1
    hist = body["latency_ms"]
    assert hist["count"] == sum(hist["counts"]) or hist["count"] > sum(hist["counts"])
    assert len(hist["buckets"]) == len(hist["counts"])
    # /stats and /health must not be counted
    before = body["requests_total"]
    client.get("/health")
    client.get("/stats")
    assert client.get("/stats").json()["requests_total"] == before


def test_stock_reset_roundtrip() -> None:
    client.post("/stock/SKU-1001/reserve", json={"qty": 5})
    body = client.post("/admin/seed").json()
    assert body["seeded"] is True
    assert body["stock"]["SKU-1001"] == DEFAULT_STOCK["SKU-1001"]
    assert STOCK.get("SKU-1001") == DEFAULT_STOCK["SKU-1001"]


def test_histogram_buckets() -> None:
    s = stats.Stats()
    s.observe(3.0)
    s.observe(70.0)
    s.observe(9999.0)
    assert s.counts[stats.BUCKETS_MS.index(5)] == 1
    assert s.counts[stats.BUCKETS_MS.index(100)] == 1
    assert s.count == 3
    assert s.counts[stats.BUCKETS_MS.index(5000)] == 0
