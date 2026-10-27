from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from contextlib import asynccontextmanager

import aio_pika
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from redis import asyncio as aioredis

from app import db, injection, mq, stats
from app.config import settings
from app.routes import router
from app.state import state


@asynccontextmanager
async def lifespan(app: FastAPI):
    state.pool = await db.create_pool(settings.database_url)
    state.redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    state.mq_connection = await _connect_mq_with_retry()
    state.mq_channel = await state.mq_connection.channel()
    await mq.declare_queues(state.mq_channel)
    state.http = httpx.AsyncClient(timeout=5.0)
    injection.flags = injection.FlagStore(state.redis, "checkout-api")
    yield

    # Graceful shutdown (PRD Phase 4): best-effort release of every resource so
    # one closed/failed dependency never blocks the rest of teardown.
    if state.http is not None:
        await _best_effort(state.http.aclose)
    if state.mq_connection is not None:
        await _best_effort(state.mq_connection.close)
    if state.redis is not None:
        await _best_effort(state.redis.aclose)
    if state.pool is not None:
        await _best_effort(state.pool.close)


async def _best_effort(close) -> None:
    try:
        await close()
    except Exception:  # shutdown continues even if one resource misbehaves
        logging.getLogger("checkout-api").exception("shutdown step failed")


async def _connect_mq_with_retry(
    attempts: int = 15, delay_s: float = 2.0
) -> aio_pika.abc.AbstractRobustConnection:
    """Tolerate RabbitMQ still coming up so a restored-service start does not
    crash-loop the app (the healthcheck pings reachable-before-ready)."""
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await aio_pika.connect_robust(settings.rabbitmq_url)
        except Exception as exc:  # noqa: BLE001 — aio-pika raises varied connect errors
            last = exc
            logging.getLogger("checkout-api").warning(
                "amqp connect failed (attempt %d/%d): %s", attempt, attempts, exc
            )
            await asyncio.sleep(delay_s)
    assert last is not None
    raise last


app = FastAPI(title="checkout-api", lifespan=lifespan)
app.state.settings = settings


@app.middleware("http")  # innermost: injected 503s still pass through stats below
async def injection_middleware(request: Request, call_next):
    if stats.stats.is_excluded(request.url.path) or injection.flags is None:
        return await call_next(request)
    latency_ms = await injection.flags.latency_ms()
    error_ratio = await injection.flags.error_ratio()
    if latency_ms > 0:
        await asyncio.sleep(latency_ms / 1000)
    if error_ratio > 0 and random.random() < error_ratio:
        return JSONResponse(
            status_code=503,
            content={"detail": "injected error"},
            headers={"X-Injection": "error"},
        )
    return await call_next(request)


@app.middleware("http")  # outermost: counts everything the injection layer produces
async def stats_middleware(request: Request, call_next):
    if stats.stats.is_excluded(request.url.path):
        return await call_next(request)
    stats.stats.begin()
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        stats.stats.end(500, (time.perf_counter() - start) * 1000)
        raise
    stats.stats.end(response.status_code, (time.perf_counter() - start) * 1000)
    return response


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/stats")
async def get_stats() -> dict:
    active = await injection.flags.active_injections() if injection.flags else []
    return stats.stats.snapshot(active)


@app.post("/admin/crash")
async def crash() -> dict:
    asyncio.get_running_loop().call_later(0.05, os._exit, 1)
    return {"ok": True}


app.include_router(router)
