from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app import injection, stats
from app.consumer import FulfillmentConsumer

log = logging.getLogger("fulfillment-worker")
PAUSE_POLL_S = 0.5


async def pause_watcher(consumer: FulfillmentConsumer) -> None:
    """Applies the inj:pause_worker flag by cancelling/resuming the queue consumer."""
    assert injection.flags is not None
    while True:
        try:
            paused = await injection.flags.pause_worker()
            if paused and consumer.is_consuming:
                await consumer.pause()
            elif not paused and not consumer.is_consuming:
                await consumer.resume()
            stats.stats.paused = paused
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("pause watcher iteration failed")
        await asyncio.sleep(PAUSE_POLL_S)


@asynccontextmanager
async def lifespan(app: FastAPI):
    consumer = FulfillmentConsumer()
    app.state.consumer = consumer
    await consumer.start()
    injection.flags = injection.FlagStore(consumer.redis, "fulfillment-worker")  # type: ignore[arg-type]
    watcher = asyncio.create_task(pause_watcher(consumer))
    yield

    # Graceful shutdown (PRD Phase 4): stop the pause watcher, then let the
    # consumer drain (aio-pika closes the connection, which finishes in-flight
    # messages). Best-effort so a failed step never blocks the rest.
    watcher.cancel()
    try:
        await watcher
    except asyncio.CancelledError:
        pass
    try:
        await consumer.stop()
    except Exception:  # shutdown continues even if the broker is gone
        log.exception("consumer stop failed")


app = FastAPI(title="fulfillment-worker", lifespan=lifespan)


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
async def health(request: Request) -> dict:
    consumer: FulfillmentConsumer = request.app.state.consumer
    if consumer.running:
        return {"status": "ok", "consumers": 1, "paused": stats.stats.paused}
    return JSONResponse(
        status_code=503,
        content={"status": "degraded", "consumers": 0, "paused": stats.stats.paused},
    )


@app.get("/stats")
async def get_stats() -> dict:
    active = await injection.flags.active_injections() if injection.flags else []
    return stats.stats.snapshot(active)


@app.post("/admin/crash")
async def crash() -> dict:
    asyncio.get_running_loop().call_later(0.05, os._exit, 1)
    return {"ok": True}
