from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.queue import fetch_queue_snapshot

router = APIRouter()


@router.get("/api/metrics/latest")
async def metrics_latest(request: Request) -> dict:
    latest = request.app.state.aggregator.latest
    if latest is None:
        raise HTTPException(status_code=503, detail="metrics not ready yet")
    return latest


@router.get("/api/queue")
async def queue_status(request: Request) -> dict:
    state = request.app.state
    try:
        return await fetch_queue_snapshot(
            state.http, state.settings.rabbitmq_mgmt_url, samples=True
        )
    except Exception as exc:  # noqa: BLE001 — mgmt API network/shape errors map to 503
        raise HTTPException(
            status_code=503, detail=f"rabbitmq management API unavailable: {exc}"
        ) from None
