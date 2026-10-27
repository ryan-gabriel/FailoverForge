from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager

import aio_pika
import asyncpg
import httpx
from fastapi import FastAPI
from redis import asyncio as aioredis

from app.aggregator import Aggregator
from app.config import settings
from app.detector import Detector
from app.eventstore import IncidentState, set_listener
from app.injector import Injector
from app.loadgen import LoadRunner
from app.queue import DLQ, MAIN_QUEUE
from app.routers import incidents, injection, load, metrics, topology
from app.ws import Hub
from app.ws import router as ws_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=5)
    redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
    stats_http = httpx.AsyncClient(timeout=1.5)
    load_http = httpx.AsyncClient(timeout=5.0)
    mq_connection = await _connect_mq_with_retry()
    mq_channel = await mq_connection.channel()
    await mq_channel.declare_queue(MAIN_QUEUE, durable=True)
    await mq_channel.declare_queue(DLQ, durable=True)

    hub = Hub()

    async def broadcast_tick(tick: dict) -> None:
        await hub.broadcast("metrics.tick", tick)
        queue = tick.get("queue") or {}
        await hub.broadcast(
            "queue.update",
            {"depth": queue.get("depth"), "dlq_depth": queue.get("dlq_depth")},
        )

    async def broadcast_injections(injections: list[dict]) -> None:
        await hub.broadcast("injection.status", injections)

    async def broadcast_event(event: dict) -> None:
        await hub.broadcast("timeline.event", event)
        if event["event_type"] in ("INCIDENT_OPENED", "INCIDENT_CLOSED"):
            opened = event["event_type"] == "INCIDENT_OPENED"
            await hub.broadcast(
                "incident.status",
                {
                    "incident_id": event["incident_id"],
                    "status": "open" if opened else "closed",
                    "opened_at": event["ts"] if opened else None,
                    "closed_at": None if opened else event["ts"],
                },
            )

    incidents_state = IncidentState(pool)
    injector = Injector(
        redis_client,
        pool,
        incidents_state,
        stats_http,
        mq_channel,
        settings,
        on_change=broadcast_injections,
    )
    detector = Detector(
        pool, incidents_state, has_injections=lambda: bool(injector.active)
    )
    aggregator = Aggregator(stats_http, settings, detector)
    aggregator.on_tick = broadcast_tick
    loadgen = LoadRunner(load_http, incidents_state, pool, settings.checkout_api_url)
    set_listener(broadcast_event)

    app.state.settings = settings
    app.state.pool = pool
    app.state.redis = redis_client
    app.state.http = stats_http
    app.state.aggregator = aggregator
    app.state.injector = injector
    app.state.loadgen = loadgen
    app.state.incidents = incidents_state
    app.state.ws_hub = hub

    aggregator_task = asyncio.create_task(aggregator.run())
    yield

    # Graceful shutdown (PRD Phase 4): stop producing work first, drain clients,
    # then release every resource. Each step is best-effort so one failure never
    # skips the rest.
    set_listener(None)
    aggregator_task.cancel()
    try:
        await aggregator_task
    except asyncio.CancelledError:
        pass
    await _best_effort(loadgen.stop)
    await _best_effort(hub.close_all)
    await _best_effort(stats_http.aclose)
    await _best_effort(load_http.aclose)
    await _best_effort(mq_connection.close)
    await _best_effort(redis_client.aclose)
    await _best_effort(pool.close)


async def _best_effort(close: Callable[[], Awaitable[object]]) -> None:
    try:
        await close()
    except Exception:  # shutdown continues even if one resource misbehaves
        logging.getLogger("orchestrator").exception("shutdown step failed")


async def _connect_mq_with_retry(
    attempts: int = 15, delay_s: float = 2.0
) -> aio_pika.abc.AbstractRobustConnection:
    """Tolerate RabbitMQ still coming up so a restored-service start does not
    crash-loop the orchestrator (the healthcheck pings reachable-before-ready)."""
    log = logging.getLogger("orchestrator")
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await aio_pika.connect_robust(settings.rabbitmq_url)
        except Exception as exc:  # noqa: BLE001 — aio-pika raises varied connect errors
            last = exc
            log.warning(
                "amqp connect failed (attempt %d/%d): %s", attempt, attempts, exc
            )
            await asyncio.sleep(delay_s)
    assert last is not None
    raise last


app = FastAPI(title="orchestrator", lifespan=lifespan)

app.include_router(injection.router)
app.include_router(load.router)
app.include_router(incidents.router)
app.include_router(topology.router)
app.include_router(metrics.router)
app.include_router(ws_router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
