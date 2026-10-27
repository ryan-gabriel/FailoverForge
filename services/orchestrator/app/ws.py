from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

log = logging.getLogger("orchestrator.ws")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class Hub:
    """Fan-out hub for GET /ws (API_SPEC §3).

    Broadcasts are best-effort: a send that fails drops that client and never
    blocks the orchestrator core (aggregator tick, injector, event store).
    Client→server messages are not used in MVP; the endpoint just drains.
    """

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.add(ws)
        log.info("ws client connected (%d total)", len(self._clients))

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)
        log.info("ws client disconnected (%d total)", len(self._clients))

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def close_all(self) -> None:
        """Graceful shutdown (PRD Phase 4): close every live socket so clients
        reconnect elsewhere / browsers drop the connection cleanly rather than
        hanging on a half-dead orchestrator."""
        async with self._lock:
            clients = list(self._clients)
            self._clients.clear()
        for ws in clients:
            try:
                await ws.close(code=1001)  # 1001 = going away
            except Exception:  # shutdown must never block on a dead socket
                log.debug("ws close failed during shutdown", exc_info=True)
        if clients:
            log.info("ws hub closed %d client(s) on shutdown", len(clients))

    async def broadcast(self, type: str, data: object) -> None:
        if not self._clients:
            return
        envelope = json.dumps({"type": type, "ts": _now_iso(), "data": data})
        async with self._lock:
            clients = list(self._clients)
        dead: list[WebSocket] = []
        for ws in clients:
            try:
                await ws.send_text(envelope)
            except Exception:  # noqa: BLE001 — slow/broken client must not block fan-out
                dead.append(ws)
        for ws in dead:
            await self.disconnect(ws)


router = APIRouter()


@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    hub: Hub = ws.app.state.ws_hub
    await ws.accept()
    await hub.connect(ws)
    try:
        while True:
            await ws.receive_text()  # MVP: no client→server protocol; drain pings
    except WebSocketDisconnect:
        pass
    finally:
        await hub.disconnect(ws)
