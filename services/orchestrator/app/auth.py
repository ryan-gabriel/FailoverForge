from __future__ import annotations

from fastapi import HTTPException, Request


def chaos_guard(request: Request) -> None:
    """Shared-secret gate for chaos endpoints (API_SPEC §0)."""
    settings = request.app.state.settings
    if not settings.require_chaos_token:
        return
    if request.headers.get("X-Chaos-Token") != settings.chaos_token:
        raise HTTPException(status_code=403, detail="invalid chaos token")
