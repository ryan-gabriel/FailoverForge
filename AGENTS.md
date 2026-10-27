# AGENTS.md — Failover Forge

> Instructions for AI coding agents (opencode-native) building this repo.
> Read `docs/PRD.md` first (scope + phases), then `docs/ARCHITECTURE.md` and `docs/API_SPEC.md`.
> **When PRD and your idea conflict: PRD wins. When API_SPEC and your code conflict: fix the code.**

## 0. Mission

Build **Failover Forge**: a real 3-service order/checkout system you can break on purpose, with a live dashboard showing blast radius, cascades, and an event-sourced incident timeline.

Vibe-coding rules of engagement:
- Work **one phase at a time** (§4 checklists). Finish phase acceptance before starting the next.
- Never skip tests/lint for a "quick" phase — broken foundations compound.
- Do not add features from PRD §6.2 (out of scope) or the roadmap. No circuit breakers, no k8s, no sliders, no auth systems in MVP.
- Small, coherent commits per logical unit (not per file).

## 1. Stack (fixed — do not swap)

| Layer | Choice | Notes |
|---|---|---|
| SUT + orchestrator | Python 3.12, FastAPI, uvicorn, httpx, asyncio | All backend code Python. No Django/Laravel/Go. |
| Data stores | PostgreSQL 16, Redis 7, RabbitMQ 3 (management plugin on) | Docker images only; no managed cloud in MVP |
| Frontend | Next.js 15 (App Router), React, TypeScript, Tailwind CSS | Charts: **recharts** (keep deps light). Graph: **reactflow** or plain SVG — decide once in Phase 2, no library churn. |
| Package mgmt (Python) | **uv** + `pyproject.toml` per service | Lock with `uv.lock` |
| Package mgmt (JS) | pnpm or npm — pick npm for zero-config | One lockfile |
| Tests | pytest + pytest-asyncio (backend), vitest optional (frontend), `tsc --noEmit` mandatory | |
| Lint/format (Python) | ruff (lint + format), mypy basic on orchestrator | |
| Lint (Frontend) | ESLint + `npm run typecheck` (`tsc --noEmit`) | |

## 2. Commands (canonical)

```bash
# one-time
cp .env.example .env

# run everything
docker compose up --build

# smoke test end-to-end
./scripts/smoke.sh

# backend tests (run inside container or with uv)
cd services/orchestrator && uv run pytest
cd services/checkout-api && uv run pytest

# lint / format
uv run ruff check services/
uv run ruff format services/
uv run mypy services/orchestrator

# frontend
cd frontend && npm install && npm run lint && npm run typecheck && npm run build

# seed demo data
./scripts/seed.sh
```

Before declaring any phase done: `docker compose up --build` clean, `./scripts/smoke.sh` pass, ruff clean, pytest pass, frontend lint+typecheck pass.

## 3. Repo layout (create exactly this; don't invent parallel structures)

```
p2-failover-forge/
├── AGENTS.md
├── README.md                  # Phase 4
├── .env.example               # committed; real .env gitignored
├── docker-compose.yml
├── Makefile                   # smoke, seed, test, lint targets
├── scripts/
│   ├── smoke.sh
│   └── seed.sh
├── docs/
│   ├── PRD.md
│   ├── ARCHITECTURE.md
│   └── API_SPEC.md
├── services/
│   ├── checkout-api/
│   │   ├── pyproject.toml
│   │   ├── Dockerfile
│   │   ├── app/
│   │   │   ├── main.py        # FastAPI app + lifespan
│   │   │   ├── routes.py
│   │   │   ├── cache.py       # Redis read-through
│   │   │   ├── db.py          # asyncpg/SQLAlchemy async
│   │   │   ├── mq.py          # aio-pika publisher
│   │   │   ├── injection.py   # Redis flag middleware  ← core pattern
│   │   │   └── stats.py       # /stats counters + latency histogram
│   │   └── tests/
│   ├── inventory-api/         # same shape
│   ├── fulfillment-worker/
│   │   ├── pyproject.toml
│   │   ├── Dockerfile
│   │   ├── app/
│   │   │   ├── main.py        # consumer loop + health server
│   │   │   ├── consumer.py
│   │   │   ├── injection.py   # pause flag
│   │   │   └── stats.py
│   │   └── tests/
│   └── orchestrator/
│       ├── pyproject.toml
│       ├── Dockerfile
│       ├── app/
│       │   ├── main.py
│       │   ├── loadgen.py
│       │   ├── injector.py
│       │   ├── detector.py
│       │   ├── eventstore.py  # append-only Postgres
│       │   ├── aggregator.py  # polls /stats → rollups
│       │   ├── ws.py          # hub
│       │   ├── reports.py
│       │   └── routers/       # injection, incidents, load, topology
│       └── tests/
└── frontend/
    ├── package.json
    ├── Dockerfile             # build + nginx or `next start` image
    └── src/
        ├── app/
        │   ├── page.tsx              # dashboard
        │   └── incidents/
        │       ├── page.tsx
        │       └── [id]/page.tsx
        ├── components/
        │   ├── MetricsCharts.tsx
        │   ├── BlastRadiusGraph.tsx
        │   ├── TriggerPanel.tsx
        │   ├── IncidentTimeline.tsx
        │   └── ReportView.tsx
        └── lib/
            ├── ws.ts           # WS client + reconnect
            └── api.ts          # typed fetch client from API_SPEC
```

Shared Python code (injection middleware pattern, stats helpers): **duplicate per service** rather than a shared package in MVP — container builds stay trivial. If duplication hurts, extract `services/common/` as a local path dependency only.

## 4. Phase checklists (burn down in order)

### Phase 0 — Scaffold & healthy world
- [ ] `docker-compose.yml`: postgres, redis, rabbitmq (management), 3 SUT services, orchestrator stub (`/health` only), frontend dev stub
- [ ] `.env.example` with all vars (ports, passwords, `CHAOS_TOKEN=dev-token`)
- [ ] checkout-api: `POST /orders`, `GET /orders/{id}`, Postgres orders table, Redis cache read-through, RabbitMQ publish, calls inventory reserve
- [ ] inventory-api: seed SKUs, `GET /stock/{sku}`, `POST /stock/{sku}/reserve`
- [ ] fulfillment-worker: consume queue → mark order `fulfilled` in Postgres; poison → DLQ
- [ ] `scripts/seed.sh`, `scripts/smoke.sh` (create order → poll → fulfilled)
- [ ] Per-service `/health` and `/stats` (even if minimal)
- **Accept:** smoke green.

### Phase 1 — Orchestrator core
- [ ] `injection.py` middleware in checkout-api + inventory-api + worker (latency, error ratio, cache outage, pause-worker) reading Redis flags with TTL — see API_SPEC §5
- [ ] Crash injection: `POST /admin/crash` on SUT services (bound to internal network; orchestrator-only); compose `restart: on-failure`
- [ ] Event store: `incident_events` append-only table + `eventstore.py` (append, list, never update)
- [ ] `injector.py`: set/clear flags + append events
- [ ] `loadgen.py`: asyncio httpx, RPS + duration + endpoint mix
- [ ] `aggregator.py`: poll `/stats` every 1s, store rollups (in Postgres or in-memory ring + latest in Redis — pick one, document in code)
- [ ] Orchestrator REST: injection, load, incidents, topology per API_SPEC
- [ ] pytest: eventstore append/read, injector flag roundtrip, loadgen math (RPS pacing)
- **Accept:** curl inject latency → stats show p95 rise → event rows exist.

### Phase 2 — Dashboard
- [ ] WS hub broadcasting: `metrics.tick`, `injection.status`, `topology.update`
- [ ] Frontend: metrics charts (RPS, error %, p95, queue depth, cache hit rate)
- [ ] Blast-radius graph: static topology from orchestrator + live node/edge status; cascade highlight on propagation
- [ ] TriggerPanel: per-service buttons for 4 failures + clear; confirmation not required (demo), but show active flags
- [ ] `lib/ws.ts` with auto-reconnect + backoff
- **Accept:** trigger all 4 failures from UI; metrics + graph respond live.

### Phase 3 — Timeline & reports
- [ ] Incident lifecycle: opens on first injection or threshold breach; closes when all injections cleared + healthy
- [ ] Timeline page: ordered immutable events with payload expansion
- [ ] `reports.py`: generate markdown report (root cause, blast radius, timeline, MTTR) from events only
- [ ] DLQ panel: depth + sample messages (via RabbitMQ management API or amqp client)
- [ ] `/incidents` list + `/incidents/[id]` detail
- **Accept:** full demo incident → report generated from event store alone.

### Phase 4 — Hardening & polish
- [ ] Graceful shutdown everywhere (drain WS, finish in-flight, close pools)
- [ ] Compose healthchecks; `depends_on: condition: service_healthy` where needed
- [ ] README: elevator pitch, unique angle, quick start, architecture Mermaid, concepts demonstrated, tradeoffs, free-tier notes
- [ ] Makefile targets green; full pytest/ruff/typecheck/build pass
- [ ] 60-second demo works from clean clone (PRD §12)
- **Accept:** PRD §8 Phase 4 acceptance.

### Phase 5 — Live deploy (only after Phase 4 done)
- [ ] Production Dockerfiles; Fly.io or Koyeb app(s); Vercel frontend
- [ ] Injection endpoints require `X-Chaos-Token` matching env; live token secret; rate-limit injections
- [ ] README live section + demo clip placeholder

## 5. Coding conventions

- **Python:** `async def` everywhere I/O happens; no blocking calls in event loop (use `asyncio.to_thread` if forced). Type hints on public functions. Ruff default line length.
- **Injection pattern (all SUT services):**
  ```python
  # injection.py — read Redis flag per request; fail-open (no flag = healthy)
  # keys: inj:latency:{service} (ms int), inj:error:{service} (0.0–1.0),
  #       inj:cache_outage ("1"), inj:pause_worker ("1")
  # flags have TTL (default 300s) set by orchestrator
  ```
- **Event store:** only `eventstore.py` touches `incident_events`. Append-only — no updates, no deletes.
- **Stats:** every SUT service exposes `GET /stats` and `GET /health` per API_SPEC; counters incremented on the hot path with a lock-free structure (e.g., plain ints + `time.perf_counter` histogram buckets defined in API_SPEC §4).
- **Frontend:** TypeScript strict; all WS/API payloads typed in `lib/` from API_SPEC; no business logic in components beyond display; reconnect logic centralized.
- **Config:** env vars via pydantic-settings (Python) / `process.env` (Next). Never hardcode secrets. `.env.example` is the source of truth for var names.
- **Errors:** services fail open on injection-flag read errors (Redis down for flags ≠ crash the SUT; treat as no injection). Log to stdout structured (JSON single-line preferred).
- **IDs:** incident IDs and order IDs are UUIDv4 generated server-side.

## 6. Anti-patterns (will be rejected in review)

- Fabricating metrics client-side or in the frontend.
- Mutable incident timeline (UPDATE/DELETE on events).
- Orchestrator importing SUT code or vice versa.
- Direct service-to-Redis injection writes from the frontend.
- Adding auth providers, databases, message buses, or chart libraries not listed here.
- "Temporary" hardcoded demo data in handlers (seed via `scripts/seed.sh`).
- Skipping `docker compose up --build` verification after compose changes.

## 7. Delegation hints (opencode)

- Use the **explore** agent for codebase orientation questions ("where is injection handled?").
- Implement phases directly; keep each phase in one session thread of work to preserve context.
- After each phase: run the phase's verification commands (§2) before marking checklist items done in your todo list.
- When unsure about a contract detail, re-read `docs/API_SPEC.md` — it is the shared source of truth for backend ↔ frontend agents.

## 8. Definition of done (whole project MVP)

PRD §8 Phase 4 acceptance + PRD Appendix checklist all green + README complete + demo script works from cold clone.
