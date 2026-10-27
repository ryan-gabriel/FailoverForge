from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request

from app import eventstore
from app.eventstore import AMBIENT_INCIDENT_ID
from app.reports import generate_report

router = APIRouter()


def _parse_incident_id(incident_id: str) -> uuid.UUID:
    try:
        incident_uuid = uuid.UUID(incident_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="incident not found") from None
    if incident_uuid == AMBIENT_INCIDENT_ID:
        raise HTTPException(status_code=404, detail="incident not found")
    return incident_uuid


@router.get("/api/incidents")
async def list_incidents(request: Request, limit: int = 50) -> list[dict]:
    return await eventstore.list_incidents(request.app.state.pool, min(limit, 500))


@router.get("/api/incidents/{incident_id}")
async def get_incident(incident_id: str, request: Request) -> dict:
    incident_uuid = _parse_incident_id(incident_id)
    projection = await eventstore.get_incident_projection(
        request.app.state.pool, incident_uuid
    )
    if projection is None:
        raise HTTPException(status_code=404, detail="incident not found")
    events = await eventstore.list_events(request.app.state.pool, incident_uuid)
    return {**projection, "events": events}


@router.get("/api/incidents/{incident_id}/report")
async def get_incident_report(incident_id: str, request: Request) -> dict:
    incident_uuid = _parse_incident_id(incident_id)
    projection = await eventstore.get_incident_projection(
        request.app.state.pool, incident_uuid
    )
    if projection is None:
        raise HTTPException(status_code=404, detail="incident not found")
    events = await eventstore.list_events(request.app.state.pool, incident_uuid)
    return generate_report(str(incident_uuid), events)
