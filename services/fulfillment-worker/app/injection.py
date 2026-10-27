from __future__ import annotations

import time

from redis.asyncio import Redis

LOCAL_TTL_S = 0.25  # bounded staleness for flag reads (ARCHITECTURE §7)


class FlagStore:
    """Reads inj:* Redis flags with a small local cache. Fail-open (API_SPEC §5)."""

    def __init__(self, client: Redis, service: str) -> None:
        self._client = client
        self._service = service
        self._cache: dict[str, tuple[float, str | None]] = {}

    async def _read(self, key: str) -> str | None:
        now = time.monotonic()
        hit = self._cache.get(key)
        if hit is not None and now - hit[0] < LOCAL_TTL_S:
            return hit[1]
        try:
            raw: str | bytes | None = await self._client.get(key)
        except Exception:  # noqa: BLE001 — fail-open: flag read errors mean no injection
            raw = None
        value = raw.decode() if isinstance(raw, bytes) else raw
        self._cache[key] = (now, value)
        return value

    async def pause_worker(self) -> bool:
        return await self._read("inj:pause_worker") == "1"

    async def active_injections(self) -> list[str]:
        if await self.pause_worker():
            return ["pause_worker"]
        return []


flags: FlagStore | None = None
