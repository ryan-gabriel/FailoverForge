from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.auth import chaos_guard
from app.loadgen import PROFILES, LoadAlreadyRunning

router = APIRouter()


class LoadRequest(BaseModel):
    profile: str = "mixed"
    rps: int = Field(ge=1, le=1000)
    duration_s: int = Field(ge=1, le=3600)


@router.post("/api/load", status_code=202, dependencies=[Depends(chaos_guard)])
async def start_load(body: LoadRequest, request: Request) -> dict:
    if body.profile not in PROFILES:
        raise HTTPException(status_code=422, detail=f"unknown profile: {body.profile}")
    try:
        status = await request.app.state.loadgen.start(
            profile=body.profile, rps=body.rps, duration_s=body.duration_s
        )
    except LoadAlreadyRunning:
        raise HTTPException(status_code=409, detail="load already running") from None
    return {"load_id": status["load_id"], "status": status["status"]}


@router.post("/api/load/stop", dependencies=[Depends(chaos_guard)])
async def stop_load(request: Request) -> dict:
    status = await request.app.state.loadgen.stop()
    return {"status": status["status"]}


@router.get("/api/load")
async def load_status(request: Request) -> dict:
    return dict(request.app.state.loadgen.status)
