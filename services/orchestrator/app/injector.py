from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import aio_pika
import asyncpg
import httpx
import redis
from pydantic import BaseModel

from app import eventstore
from app.config import Settings
from app.eventstore import IncidentState

SERVICES = ("checkout-api", "inventory-api", "fulfillment-worker")
FAILURE_TYPES = ("latency", "error", "crash", "queue_saturation", "cache_outage")

SERVICE_URLS = {
    "checkout-api": "checkout_api_url",
    "inventory-api": "inventory_api_url",
    "fulfillment-worker": "worker_api_url",
}


class InjectionRejected(Exception):
    """Validation failure for an injection request (mapped to HTTP 422)."""


class Injection(BaseModel):
    injection_id: str
    service: str
    failure_type: str
    intensity: int | float
    ttl_s: int
    created_at: str
    active: bool = True


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class Injector:
    """Writes Redis flags with TTL, drives crash/flood actions, appends events.

    Flags are TTL-bounded (ARCHITECTURE §7) so a dead orchestrator never
    leaves the SUT permanently broken.
    """

    def __init__(
        self,
        redis_client: redis.asyncio.Redis,
        pool: asyncpg.Pool,
        incidents: IncidentState,
        http: httpx.AsyncClient,
        mq_channel: aio_pika.abc.AbstractChannel | None,
        settings: Settings,
        on_change: Callable[[list[dict]], Awaitable[None]] | None = None,
    ) -> None:
        self._redis = redis_client
        self._pool = pool
        self._incidents = incidents
        self._http = http
        self._mq_channel = mq_channel
        self._settings = settings
        self._on_change = on_change
        self.active: dict[str, Injection] = {}

    async def _notify_change(self) -> None:
        if self._on_change is not None:
            await self._on_change(
                [injection.model_dump() for injection in self.active.values()]
            )

    def _validate(
        self,
        service: str,
        failure_type: str,
        intensity: float,
        ttl_s: int,
    ) -> None:
        if service not in SERVICES:
            raise InjectionRejected(f"unknown service: {service}")
        if failure_type not in FAILURE_TYPES:
            raise InjectionRejected(f"unknown failure_type: {failure_type}")
        if failure_type == "cache_outage" and service != "checkout-api":
            raise InjectionRejected("cache_outage is only valid on checkout-api")
        if failure_type == "queue_saturation" and service != "fulfillment-worker":
            raise InjectionRejected(
                "queue_saturation is only valid on fulfillment-worker"
            )
        if not 1 <= ttl_s <= self._settings.injection_ttl_max_s:
            raise InjectionRejected(
                f"ttl_s must be between 1 and {self._settings.injection_ttl_max_s}"
            )
        if failure_type == "latency" and not 1 <= int(intensity) <= 10000:
            raise InjectionRejected("latency intensity must be 1-10000 ms")
        if failure_type == "error" and not 0.0 <= float(intensity) <= 1.0:
            raise InjectionRejected("error intensity must be 0.0-1.0")
        if failure_type == "queue_saturation" and not 1 <= int(intensity) <= 5000:
            raise InjectionRejected(
                "queue_saturation intensity must be 1-5000 messages"
            )

    async def inject(
        self,
        service: str,
        failure_type: str,
        intensity: float,
        ttl_s: int | None = None,
    ) -> Injection:
        ttl = ttl_s if ttl_s is not None else self._settings.injection_ttl_default_s
        if failure_type in ("crash", "cache_outage"):
            intensity = int(intensity or 0)
        self._validate(service, failure_type, intensity, ttl)

        title = f"{failure_type} on {service}"
        if not self._incidents.is_open:
            await self._incidents.open("injection", title=title)
        incident_id = self._incidents.current_id
        if incident_id is None:  # open() above always assigns
            raise InjectionRejected("incident state unavailable")

        injection_id = f"inj_{uuid.uuid4().hex[:12]}"
        if failure_type == "crash":
            await self._crash(service)
        elif failure_type == "queue_saturation":
            await self._flood(int(intensity))
            await self._redis.set("inj:pause_worker", "1", ex=ttl)
        elif failure_type == "cache_outage":
            await self._redis.set("inj:cache_outage", "1", ex=ttl)
        else:  # latency | error
            value = (
                str(int(intensity))
                if failure_type == "latency"
                else str(float(intensity))
            )
            await self._redis.set(f"inj:{failure_type}:{service}", value, ex=ttl)

        injection = Injection(
            injection_id=injection_id,
            service=service,
            failure_type=failure_type,
            intensity=intensity,
            ttl_s=ttl,
            created_at=_now_iso(),
        )
        self.active[injection_id] = injection
        await eventstore.append(
            self._pool,
            incident_id,
            "FAILURE_INJECTED",
            {
                "service": service,
                "failure_type": failure_type,
                "intensity": intensity,
                "ttl_s": ttl,
            },
        )
        await self._notify_change()
        return injection

    async def clear(self, injection_id: str) -> Injection:
        injection = self.active.pop(injection_id, None)
        if injection is None:
            raise KeyError(injection_id)
        await self._clear_flags(injection)
        incident_id = self._incidents.current_id
        if incident_id is not None:  # invariant: an active injection implies open
            await eventstore.append(
                self._pool,
                incident_id,
                "MITIGATION_APPLIED",
                {"service": injection.service, "failure_type": injection.failure_type},
            )
        if not self.active and self._incidents.is_open:
            await self._incidents.close(summary="all injections cleared")
        await self._notify_change()
        return injection

    async def clear_all(self) -> int:
        ids = list(self.active)
        for injection_id in ids:
            await self.clear(injection_id)
        return len(ids)

    async def _clear_flags(self, injection: Injection) -> None:
        if injection.failure_type == "crash":
            return  # the process already died; nothing to clear in Redis
        if injection.failure_type == "queue_saturation":
            still_saturating = any(
                i.failure_type == "queue_saturation" for i in self.active.values()
            )
            if not still_saturating:
                await self._redis.delete("inj:pause_worker")
            return
        if injection.failure_type == "cache_outage":
            await self._redis.delete("inj:cache_outage")
            return
        await self._redis.delete(f"inj:{injection.failure_type}:{injection.service}")

    async def _crash(self, service: str) -> None:
        url = getattr(self._settings, SERVICE_URLS[service])
        await self._http.post(f"{url}/admin/crash", timeout=2.0)

    async def _flood(self, count: int) -> None:
        channel = self._mq_channel
        if channel is None:
            raise InjectionRejected("rabbitmq channel unavailable")

        async def publish_one() -> None:
            body = json.dumps(
                {
                    "order_id": str(uuid.uuid4()),
                    "sku": "SKU-FLOOD",
                    "qty": 1,
                    "attempt": 0,
                }
            ).encode()
            message = aio_pika.Message(
                body=body,
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                content_type="application/json",
            )
            await channel.default_exchange.publish(message, routing_key="fulfillment")

        await asyncio.gather(*(publish_one() for _ in range(count)))
