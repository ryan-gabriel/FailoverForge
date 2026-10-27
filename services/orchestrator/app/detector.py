from __future__ import annotations

import logging
import time
from collections.abc import Callable

import asyncpg

from app import eventstore
from app.eventstore import IncidentState

log = logging.getLogger("orchestrator.detector")

THRESHOLDS = {
    "error_rate": 0.2,
    "p95_ms": 1000.0,
    "queue_depth": 100,
}
COOLDOWN_S = 10.0  # per (service, metric) re-emit throttle


class Detector:
    """Heuristic threshold detector over aggregator ticks (PRD §7).

    Opens an incident on first threshold breach when none is open; emits
    SYMPTOM_DETECTED events (rate-limited per service+metric). Closes a
    threshold-opened incident once every metric has recovered AND no
    injections are active (Phase 3 lifecycle: "close on clear + healthy").
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        incidents: IncidentState,
        has_injections: Callable[[], bool] | None = None,
    ) -> None:
        self._pool = pool
        self._incidents = incidents
        self._has_injections = has_injections
        self._last_emitted: dict[tuple[str, str], float] = {}
        self._active_breaches: set[tuple[str, str]] = set()

    async def evaluate(self, tick: dict) -> None:
        for service, metrics in tick["services"].items():
            await self._maybe_emit(service, "error_rate", metrics["error_rate"])
            p95 = metrics.get("p95_ms")
            if p95 is not None:
                await self._maybe_emit(service, "p95_ms", p95)
        depth = tick["queue"].get("depth")
        if depth is not None:
            await self._maybe_emit("rabbitmq", "queue_depth", float(depth))
        else:
            self._active_breaches.discard(("rabbitmq", "queue_depth"))
        await self._maybe_close()

    async def _maybe_emit(self, service: str, metric: str, observed: float) -> None:
        threshold = THRESHOLDS[metric]
        key = (service, metric)
        if observed <= threshold:
            self._active_breaches.discard(key)
            return
        self._active_breaches.add(key)
        now = time.monotonic()
        if now - self._last_emitted.get(key, 0.0) < COOLDOWN_S:
            return
        self._last_emitted[key] = now
        if not self._incidents.is_open:
            await self._incidents.open(
                "threshold", title=f"{metric} breach on {service}"
            )
        incident_id = self._incidents.current_id
        if incident_id is None:  # open() above always assigns; stay fail-open
            return
        await eventstore.append(
            self._pool,
            incident_id,
            "SYMPTOM_DETECTED",
            {
                "service": service,
                "metric": metric,
                "observed": round(observed, 3),
                "threshold": threshold,
            },
        )

    async def _maybe_close(self) -> None:
        if self._active_breaches or not self._incidents.is_open:
            return
        if self._incidents.trigger != "threshold":
            return  # injection incidents close via Injector.clear (PRD "close on clear")
        if self._has_injections is not None and self._has_injections():
            return
        await self._incidents.close(summary="threshold recovered, no injections active")
        log.info("threshold incident closed on recovery")
