from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

import asyncpg

log = logging.getLogger("orchestrator.eventstore")

# Ambient stream for control-plane events that happen outside any incident
# (e.g. LOAD_STARTED with no open incident). Never listed as an incident.
AMBIENT_INCIDENT_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")

# Broadcast hook (WS hub), registered once at startup from main.py. Fired
# after every successful append; failures are logged, never raised — the
# event store stays the source of truth regardless of subscribers.
_listener: Callable[[dict], Awaitable[None]] | None = None


def set_listener(listener: Callable[[dict], Awaitable[None]] | None) -> None:
    global _listener
    _listener = listener


def to_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.isoformat().replace("+00:00", "Z")


def _shape(row: Any) -> dict:
    payload = row["payload"]
    if isinstance(payload, str):  # asyncpg returns jsonb as str by default
        payload = json.loads(payload)
    return {
        "id": row["id"],
        "seq": row["seq"],
        "event_type": row["event_type"],
        "ts": to_iso(row["ts"]),
        "payload": dict(payload) if payload else {},
    }


async def append(
    pool: asyncpg.Pool,
    incident_id: uuid.UUID,
    event_type: str,
    payload: dict,
) -> dict:
    """Append-only write to incident_events (the ONLY accessor of that table).

    The MAX(seq)+1 is computed inside the INSERT statement so concurrent
    appends serialize on the per-incident unique constraint.
    Projection rows in `incidents` are maintained here too — projections are
    never source of truth (ARCHITECTURE §5.1).
    """
    row = await pool.fetchrow(
        """
        INSERT INTO incident_events (incident_id, seq, event_type, payload)
        SELECT $1,
               COALESCE((SELECT MAX(seq) FROM incident_events WHERE incident_id = $1), 0) + 1,
               $2,
               $3::jsonb
        RETURNING id, seq, event_type, payload, ts
        """,
        incident_id,
        event_type,
        json.dumps(payload),
    )
    if event_type == "INCIDENT_OPENED":
        await pool.execute(
            """
            INSERT INTO incidents (incident_id, title, status, opened_at)
            VALUES ($1, $2, 'open', now())
            ON CONFLICT (incident_id) DO NOTHING
            """,
            incident_id,
            payload.get("title"),
        )
    elif event_type == "INCIDENT_CLOSED":
        await pool.execute(
            "UPDATE incidents SET status = 'closed', closed_at = now()"
            " WHERE incident_id = $1",
            incident_id,
        )
    shaped = _shape(row)
    if _listener is not None:
        try:
            await _listener({**shaped, "incident_id": str(incident_id)})
        except Exception:
            log.exception("event listener failed for %s", event_type)
    return shaped


async def list_events(pool: asyncpg.Pool, incident_id: uuid.UUID) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT id, seq, event_type, ts, payload
        FROM incident_events
        WHERE incident_id = $1
        ORDER BY seq ASC
        """,
        incident_id,
    )
    return [_shape(r) for r in rows]


async def list_incidents(pool: asyncpg.Pool, limit: int) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT i.incident_id, i.title, i.status, i.opened_at, i.closed_at,
               (SELECT COUNT(*) FROM incident_events e
                 WHERE e.incident_id = i.incident_id) AS event_count
        FROM incidents i
        WHERE i.incident_id <> $1
        ORDER BY i.opened_at DESC
        LIMIT $2
        """,
        AMBIENT_INCIDENT_ID,
        limit,
    )
    return [
        {
            "incident_id": str(r["incident_id"]),
            "status": r["status"],
            "opened_at": to_iso(r["opened_at"]),
            "closed_at": to_iso(r["closed_at"]),
            "title": r["title"],
            "event_count": r["event_count"],
        }
        for r in rows
    ]


async def get_incident_projection(
    pool: asyncpg.Pool, incident_id: uuid.UUID
) -> dict | None:
    row = await pool.fetchrow(
        """
        SELECT incident_id, title, status, opened_at, closed_at
        FROM incidents WHERE incident_id = $1
        """,
        incident_id,
    )
    if row is None:
        return None
    opened, closed = row["opened_at"], row["closed_at"]
    mttr_s = (closed - opened).total_seconds() if closed else None
    return {
        "incident_id": str(row["incident_id"]),
        "title": row["title"],
        "status": row["status"],
        "opened_at": to_iso(opened),
        "closed_at": to_iso(closed),
        "mttr_s": round(mttr_s, 1) if mttr_s is not None else None,
    }


class IncidentState:
    """In-memory handle on the currently-open incident (single orchestrator process).

    Phase 1: incidents open on first injection or first threshold breach and
    close when all injections are cleared. Full lifecycle polish is Phase 3.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        self.current_id: uuid.UUID | None = None
        self.trigger: str | None = None  # "injection" | "threshold" of open incident
        self._opened_monotonic: float | None = None

    @property
    def is_open(self) -> bool:
        return self.current_id is not None

    async def open(self, trigger: str, title: str | None = None) -> uuid.UUID:
        if self.current_id is not None:
            return self.current_id
        incident_id = uuid.uuid4()
        await append(
            self._pool,
            incident_id,
            "INCIDENT_OPENED",
            {"trigger": trigger, "title": title},
        )
        self.current_id = incident_id
        self.trigger = trigger
        self._opened_monotonic = time.monotonic()
        return incident_id

    async def close(self, summary: str = "") -> None:
        if self.current_id is None:
            return
        mttr_s = (
            time.monotonic() - self._opened_monotonic
            if self._opened_monotonic is not None
            else 0.0
        )
        await append(
            self._pool,
            self.current_id,
            "INCIDENT_CLOSED",
            {"mttr_s": round(mttr_s, 1), "summary": summary},
        )
        self.current_id = None
        self.trigger = None
        self._opened_monotonic = None
