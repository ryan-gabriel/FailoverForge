from __future__ import annotations

import logging
import uuid

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app import cache, db, injection, mq, stats
from app.config import settings
from app.state import state

log = logging.getLogger("checkout-api")
router = APIRouter()


class OrderCreateRequest(BaseModel):
    sku: str
    qty: int = Field(ge=1)
    customer_ref: str | None = None


@router.post("/orders", status_code=201)
async def create_order(body: OrderCreateRequest) -> dict:
    try:
        resp = await state.http.post(
            f"{settings.inventory_api_url}/stock/{body.sku}/reserve",
            json={"qty": body.qty},
        )
    except httpx.HTTPError:
        raise HTTPException(status_code=503, detail="inventory unavailable") from None

    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail="unknown SKU")
    if resp.status_code == 409:
        raise HTTPException(
            status_code=409, detail=f"insufficient stock for {body.sku}"
        )
    if resp.status_code != 200:
        raise HTTPException(status_code=503, detail="inventory unavailable")

    order_id = uuid.uuid4()
    created_at = await db.insert_order(
        state.pool, order_id, body.sku, body.qty, body.customer_ref
    )

    try:
        await mq.publish_fulfillment(state.mq_channel, order_id, body.sku, body.qty)
        stats.stats.queue_published += 1
    except Exception:
        log.exception("failed to publish fulfillment message order_id=%s", order_id)

    return {
        "order_id": str(order_id),
        "status": "pending",
        "sku": body.sku,
        "qty": body.qty,
        "created_at": created_at,
    }


@router.get("/orders/{order_id}")
async def get_order(order_id: str) -> JSONResponse:
    try:
        uuid.UUID(order_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="order not found") from None

    outage = injection.flags is not None and await injection.flags.cache_outage()

    cached = None if outage else await cache.cache_get(state.redis, order_id)
    if cached is not None:
        stats.stats.cache_hits += 1
        return JSONResponse(content=cached, headers={"X-Cache": "hit"})

    stats.stats.cache_misses += 1
    snapshot = await db.get_order(state.pool, uuid.UUID(order_id))
    if snapshot is None:
        raise HTTPException(status_code=404, detail="order not found")

    if not outage:
        await cache.cache_put(state.redis, order_id, snapshot)
    return JSONResponse(content=snapshot, headers={"X-Cache": "miss"})
