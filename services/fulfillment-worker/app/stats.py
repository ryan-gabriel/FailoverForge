from __future__ import annotations

import time

SERVICE = "fulfillment-worker"
BUCKETS_MS = [0, 5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000]
EXCLUDED_PREFIXES = ("/health", "/stats", "/admin")


class Stats:
    """In-process counters + fixed latency histogram (API_SPEC §4)."""

    def __init__(self) -> None:
        self.started_at = time.perf_counter()
        self.requests_total = 0
        self.requests_error = 0
        self.in_flight = 0
        self.counts = [0] * len(BUCKETS_MS)
        self.count = 0
        self.sum_ms = 0.0
        self.consumed = 0
        self.messages_acked = 0
        self.messages_retried = 0
        self.messages_dead_lettered = 0
        self.paused = False

    def is_excluded(self, path: str) -> bool:
        return any(path.startswith(prefix) for prefix in EXCLUDED_PREFIXES)

    def begin(self) -> None:
        self.in_flight += 1

    def end(self, status_code: int, elapsed_ms: float) -> None:
        self.in_flight = max(0, self.in_flight - 1)
        self.requests_total += 1
        if status_code >= 500:
            self.requests_error += 1
        self.observe(elapsed_ms)

    def observe(self, elapsed_ms: float) -> None:
        self.count += 1
        self.sum_ms += elapsed_ms
        for i, edge in enumerate(BUCKETS_MS):
            if elapsed_ms <= edge:
                self.counts[i] += 1
                return

    def snapshot(self, injections_active: list[str] | None = None) -> dict:
        return {
            "service": SERVICE,
            "uptime_s": round(time.perf_counter() - self.started_at, 1),
            "requests_total": self.requests_total,
            "requests_error": self.requests_error,
            "in_flight": self.in_flight,
            "latency_ms": {
                "buckets": BUCKETS_MS,
                "counts": self.counts,
                "count": self.count,
                "sum_ms": round(self.sum_ms, 1),
            },
            "consumed": self.consumed,
            "messages_acked": self.messages_acked,
            "messages_retried": self.messages_retried,
            "messages_dead_lettered": self.messages_dead_lettered,
            "paused": self.paused,
            "injections_active": injections_active or [],
        }


stats = Stats()
