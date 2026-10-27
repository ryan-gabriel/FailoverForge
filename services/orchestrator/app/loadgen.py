from __future__ import annotations

import asyncio
import math
import random
import time
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime

import asyncpg
import httpx

from app import eventstore
from app.eventstore import AMBIENT_INCIDENT_ID, IncidentState

PROFILES = ("mixed", "write", "read")
SKUS = ("SKU-1001", "SKU-1002", "SKU-1003")


class LoadAlreadyRunning(Exception):
    pass


def pacing_interval(rps: int) -> float:
    if rps < 1:
        raise ValueError("rps must be >= 1")
    return 1.0 / rps


def slot_count(rps: int, duration_s: float) -> int:
    """Whole request slots k with k/rps < duration_s (epsilon guards 0.1*10)."""
    return max(0, math.ceil(duration_s * rps - 1e-9))


class Pacer:
    """Drift-corrected request scheduler: yields one tick per request slot.

    Slot k fires at start + k/rps on an absolute schedule, so a 10 RPS run
    over 1.0s issues exactly 10 ticks (no boundary over-yield) and drift
    cannot accumulate. clock/sleep are injectable for unit tests.
    """

    def __init__(
        self,
        rps: int,
        duration_s: float,
        clock: Callable[[], float] = time.perf_counter,
        sleep: Callable[[float], AsyncIterator | object] | None = None,
    ) -> None:
        self.rps = rps
        self.interval = pacing_interval(rps)
        self.duration_s = duration_s
        self._clock = clock
        self._sleep = sleep if sleep is not None else asyncio.sleep

    async def ticks(self) -> AsyncIterator[None]:
        start = self._clock()
        for slot in range(slot_count(self.rps, self.duration_s)):
            delay = (start + slot * self.interval) - self._clock()
            if delay > 0:
                await self._sleep(delay)  # type: ignore[misc]
            yield None


def pick_request(
    profile: str,
    known_ids: list[str],
    rand: Callable[[], float] = random.random,
) -> tuple[str, str, dict | None]:
    """Returns (method, path, json_body) for one loadgen request.

    GET /orders/{id} needs a known order id; until one exists (or under a
    write-heavy mix) the slot falls back to POST so traffic never stalls.
    """

    def post() -> tuple[str, str, dict | None]:
        sku = SKUS[int(rand() * len(SKUS)) % len(SKUS)]
        return ("POST", "/orders", {"sku": sku, "qty": 1, "customer_ref": "loadgen"})

    def get() -> tuple[str, str, dict | None]:
        if not known_ids:
            return post()
        order_id = known_ids[int(rand() * len(known_ids)) % len(known_ids)]
        return ("GET", f"/orders/{order_id}", None)

    if profile == "write":
        return post()
    if profile == "read":
        return get()
    # mixed: 70% GET, 30% POST (API_SPEC §2.2)
    return get() if rand() >= 0.3 else post()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class LoadRunner:
    """Owns at most one load run; every start/stop is an event-store append.

    Load events attach to the open incident when one exists, otherwise they go
    to the ambient stream (AMBIENT_INCIDENT_ID) so they are always recorded
    without inventing fake incidents.
    """

    def __init__(
        self,
        http: httpx.AsyncClient,
        incidents: IncidentState,
        pool: asyncpg.Pool,
        checkout_url: str,
    ) -> None:
        self._http = http
        self._incidents = incidents
        self._pool = pool
        self._checkout_url = checkout_url
        self.task: asyncio.Task | None = None
        self.status: dict = {
            "load_id": None,
            "status": "stopped",
            "profile": None,
            "rps": None,
            "started_at": None,
            "completed_requests": 0,
            "errors": 0,
        }

    @property
    def is_running(self) -> bool:
        return self.task is not None and not self.task.done()

    async def start(self, profile: str, rps: int, duration_s: int) -> dict:
        if self.is_running:
            raise LoadAlreadyRunning()
        if profile not in PROFILES:
            raise ValueError(f"unknown profile: {profile}")

        load_id = str(uuid.uuid4())
        incident_id = self._incidents.current_id or AMBIENT_INCIDENT_ID
        self.status = {
            "load_id": load_id,
            "status": "running",
            "profile": profile,
            "rps": rps,
            "started_at": _now_iso(),
            "completed_requests": 0,
            "errors": 0,
        }
        await eventstore.append(
            self._pool,
            incident_id,
            "LOAD_STARTED",
            {"profile": profile, "rps": rps, "duration_s": duration_s},
        )
        self.task = asyncio.create_task(
            self._run(load_id, profile, rps, duration_s, incident_id)
        )
        return dict(self.status)

    async def stop(self) -> dict:
        if self.task is not None and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        self.status["status"] = "stopped"
        return dict(self.status)

    async def _run(
        self,
        load_id: str,
        profile: str,
        rps: int,
        duration_s: int,
        incident_id,
    ) -> None:
        known_ids: list[str] = []
        try:
            pacer = Pacer(rps, duration_s)
            async for _ in pacer.ticks():
                method, path, body = pick_request(profile, known_ids)
                try:
                    resp = await self._http.request(
                        method, f"{self._checkout_url}{path}", json=body
                    )
                    self.status["completed_requests"] += 1
                    if resp.status_code >= 400:
                        self.status["errors"] += 1
                    elif method == "POST":
                        known_ids.append(resp.json()["order_id"])
                except (httpx.HTTPError, KeyError, ValueError):
                    self.status["completed_requests"] += 1
                    self.status["errors"] += 1
        finally:
            self.status["status"] = "stopped"
            await eventstore.append(
                self._pool,
                incident_id,
                "LOAD_STOPPED",
                {
                    "profile": profile,
                    "rps": rps,
                    "duration_s": duration_s,
                    "completed_requests": self.status["completed_requests"],
                    "errors": self.status["errors"],
                    "load_id": load_id,
                },
            )
