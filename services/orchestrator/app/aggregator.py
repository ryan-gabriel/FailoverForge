from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import httpx

from app.config import Settings
from app.detector import Detector
from app.queue import fetch_depths

log = logging.getLogger("orchestrator.aggregator")

SERVICE_URLS = {
    "checkout-api": "checkout_api_url",
    "inventory-api": "inventory_api_url",
    "fulfillment-worker": "worker_api_url",
}

# Rollups live in an in-memory ring (deque), not Postgres/Redis: reports are
# reconstructed from the event store alone (replayability invariant), so
# rollups are a convenience for live charts only — losing them on restart is
# acceptable for the demo (PRD §10.4).
RING_MAX_TICKS = 900  # 15 minutes at 1 tick/s

DEGRADED_ERROR_RATE = 0.2
DEGRADED_P95_MS = 1500.0


def p95_from_buckets(buckets: list[int], counts: list[int]) -> float | None:
    total = sum(counts)
    if total == 0:
        return None
    target = 0.95 * total
    cumulative = 0
    for edge, count in zip(buckets, counts):
        cumulative += count
        if cumulative >= target:
            return float(edge)
    return float(buckets[-1])


def window_metrics(prev: dict | None, cur: dict, dt: float) -> dict:
    total = cur["requests_total"]
    errors = cur["requests_error"]
    prev_total = prev["requests_total"] if prev else total
    prev_errors = prev["requests_error"] if prev else errors
    requests = max(0, total - prev_total)
    error_count = max(0, errors - prev_errors)
    rps = requests / dt if dt > 0 else 0.0
    error_rate = error_count / requests if requests else 0.0
    cur_counts = cur["latency_ms"]["counts"]
    prev_counts = prev["latency_ms"]["counts"] if prev else [0] * len(cur_counts)
    window_counts = [max(0, c - p) for c, p in zip(cur_counts, prev_counts)]
    p95 = p95_from_buckets(cur["latency_ms"]["buckets"], window_counts)
    return {
        "rps": round(rps, 2),
        "error_rate": round(error_rate, 3),
        "p95_ms": p95,
        "in_flight": cur["in_flight"],
    }


def derive_service_state(
    unreachable: bool, metrics: dict | None, prev_state: str
) -> str:
    if unreachable:
        return "down"
    assert metrics is not None
    if metrics["error_rate"] > DEGRADED_ERROR_RATE or (
        metrics["p95_ms"] is not None and metrics["p95_ms"] > DEGRADED_P95_MS
    ):
        return "degraded"
    if prev_state == "down":
        return "recovering"
    return "healthy"


class Aggregator:
    """Polls SUT /stats + RabbitMQ depths every 1s; builds dashboard ticks."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        settings: Settings,
        detector: Detector | None = None,
    ) -> None:
        self._http = http
        self._settings = settings
        self._detector = detector
        self._prev: dict[str, dict] = {}
        self._prev_ts: float | None = None
        self._node_states: dict[str, str] = {s: "healthy" for s in SERVICE_URLS}
        self.ring: deque[dict] = deque(maxlen=RING_MAX_TICKS)
        self.latest: dict | None = None
        # Phase 2 wires the WS hub here; None = no subscribers yet.
        self.on_tick: Callable[[dict], Awaitable[None]] | None = None

    async def tick(self) -> dict:
        now = time.time()
        dt = now - self._prev_ts if self._prev_ts else 1.0
        self._prev_ts = now

        results = await asyncio.gather(
            *(
                self._fetch_stats(getattr(self._settings, url_attr))
                for url_attr in SERVICE_URLS.values()
            ),
            return_exceptions=True,
        )

        services: dict[str, dict] = {}
        cache: dict = {"hit_rate": 0.0, "hits": 0, "misses": 0}
        for (service, _), result in zip(SERVICE_URLS.items(), results):
            if isinstance(result, BaseException):
                log.warning("stats poll failed for %s: %s", service, result)
                self._node_states[service] = derive_service_state(
                    True, None, self._node_states.get(service, "healthy")
                )
                continue
            metrics = window_metrics(self._prev.get(service), result, dt)
            self._prev[service] = result
            services[service] = metrics
            self._node_states[service] = derive_service_state(
                False, metrics, self._node_states.get(service, "healthy")
            )
            if service == "checkout-api":
                hits = result["cache"]["hits"]
                misses = result["cache"]["misses"]
                total = hits + misses
                cache = {
                    "hit_rate": round(hits / total, 3) if total else 0.0,
                    "hits": hits,
                    "misses": misses,
                }

        try:
            queue = await fetch_depths(self._http, self._settings.rabbitmq_mgmt_url)
            self._node_states["rabbitmq"] = "healthy"
        except httpx.HTTPError as exc:
            log.warning("queue depth poll failed: %s", exc)
            queue = {"depth": None, "consumers": None, "dlq_depth": None}
            self._node_states["rabbitmq"] = "down"

        tick = {
            "ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "services": services,
            "cache": cache,
            "queue": queue,
            "node_states": dict(self._node_states),
        }
        self.latest = tick
        self.ring.append(tick)
        if self._detector is not None:
            try:
                await self._detector.evaluate(tick)
            except Exception:
                log.exception("detector evaluation failed")
        if self.on_tick is not None:
            try:
                await self.on_tick(tick)
            except Exception:
                log.exception("on_tick broadcast failed")
        return tick

    async def run(self) -> None:
        while True:
            started = time.monotonic()
            try:
                await self.tick()
            except Exception:
                log.exception("aggregator tick failed")
            elapsed = time.monotonic() - started
            await asyncio.sleep(max(0.0, 1.0 - elapsed))

    async def _fetch_stats(self, base_url: str) -> dict:
        resp = await self._http.get(f"{base_url}/stats")
        resp.raise_for_status()
        return resp.json()
