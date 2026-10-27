from __future__ import annotations

from typing import Any

import aio_pika
import asyncpg
import httpx
from redis.asyncio import Redis


class AppState:
    def __init__(self) -> None:
        self.pool: asyncpg.Pool | None = None
        self.redis: Redis | None = None
        self.mq_connection: aio_pika.abc.AbstractRobustConnection | None = None
        self.mq_channel: aio_pika.abc.AbstractChannel | None = None
        self.http: httpx.AsyncClient | None = None


state = AppState()


def to_iso(dt: Any) -> str | None:
    if dt is None:
        return None
    return dt.isoformat().replace("+00:00", "Z")
