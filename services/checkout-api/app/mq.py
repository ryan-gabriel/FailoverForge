from __future__ import annotations

import json
import uuid

import aio_pika

FULFILLMENT_QUEUE = "fulfillment"
DLQ = "fulfillment.dlq"


async def declare_queues(channel: aio_pika.abc.AbstractChannel) -> None:
    await channel.declare_queue(FULFILLMENT_QUEUE, durable=True)
    await channel.declare_queue(DLQ, durable=True)


async def publish_fulfillment(
    channel: aio_pika.abc.AbstractChannel,
    order_id: uuid.UUID,
    sku: str,
    qty: int,
) -> None:
    body = json.dumps(
        {"order_id": str(order_id), "sku": sku, "qty": qty, "attempt": 0}
    ).encode()
    message = aio_pika.Message(
        body=body,
        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        content_type="application/json",
    )
    await channel.default_exchange.publish(message, routing_key=FULFILLMENT_QUEUE)
