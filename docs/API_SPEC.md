# API Specification: Failover Forge

> The contract between all services and the frontend. **Code must match this file**; if you need to change a contract, update this file first (then code, then note the change in the PR).
>
> Base URLs (docker compose network, defaults; override via env — see §0):
>
> | Component | Internal | Host-mapped (dev) |
> |---|---|---|
> | checkout-api | `http://checkout-api:8000` | `http://localhost:8001` |
> | inventory-api | `http://inventory-api:8000` | `http://localhost:8002` |
> | fulfillment-worker (health) | `http://fulfillment-worker:8000` | `http://localhost:8003` |
> | orchestrator | `http://orchestrator:8000` | `http://localhost:8000` |
> | frontend | — | `http://localhost:3000` |

## 0. Shared conventions

- JSON everywhere; `Content-Type: application/json`.
- Errors: `{"detail": string}` with proper HTTP status (FastAPI default).
- IDs: UUIDv4 strings.
- Timestamps: ISO-8601 UTC with `Z` (e.g. `2026-10-20T12:00:00.123Z`).
- Injection header for chaos-gated endpoints: `X-Chaos-Token: <CHAOS_TOKEN>` (403 on mismatch when `REQUIRE_CHAOS_TOKEN=true`).
- Every SUT service exposes `GET /health` and `GET /stats` (§4) — no auth.

## 1. System Under Test (SUT) endpoints

### 1.1 checkout-api

#### `POST /orders`

Create an order (reserves stock → persists → enqueues fulfillment).

Request:

```json
{
  "sku": "SKU-1001",
  "qty": 1,
  "customer_ref": "loadgen"          // opaque string, free-form
}
```

Responses:
- `201`:
  ```json
  {
    "order_id": "550e8400-e29b-41d4-a716-446655440000",
    "status": "pending",
    "sku": "SKU-1001",
    "qty": 1,
    "created_at": "2026-10-20T12:00:00.123Z"
  }
  ```
- `404` unknown SKU: `{"detail": "unknown SKU"}`
- `409` insufficient stock: `{"detail": "insufficient stock for SKU-1001"}`
- `503` inventory unreachable / injection: `{"detail": "inventory unavailable"}`
- `5xx` injection-induced: `{"detail": "injected error"}` with header `X-Injection: error`

Side effects: `orders` row in Postgres; message to `fulfillment` queue.

#### `GET /orders/{order_id}`

Status read with cache read-through.

Responses:
- `200`:
  ```json
  {
    "order_id": "550e8400-e29b-41d4-a716-446655440000",
    "status": "pending",
    "sku": "SKU-1001",
    "qty": 1,
    "created_at": "2026-10-20T12:00:00.123Z",
    "fulfilled_at": null
  }
  ```
- `404` unknown id
- Cache hits include header `X-Cache: hit`; misses `X-Cache: miss` (teaching/debug affordance).

#### `POST /admin/crash` (internal only)

Immediately exit the process (`os._exit(1)`). Compose restart policy brings it back. Bind/allow only on the internal network; not host-mapped. Used by orchestrator crash injection. Always `200 {"ok": true}` if reached.

### 1.2 inventory-api

#### `GET /stock/{sku}`

- `200`: `{"sku": "SKU-1001", "available": 42}`
- `404`: unknown sku

#### `POST /stock/{sku}/reserve`

Request: `{"qty": 1}`

- `200`: `{"sku": "SKU-1001", "reserved": 1, "available": 41}`
- `409`: `{"detail": "insufficient stock"}`
- `404`: unknown sku
- `503` on injected error/crash window

#### `POST /admin/seed` (internal only, host-reachable for the seed script)

Resets the in-memory stock map to the default seed set. Always `200`:
`{"seeded": true, "stock": {"SKU-1001": 100, …}}`

#### `POST /admin/crash` — same as checkout-api.

### 1.3 fulfillment-worker

No business HTTP API. Health server on port 8000:

#### `GET /health`

- `200`: `{"status": "ok", "consumers": 1, "paused": false}`
- `503` if consumer loop dead but process alive

#### `GET /stats` — §4 (includes queue consumption counters).

#### `POST /admin/crash` — same pattern.

Worker behavior contract:
- Consumes `fulfillment` with `prefetch=8`.
- On success: update order `status=fulfilled`, `fulfilled_at=now()`; `stats.messages_acked += 1`.
- On retryable failure: republish with `attempt+1`, ack original; `messages_retried += 1`.
- On poison (`attempt >= 2` or unparseable JSON): publish to `fulfillment.dlq` with headers `x-reason`, `x-original-queue`; ack original; `messages_dead_lettered += 1`.
- While Redis flag `inj:pause_worker=1`: do not consume (basic_ack held / consumer channel paused); `paused=true` in health.

## 2. Orchestrator REST API

Mount prefix: none (root). Chaos-gated endpoints require `X-Chaos-Token` when `REQUIRE_CHAOS_TOKEN=true`.

### 2.1 Health & info

#### `GET /health` → `200 {"status": "ok"}`

#### `GET /api/topology`

Static dependency graph + latest node states (for blast-radius graph).

```json
{
  "nodes": [
    {"id": "checkout-api", "kind": "service", "health": "healthy"},
    {"id": "inventory-api", "kind": "service", "health": "degraded"},
    {"id": "fulfillment-worker", "kind": "service", "health": "healthy"},
    {"id": "postgres", "kind": "data", "health": "healthy"},
    {"id": "redis", "kind": "data", "health": "healthy"},
    {"id": "rabbitmq", "kind": "data", "health": "healthy"}
  ],
  "edges": [
    {"from": "inventory-api", "to": "checkout-api", "kind": "http"},
    {"from": "checkout-api", "to": "rabbitmq", "kind": "queue"},
    {"from": "rabbitmq", "to": "fulfillment-worker", "kind": "queue"},
    {"from": "checkout-api", "to": "redis", "kind": "cache"},
    {"from": "checkout-api", "to": "postgres", "kind": "db"},
    {"from": "fulfillment-worker", "to": "postgres", "kind": "db"}
  ]
}
```

`health` ∈ `healthy | degraded | down | recovering`. `kind` on edges ∈ `http | queue | db | cache`.

### 2.2 Load generation

#### `POST /api/load` *(gated)*

```json
{
  "profile": "mixed",
  "rps": 10,
  "duration_s": 60
}
```

- `profile`: `mixed` (70% GET /orders/{id} of known ids, 30% POST /orders) | `write` (100% POST) | `read` (100% GET).
- `202`: `{"load_id": "…", "status": "running"}`
- `409` if load already running: `{"detail": "load already running"}`

#### `POST /api/load/stop` *(gated)* → `200 {"status": "stopped"}`

#### `GET /api/load` → current/last run: `{"load_id", "status": "running|stopped", "profile", "rps", "started_at", "completed_requests", "errors"}`

- Start/stop append `LOAD_STARTED` / `LOAD_STOPPED` events to the ambient stream (`incident_id = 00000000-0000-0000-0000-000000000000`) when no incident is open; otherwise they attach to the open incident. The ambient stream is never listed by `GET /api/incidents`.

### 2.3 Failure injection

#### `POST /api/injections` *(gated)*

```json
{
  "service": "inventory-api",
  "failure_type": "latency",
  "intensity": 400,
  "ttl_s": 300
}
```

- `service`: `checkout-api | inventory-api | fulfillment-worker` (cache_outage only valid on checkout-api; queue_saturation only on fulfillment-worker).
- `failure_type` ∈ `latency | error | crash | queue_saturation | cache_outage`.
- `intensity` semantics by type:
  - `latency`: added milliseconds per request (int, 1–10000)
  - `error`: failure ratio float 0.0–1.0
  - `crash`: ignored
  - `queue_saturation`: number of synthetic messages to flood (int, 1–5000); also sets pause flag
  - `cache_outage`: ignored
- `ttl_s`: flag TTL (default 300, max 3600). Crash executes immediately; TTL irrelevant.
- `latency` / `error` on `fulfillment-worker` are accepted and recorded but inert: the worker's injection middleware only honors `inj:pause_worker` (consumer pause) — its queue loop has no per-request hot path to delay or fail.
- `201`:
  ```json
  {
    "injection_id": "inj_01H…",
    "service": "inventory-api",
    "failure_type": "latency",
    "intensity": 400,
    "ttl_s": 300,
    "created_at": "2026-10-20T12:00:00.123Z"
  }
  ```
- Appends `FAILURE_INJECTED` (and `INCIDENT_OPENED` if no active incident) to the event store.

#### `DELETE /api/injections/{injection_id}` *(gated)* → `200`; clears flag; appends `MITIGATION_APPLIED`.

#### `DELETE /api/injections` *(gated)* → clears **all** active injections; `200 {"cleared": n}`.

#### `GET /api/injections` → active injections array (same shape as POST response, plus `"active": true`).

### 2.4 Incidents & events

#### `GET /api/incidents?limit=50`

```json
[
  {
    "incident_id": "…uuid",
    "status": "open",
    "opened_at": "…",
    "closed_at": null,
    "title": "latency on inventory-api",
    "event_count": 12
  }
]
```

#### `GET /api/incidents/{incident_id}`

Incident + ordered events:

```json
{
  "incident_id": "…uuid",
  "status": "closed",
  "opened_at": "…",
  "closed_at": "…",
  "mttr_s": 84,
  "events": [
    {
      "id": 101,
      "seq": 1,
      "event_type": "INCIDENT_OPENED",
      "ts": "…",
      "payload": {"trigger": "injection"}
    }
  ]
}
```

#### `GET /api/incidents/{incident_id}/report`

```json
{
  "incident_id": "…uuid",
  "title": "latency on inventory-api",
  "root_cause": {"service": "inventory-api", "failure_type": "latency", "intensity": 400},
  "blast_radius": ["inventory-api", "checkout-api"],
  "mttr_s": 84,
  "timeline_summary": ["INCIDENT_OPENED", "FAILURE_INJECTED", "SYMPTOM_DETECTED", "…"],
  "markdown": "# Incident report\n…full markdown…"
}
```

`markdown` is generated **from the event store alone** (replayability invariant, ARCHITECTURE §5.3).

### 2.5 Queue & DLQ visibility

#### `GET /api/queue`

```json
{
  "depth": 142,
  "consumers": 1,
  "dlq_depth": 3,
  "dlq_samples": [
    {"order_id": "…", "reason": "unparseable", "at": "…"}
  ]
}
```

### 2.6 Metrics (poll fallback; WS is primary)

#### `GET /api/metrics/latest`

```json
{
  "ts": "2026-10-20T12:00:00Z",
  "services": {
    "checkout-api": {
      "rps": 10.2,
      "error_rate": 0.03,
      "p95_ms": 48,
      "in_flight": 2
    },
    "inventory-api": { "rps": 3.1, "error_rate": 0.0, "p95_ms": 12, "in_flight": 1 },
    "fulfillment-worker": { "rps": 9.8, "error_rate": 0.0, "p95_ms": 5, "in_flight": 0 }
  },
  "cache": {"hit_rate": 0.72, "hits": 810, "misses": 312},
  "queue": {"depth": 12, "dlq_depth": 0},
  "node_states": {"checkout-api": "healthy", "inventory-api": "degraded", "…": "…"}
}
```

## 3. WebSocket protocol

`GET /ws` on orchestrator (host: `ws://localhost:8000/ws`).

Server → client messages, envelope:

```json
{"type": "<type>", "ts": "…ISO…", "data": { … }}
```

| type | data | cadence |
|---|---|---|
| `metrics.tick` | same shape as `/api/metrics/latest` | 1s |
| `timeline.event` | `{incident_id, id, seq, event_type, ts, payload}` | on append |
| `injection.status` | array of active injections (§2.3 shape) | on change |
| `incident.status` | `{incident_id, status, opened_at, closed_at}` | on open/close |
| `queue.update` | `{depth, dlq_depth}` | 1–2s (piggyback ok) |

Client → server: none required in MVP (ping/pong only). Frontend drives all mutations via REST.

Reconnect: client responsibility, exponential backoff (1s → 30s cap), re-fetch `/api/metrics/latest` + `/api/injections` + active incident on reconnect.

## 4. SUT `/stats` and `/health` contract (every SUT service)

### `GET /health`

```json
{"status": "ok"}
```

`503` when the process is alive but functionally broken (optional; default `200` even under injection — injection ≠ unhealth, but detector may mark `degraded` via stats).

### `GET /stats`

```json
{
  "service": "checkout-api",
  "uptime_s": 123.4,
  "requests_total": 5432,
  "requests_error": 162,
  "in_flight": 2,
  "latency_ms": {
    "buckets": [0, 5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000],
    "counts": [10, 200, 400, 300, 120, 40, 10, 5, 2, 1, 0],
    "count": 1088,
    "sum_ms": 42100.5
  },
  "cache": {"hits": 810, "misses": 312},
  "queue_published": 400,
  "messages_acked": 390,
  "messages_retried": 2,
  "messages_dead_lettered": 1,
  "consumed": 390,
  "paused": false,
  "injections_active": ["latency"]
}
```

Rules:
- `service` ∈ `checkout-api | inventory-api | fulfillment-worker`.
- Only include service-relevant counters (checkout has `cache`, `queue_published`; worker has `consumed`, `messages_*`, `paused`; inventory has none of those).
- Counters are cumulative since process start (monotonic); orchestrator differentiates rates.
- `latency_ms.counts[i]` counts requests with `bucket[i-1] < latency <= bucket[i]` (first bucket = ≤ buckets[0]). p95 is approximated from buckets by the orchestrator.
- Stats endpoint itself must **not** be counted in `requests_total` (exclude `/health`, `/stats`, `/admin/*`).

## 5. Redis key contract (injection flags + cache)

| Key | Type | Value | Set by | Read by |
|---|---|---|---|---|
| `inj:latency:{service}` | string | ms int | orchestrator | checkout-api, inventory-api middleware |
| `inj:error:{service}` | string | float 0.0–1.0 | orchestrator | same |
| `inj:cache_outage` | string | `"1"` | orchestrator | checkout-api cache layer |
| `inj:pause_worker` | string | `"1"` | orchestrator | fulfillment-worker |
| `cache:order:{order_id}` | string | JSON order snapshot | checkout-api | checkout-api |

All `inj:*` keys carry a TTL (default 300s) set on write; readers treat missing key as no injection. Services may cache flag values ≤250ms (bounded staleness). Flag read errors fail open (no injection).

The fulfillment-worker **deletes** `cache:order:{order_id}` after marking an order `fulfilled` (write-invalidation, so status reads don't serve stale `pending` for the full TTL).

## 6. Postgres schema (canonical)

```sql
-- orders (SUT)
CREATE TABLE orders (
    order_id    UUID PRIMARY KEY,
    sku         TEXT NOT NULL,
    qty         INT NOT NULL,
    customer_ref TEXT,
    status      TEXT NOT NULL DEFAULT 'pending',  -- pending | fulfilled | failed
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    fulfilled_at TIMESTAMPTZ
);

-- incident event store (orchestrator) — APPEND ONLY
CREATE TABLE incident_events (
    id          BIGSERIAL PRIMARY KEY,
    incident_id UUID NOT NULL,
    seq         BIGINT NOT NULL,
    event_type  TEXT NOT NULL,
    payload     JSONB NOT NULL DEFAULT '{}',
    ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (incident_id, seq)
);

-- optional projection (never source of truth)
CREATE TABLE incidents (
    incident_id UUID PRIMARY KEY,
    title       TEXT,
    status      TEXT NOT NULL DEFAULT 'open',
    opened_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at   TIMESTAMPTZ
);
```

RabbitMQ:
- `fulfillment` (durable queue), `fulfillment.dlq` (durable).
- Message body: `{"order_id": "…", "sku": "…", "qty": 1, "attempt": 0}`.

## 7. Environment variables (`.env.example` source of truth)

| Var | Default | Used by |
|---|---|---|
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | `forge` / `forge` / `forge` | pg, SUT, orchestrator |
| `DATABASE_URL` | `postgresql+asyncpg://…` | SUT, orchestrator |
| `REDIS_URL` | `redis://redis:6379/0` | SUT, orchestrator |
| `RABBITMQ_USER` / `RABBITMQ_PASSWORD` | `forge` / `forge` | broker (compose creates a non-loopback user; guest is loopback-only) |
| `RABBITMQ_URL` | `amqp://forge:forge@rabbitmq:5672/` | checkout-api, worker, orchestrator |
| `CHECKOUT_API_URL` / `INVENTORY_API_URL` | internal compose URLs | checkout-api, orchestrator |
| `WORKER_API_URL` | `http://fulfillment-worker:8000` (internal) | orchestrator (crash injection) |
| `RABBITMQ_MGMT_URL` | `http://forge:forge@rabbitmq:15672` | orchestrator (queue depth + DLQ panel) |
| `CHAOS_TOKEN` | `dev-token` | orchestrator, curl/README |
| `REQUIRE_CHAOS_TOKEN` | `false` local / `true` live | orchestrator |
| `INJECTION_TTL_DEFAULT_S` | `300` | orchestrator |
| `ORCHESTRATOR_URL` | `http://orchestrator:8000` | frontend (server-side) / ws URL |
| `NEXT_PUBLIC_ORCHESTRATOR_URL` | `http://localhost:8000` | frontend browser (host-mapped REST/WS) |

> Frontend uses `NEXT_PUBLIC_ORCHESTRATOR_URL` for browser-side REST/WS. Keep names consistent with this table.
