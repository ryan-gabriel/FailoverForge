from __future__ import annotations

import json

from redis.asyncio import Redis

CACHE_TTL_S = 60


def order_cache_key(order_id: str) -> str:
    return f"cache:order:{order_id}"


async def cache_get(client: Redis, order_id: str) -> dict | None:
    raw = await client.get(order_cache_key(order_id))
    if raw is None:
        return None
    return json.loads(raw)


async def cache_put(client: Redis, order_id: str, snapshot: dict) -> None:
    await client.set(order_cache_key(order_id), json.dumps(snapshot), ex=CACHE_TTL_S)
