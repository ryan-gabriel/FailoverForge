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

    async def latency_ms(self) -> int:
        raw = await self._read(f"inj:latency:{self._service}")
        try:
            return max(0, int(raw)) if raw else 0
        except ValueError:
            return 0

    async def error_ratio(self) -> float:
        raw = await self._read(f"inj:error:{self._service}")
        try:
            ratio = float(raw) if raw else 0.0
        except ValueError:
            return 0.0
        return min(1.0, max(0.0, ratio))

    async def cache_outage(self) -> bool:
        return await self._read("inj:cache_outage") == "1"

    async def active_injections(self) -> list[str]:
        active: list[str] = []
        if await self.latency_ms() > 0:
            active.append("latency")
        if await self.error_ratio() > 0:
            active.append("error")
        if await self.cache_outage():
            active.append("cache_outage")
        return active


flags: FlagStore | None = None
