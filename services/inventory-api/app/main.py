from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from redis.asyncio import Redis

from app import injection, stats
from app.stock import InsufficientStock, Stock, UnknownSku

STOCK = Stock()


@asynccontextmanager
async def lifespan(app: FastAPI):
    client = Redis.from_url(
        os.environ.get("REDIS_URL", "redis://redis:6379/0"), decode_responses=True
    )
    injection.flags = injection.FlagStore(client, "inventory-api")
    yield
    try:
        await client.aclose()
    except Exception:  # shutdown continues even if Redis already dropped
        logging.getLogger("inventory-api").exception("shutdown step failed")


app = FastAPI(title="inventory-api", lifespan=lifespan)


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


class ReserveRequest(BaseModel):
    qty: int = Field(ge=1)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/stats")
async def get_stats() -> dict:
    active = await injection.flags.active_injections() if injection.flags else []
    return stats.stats.snapshot(active)


@app.post("/admin/seed")
async def reseed() -> dict:
    stock = await STOCK.reset()
    return {"seeded": True, "stock": stock}


@app.post("/admin/crash")
async def crash() -> dict:
    asyncio.get_running_loop().call_later(0.05, os._exit, 1)
    return {"ok": True}


@app.get("/stock/{sku}")
async def get_stock(sku: str) -> dict:
    available = STOCK.get(sku)
    if available is None:
        raise HTTPException(status_code=404, detail="unknown SKU")
    return {"sku": sku, "available": available}


@app.post("/stock/{sku}/reserve")
async def reserve_stock(sku: str, body: ReserveRequest) -> dict:
    try:
        available = await STOCK.reserve(sku, body.qty)
    except UnknownSku:
        raise HTTPException(status_code=404, detail="unknown SKU") from None
    except InsufficientStock:
        raise HTTPException(status_code=409, detail="insufficient stock") from None
    return {"sku": sku, "reserved": body.qty, "available": available}
