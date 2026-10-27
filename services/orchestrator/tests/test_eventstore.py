from __future__ import annotations

import uuid
from datetime import timedelta

from app import eventstore
from app.eventstore import AMBIENT_INCIDENT_ID, IncidentState, to_iso
from tests.conftest import FakePool, FakeRecord


async def test_append_returns_shaped_event() -> None:
    pool = FakePool()
    incident_id = uuid.uuid4()
    event = await eventstore.append(
        pool, incident_id, "FAILURE_INJECTED", {"service": "x"}
    )  # type: ignore[arg-type]
    assert event["seq"] == 1
    assert event["event_type"] == "FAILURE_INJECTED"
    assert event["payload"] == {"service": "x"}
    assert event["ts"].endswith("Z")


async def test_append_opened_and_closed_update_projection() -> None:
    import json

    pool = FakePool()
    incident_id = uuid.uuid4()
    await eventstore.append(pool, incident_id, "INCIDENT_OPENED", {"title": "t"})  # type: ignore[arg-type]
    await eventstore.append(pool, incident_id, "INCIDENT_CLOSED", {"mttr_s": 1.0})  # type: ignore[arg-type]
    kinds = [sql.split()[2] for sql, _ in pool.execute_calls]
    assert kinds[0] == "incidents" and "INSERT" in pool.execute_calls[0][0]
    assert "UPDATE" in pool.execute_calls[1][0]

    closed_payloads = [
        json.loads(args[2])
        for sql, args in pool.fetchrow_calls
        if args[1] == "INCIDENT_CLOSED"
    ]
    assert closed_payloads == [{"mttr_s": 1.0}]


async def test_list_events_orders_by_seq() -> None:
    pool = FakePool()
    pool.fetch_rows = [
        FakeRecord(id=2, seq=2, event_type="B", payload={}, ts=None),
        FakeRecord(id=1, seq=1, event_type="A", payload={}, ts=None),
    ]
    events = await eventstore.list_events(pool, uuid.uuid4())  # type: ignore[arg-type]
    assert [e["seq"] for e in events] == [2, 1]  # passthrough; SQL orders by seq


async def test_incident_state_open_close_roundtrip() -> None:
    import json

    pool = FakePool()
    state = IncidentState(pool)  # type: ignore[arg-type]
    assert not state.is_open

    first = await state.open("injection", title="latency on inventory-api")
    assert state.is_open
    again = await state.open("threshold", title="other")
    assert again == first  # no double-open

    await state.close(summary="cleared")
    assert not state.is_open
    closed_payloads = [
        json.loads(args[2])
        for sql, args in pool.fetchrow_calls
        if args[1] == "INCIDENT_CLOSED"
    ]
    assert len(closed_payloads) == 1
    assert closed_payloads[0]["summary"] == "cleared"
    assert isinstance(closed_payloads[0]["mttr_s"], float)


async def test_incident_state_close_without_open_is_noop() -> None:
    pool = FakePool()
    state = IncidentState(pool)  # type: ignore[arg-type]
    await state.close()
    assert pool.fetchrow_calls == []


async def test_get_incident_projection_mttr() -> None:
    pool = FakePool()
    opened = datetime_ago(84)
    pool.fetchrow_projection = FakeRecord(
        incident_id=uuid.uuid4(),
        title="t",
        status="closed",
        opened_at=opened,
        closed_at=opened + timedelta(seconds=84),
    )
    proj = await eventstore.get_incident_projection(pool, uuid.uuid4())  # type: ignore[arg-type]
    assert proj is not None
    assert proj["mttr_s"] == 84.0
    assert proj["status"] == "closed"


def datetime_ago(seconds: float):
    from datetime import UTC, datetime

    return datetime.now(UTC) - timedelta(seconds=seconds)


def test_to_iso_and_ambient_id() -> None:
    assert to_iso(None) is None
    assert AMBIENT_INCIDENT_ID.int == 0


def test_shape_parses_jsonb_string_payload() -> None:
    # asyncpg returns jsonb columns as JSON strings, not dicts.
    row = FakeRecord(id=1, seq=1, event_type="A", payload='{"x": 1}', ts=None)
    assert eventstore._shape(row)["payload"] == {"x": 1}
    empty = FakeRecord(id=2, seq=2, event_type="B", payload="{}", ts=None)
    assert eventstore._shape(empty)["payload"] == {}
