from __future__ import annotations

from datetime import UTC, datetime


class FakeRecord(dict):
    pass


class FakePool:
    """Records SQL calls; returns shaped rows for the eventstore INSERT."""

    def __init__(self) -> None:
        self.fetchrow_calls: list[tuple[str, tuple]] = []
        self.fetch_calls: list[tuple[str, tuple]] = []
        self.execute_calls: list[tuple[str, tuple]] = []
        self._seq = 0
        self.fetch_rows: list[FakeRecord] = []
        self.fetchrow_projection: FakeRecord | None = None

    async def fetchrow(self, sql: str, *args):
        self.fetchrow_calls.append((sql, args))
        if sql.strip().startswith("INSERT INTO incident_events"):
            self._seq += 1
            return FakeRecord(
                id=self._seq,
                seq=self._seq,
                event_type=args[1],
                # asyncpg returns jsonb columns as JSON strings — mirror that
                payload=args[2],
                ts=datetime.now(UTC),
            )
        if sql.strip().startswith("SELECT incident_id"):
            return self.fetchrow_projection
        return None

    async def fetch(self, sql: str, *args):
        self.fetch_calls.append((sql, args))
        return list(self.fetch_rows)

    async def execute(self, sql: str, *args):
        self.execute_calls.append((sql, args))
        return "OK"

    def executed_types(self) -> list[str]:
        return [
            args[1] for sql, args in self.fetchrow_calls if "incident_events" in sql
        ]
