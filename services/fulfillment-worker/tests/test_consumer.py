from __future__ import annotations

import json

import pytest

from app import stats
from app.consumer import (
    FulfillmentConsumer,
    MessageUnparseable,
    classify_failure,
    parse_message,
)


class FakeMessage:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.acked = False
        self.rejected = False

    async def ack(self) -> None:
        self.acked = True

    def reject(self, requeue: bool = False) -> None:
        self.rejected = True


class FakeChannel:
    def __init__(self) -> None:
        self.published: list[dict] = []

    class _Exchange:
        def __init__(self, channel: FakeChannel) -> None:
            self._channel = channel

        async def publish(self, message, routing_key: str) -> None:
            self._channel.published.append(
                {
                    "routing_key": routing_key,
                    "body": message.body,
                    "headers": message.headers,
                }
            )

    @property
    def default_exchange(self):
        return FakeChannel._Exchange(self)


class FakePool:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.executed: list[tuple] = []

    async def execute(self, query: str, *args) -> None:
        if self.fail:
            raise RuntimeError("db down")
        self.executed.append((query, args))


class FakeRedis:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    async def delete(self, key: str) -> None:
        self.deleted.append(key)


def make_consumer(
    fail_db: bool = False,
) -> tuple[FulfillmentConsumer, FakeChannel, FakePool, FakeRedis]:
    consumer = FulfillmentConsumer()
    channel = FakeChannel()
    pool = FakePool(fail=fail_db)
    redis_client = FakeRedis()
    consumer.channel = channel
    consumer.pool = pool
    consumer.redis = redis_client
    return consumer, channel, pool, redis_client


def valid_body(
    order_id: str = "550e8400-e29b-41d4-a716-446655440000", attempt: int = 0
) -> bytes:
    return json.dumps(
        {"order_id": order_id, "sku": "SKU-1001", "qty": 1, "attempt": attempt}
    ).encode()


def test_parse_valid() -> None:
    payload = parse_message(valid_body())
    assert payload == {
        "order_id": "550e8400-e29b-41d4-a716-446655440000",
        "sku": "SKU-1001",
        "qty": 1,
        "attempt": 0,
    }


def test_parse_attempt_defaults_to_zero() -> None:
    body = json.dumps(
        {"order_id": "550e8400-e29b-41d4-a716-446655440000", "sku": "S", "qty": 1}
    ).encode()
    assert parse_message(body)["attempt"] == 0


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"[]",
        json.dumps({"sku": "S", "qty": 1}).encode(),
        json.dumps({"order_id": "nope", "sku": "S", "qty": 1}).encode(),
        json.dumps(
            {"order_id": "550e8400-e29b-41d4-a716-446655440000", "sku": "S", "qty": "x"}
        ).encode(),
    ],
)
def test_parse_unparseable(body: bytes) -> None:
    with pytest.raises(MessageUnparseable):
        parse_message(body)


def test_classify_failure() -> None:
    assert classify_failure(0) == "retry"
    assert classify_failure(1) == "retry"
    assert classify_failure(2) == "dlq"


async def test_on_message_success() -> None:
    consumer, channel, pool, redis_client = make_consumer()
    msg = FakeMessage(valid_body())
    await consumer.on_message(msg)  # type: ignore[arg-type]
    assert msg.acked
    assert (
        pool.executed
        and pool.executed[0][1][0].hex == "550e8400e29b41d4a716446655440000"
    )
    assert redis_client.deleted == ["cache:order:550e8400-e29b-41d4-a716-446655440000"]
    assert stats.stats.messages_acked >= 1
    assert channel.published == []


async def test_on_message_poison_goes_to_dlq() -> None:
    consumer, channel, pool, _ = make_consumer()
    msg = FakeMessage(b"{broken")
    await consumer.on_message(msg)  # type: ignore[arg-type]
    assert msg.acked
    assert channel.published[0]["routing_key"] == "fulfillment.dlq"
    assert channel.published[0]["headers"]["x-reason"] == "unparseable"
    assert channel.published[0]["headers"]["x-original-queue"] == "fulfillment"
    assert stats.stats.messages_dead_lettered >= 1
    assert pool.executed == []


async def test_on_message_retry_republish() -> None:
    consumer, channel, _pool, _ = make_consumer(fail_db=True)
    msg = FakeMessage(valid_body(attempt=0))
    await consumer.on_message(msg)  # type: ignore[arg-type]
    assert msg.acked
    republished = json.loads(channel.published[0]["body"])
    assert republished["attempt"] == 1
    assert channel.published[0]["routing_key"] == "fulfillment"
    assert stats.stats.messages_retried >= 1


async def test_on_message_max_attempts_dead_letter() -> None:
    consumer, channel, _pool, _ = make_consumer(fail_db=True)
    msg = FakeMessage(valid_body(attempt=2))
    await consumer.on_message(msg)  # type: ignore[arg-type]
    assert msg.acked
    assert channel.published[0]["routing_key"] == "fulfillment.dlq"
    assert channel.published[0]["headers"]["x-reason"] == "max_attempts"


def test_worker_stats_shape() -> None:
    s = stats.Stats()
    snap = s.snapshot()
    assert snap["service"] == "fulfillment-worker"
    assert snap["paused"] is False
    assert snap["consumed"] == 0
    assert "cache" not in snap
    assert "queue_published" not in snap
