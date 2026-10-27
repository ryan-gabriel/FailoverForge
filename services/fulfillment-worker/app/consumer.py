from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any

import aio_pika
import asyncpg
from aio_pika.abc import AbstractIncomingMessage
from redis.asyncio import Redis

from app import stats
from app.config import settings

log = logging.getLogger("fulfillment-worker")

FULFILLMENT_QUEUE = "fulfillment"
DLQ = "fulfillment.dlq"
PREFETCH = 8
CONNECT_RETRIES = 15
CONNECT_RETRY_DELAY_S = 2.0


class MessageUnparseable(Exception):
    pass


def parse_message(body: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise MessageUnparseable(str(exc)) from exc
    if not isinstance(payload, dict):
        raise MessageUnparseable("payload is not an object")
    try:
        uuid.UUID(str(payload["order_id"]))
        sku = str(payload["sku"])
        qty = int(payload["qty"])
        attempt = int(payload.get("attempt", 0))
    except (KeyError, TypeError, ValueError) as exc:
        raise MessageUnparseable(str(exc)) from exc
    return {
        "order_id": str(payload["order_id"]),
        "sku": sku,
        "qty": qty,
        "attempt": attempt,
    }


def classify_failure(attempt: int) -> str:
    """Spec: retry with attempt+1 while attempt < 2; attempt >= 2 goes to DLQ."""
    return "dlq" if attempt >= 2 else "retry"


class FulfillmentConsumer:
    def __init__(self) -> None:
        self.pool: asyncpg.Pool | None = None
        self.redis: Redis | None = None
        self.connection: aio_pika.abc.AbstractRobustConnection | None = None
        self.channel: aio_pika.abc.AbstractChannel | None = None
        self.queue: aio_pika.abc.AbstractQueue | None = None
        self.consumer_tag: str | None = None
        self.is_consuming = False
        self.running = False

    async def start(self) -> None:
        dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
        self.pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=3)
        self.redis = Redis.from_url(settings.redis_url, decode_responses=True)
        self.connection = await self._connect_with_retry()
        self.channel = await self.connection.channel()
        await self.channel.set_qos(prefetch_count=PREFETCH)
        await self.channel.declare_queue(FULFILLMENT_QUEUE, durable=True)
        await self.channel.declare_queue(DLQ, durable=True)
        self.queue = await self.channel.declare_queue(FULFILLMENT_QUEUE, durable=True)
        self.consumer_tag = await self.queue.consume(self.on_message)
        self.is_consuming = True
        self.running = True
        log.info("consumer started")

    async def _connect_with_retry(
        self, attempts: int = CONNECT_RETRIES, delay_s: float = CONNECT_RETRY_DELAY_S
    ) -> aio_pika.abc.AbstractRobustConnection:
        """Tolerate RabbitMQ still coming up: a restored-service start must not
        crash-loop the worker (the healthcheck pings reachable-before-ready)."""
        last: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                return await aio_pika.connect_robust(settings.rabbitmq_url)
            except Exception as exc:  # noqa: BLE001 — any connect failure is retryable
                last = exc
                log.warning(
                    "amqp connect failed (attempt %d/%d): %s", attempt, attempts, exc
                )
                await asyncio.sleep(delay_s)
        assert last is not None
        raise last

    async def pause(self) -> None:
        """Stop consuming (messages stay in the queue → depth climbs honestly)."""
        if (
            self.queue is not None
            and self.consumer_tag is not None
            and self.is_consuming
        ):
            await self.queue.cancel(self.consumer_tag)
            self.is_consuming = False
            log.info("consumer paused")

    async def resume(self) -> None:
        if self.queue is not None and not self.is_consuming:
            self.consumer_tag = await self.queue.consume(self.on_message)
            self.is_consuming = True
            log.info("consumer resumed")

    async def stop(self) -> None:
        self.running = False
        if self.connection is not None:
            await self.connection.close()
        if self.redis is not None:
            await self.redis.aclose()
        if self.pool is not None:
            await self.pool.close()
        log.info("consumer stopped")

    async def on_message(self, message: AbstractIncomingMessage) -> None:
        stats.stats.begin()
        start = time.perf_counter()
        status = 200
        try:
            try:
                payload = parse_message(message.body)
            except MessageUnparseable:
                await self._dead_letter(message.body, "unparseable")
                await message.ack()
                status = 500
                return

            try:
                await self._fulfill(payload["order_id"])
            except Exception:
                log.exception("fulfillment failed order_id=%s", payload["order_id"])
                status = 500
                if classify_failure(payload["attempt"]) == "dlq":
                    await self._dead_letter(message.body, "max_attempts")
                    await message.ack()
                else:
                    await self._republish(payload["attempt"] + 1, payload)
                    stats.stats.messages_retried += 1
                    await message.ack()
                return

            stats.stats.messages_acked += 1
            stats.stats.consumed += 1
            await message.ack()
        except Exception:
            status = 500
            raise
        finally:
            stats.stats.end(status, (time.perf_counter() - start) * 1000)

    async def _fulfill(self, order_id: str) -> None:
        await self.pool.execute(
            "UPDATE orders SET status = 'fulfilled', fulfilled_at = now() WHERE order_id = $1",
            uuid.UUID(order_id),
        )
        # write-invalidation (API_SPEC §5): status reads must not serve stale pending
        await self.redis.delete(f"cache:order:{order_id}")

    async def _republish(self, next_attempt: int, payload: dict[str, Any]) -> None:
        body = json.dumps(
            {
                "order_id": payload["order_id"],
                "sku": payload["sku"],
                "qty": payload["qty"],
                "attempt": next_attempt,
            }
        ).encode()
        out = aio_pika.Message(
            body=body,
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            content_type="application/json",
        )
        await self.channel.default_exchange.publish(out, routing_key=FULFILLMENT_QUEUE)

    async def _dead_letter(self, body: bytes, reason: str) -> None:
        out = aio_pika.Message(
            body=body,
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            headers={"x-reason": reason, "x-original-queue": FULFILLMENT_QUEUE},
        )
        await self.channel.default_exchange.publish(out, routing_key=DLQ)
        stats.stats.messages_dead_lettered += 1
