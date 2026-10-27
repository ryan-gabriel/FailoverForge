from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.auth import chaos_guard
from app.injector import InjectionRejected

router = APIRouter()


class InjectionRequest(BaseModel):
    service: str
    failure_type: str
    intensity: int | float = 0
    ttl_s: int | None = None


@router.post("/api/injections", status_code=201, dependencies=[Depends(chaos_guard)])
async def create_injection(body: InjectionRequest, request: Request) -> dict:
    try:
        injection = await request.app.state.injector.inject(
            service=body.service,
            failure_type=body.failure_type,
            intensity=body.intensity,
            ttl_s=body.ttl_s,
        )
    except InjectionRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return injection.model_dump()


@router.delete("/api/injections/{injection_id}", dependencies=[Depends(chaos_guard)])
async def clear_injection(injection_id: str, request: Request) -> dict:
    try:
        injection = await request.app.state.injector.clear(injection_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="injection not found") from None
    return injection.model_dump()


@router.delete("/api/injections", dependencies=[Depends(chaos_guard)])
async def clear_all_injections(request: Request) -> dict:
    cleared = await request.app.state.injector.clear_all()
    return {"cleared": cleared}


@router.get("/api/injections")
async def list_injections(request: Request) -> list[dict]:
    return [i.model_dump() for i in request.app.state.injector.active.values()]
