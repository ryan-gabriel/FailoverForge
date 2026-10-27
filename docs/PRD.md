# PRD: Failover Forge

> **Version:** 1.0 · **Status:** Locked after grilling session · **Owner:** Ryan
> **Repo root:** `p2-failover-forge/`

## 1. Elevator Pitch

Failover Forge trains on-call engineers by letting them break a real production-shaped system safely. A live dashboard shows blast radius, cascading failures, and an event-sourced incident timeline — making distributed systems behavior visible instead of theoretical.

**Recruiter hook:** "Built a chaos-engineering platform demonstrating backpressure, DLQ, failover, and replayable incident timelines — real services, real failures, visible blast radius."

## 2. Product Thesis

Most portfolio projects show the happy path. Failover Forge is infrastructure-as-lesson: the product *is* the failure. A recruiter (or the builder) injects a fault into a running multi-service system and watches the consequences propagate in real time, then reads a generated post-incident report.

Non-goals for framing: this is **not** a production chaos platform (no Gremlin/Chaos Monkey comparison claims), **not** multi-tenant SaaS, **not** a real e-commerce product.

## 3. Locked Decisions (from grilling session)

| Axis | Decision | Consequence |
|---|---|---|
| System realism | **Real services, real failures** — 3 real FastAPI-backed services process real HTTP/queue traffic | Infrastructure learning is genuine; failures have real effects |
| Injection method | **App-level injection** (middleware + admin flags via Redis) | Works on Docker Compose day 1; Chaos Mesh/k3s is roadmap-only, out of MVP |
| Demo workload | **Order/checkout API** — API → Postgres orders, Redis cache, RabbitMQ fulfillment + DLQ | Queue saturation and cache outage map naturally to the domain |
| MVP bar | **Docker Compose only.** Live deploy (Fly/Koyeb + Vercel) = Phase 5. k3s/Chaos Mesh = roadmap | Intermediate shippable states exist; "done" is phased |
| Failure count | **4 core:** injected latency, error/crash, queue saturation, cache outage. **DB kill = local-only stretch** | Keeps managed-Postgres live deploy viable |
| Load generation | **Built into orchestrator** (asyncio + httpx) | One control plane; no Locust/k6 dependency |
| Dashboard panels | Live metrics charts, blast-radius graph, incident timeline, post-incident report + **minimal trigger UI** | "Chaos control panel" with intensity sliders is stretch, not MVP |
| Circuit breakers / backpressure | **Out of MVP** (Phase 2 roadmap) | MVP shows cascading failure *honestly*; Phase 2 shows the fix — better narrative |
| Live demo auth | **Public read-only dashboard; injection gated** (token; may be disabled live) | No strangers DDoSing a free-tier VM |
| Stack | **All Python/FastAPI backend; Next.js + React + Tailwind frontend** | Single backend toolchain for agent iteration speed |
| Target harness | **opencode** — AGENTS.md is opencode-native | See `AGENTS.md` |

## 4. Users & Jobs-To-Be-Done

1. **Primary user — the builder / on-call trainee:** "I want to break a realistic system and *see* what happens so I internalize failure modes."
2. **Secondary user — recruiter / interviewer (viewer):** "In 60 seconds I want to understand this person can design resilient systems — not just talk about them."
3. **Tertiary — future interview prep:** "I want a repeatable demo I can run live in interviews."

## 5. System Under Test (the demo workload)

A fictional but realistic order/checkout flow:

- **checkout-api** — `POST /orders` (create order), `GET /orders/{id}` (status). Reads Redis cache for order status; on cache miss reads Postgres. Publishes fulfillment message to RabbitMQ. Calls inventory-api synchronously to reserve stock.
- **inventory-api** — `GET /stock/{sku}`, `POST /stock/{sku}/reserve`. In-memory stock map seeded at startup (Postgres optional for stock persistence — prefer in-memory for MVP simplicity).
- **fulfillment-worker** — consumes RabbitMQ queue, fulfills orders (writes status `fulfilled` to Postgres), publishes result. On poison messages → DLQ.

Plus control plane (not part of the SUT):

- **orchestrator** — load generator, failure-injection engine, event-sourced incident log, metrics aggregation, WebSocket hub, report generator.
- **frontend** — Next.js dashboard.

Shared infra: **Postgres** (orders + append-only incident event store), **Redis** (cache + injection-flag store + counters), **RabbitMQ** (fulfillment queue + DLQ).

## 6. Scope

### 6.1 In scope — MVP (Phases 0–4)

- Docker Compose stack: 3 SUT services + orchestrator + Postgres + Redis + RabbitMQ + frontend.
- Orchestrator load generator: configurable RPS, mix of `POST /orders` and `GET /orders/{id}`, duration.
- Failure injection (4 types), real, app-level:
  1. **Injected latency** — per-service added delay (ms) via middleware flag.
  2. **Error/crash** — elevated HTTP error rate on target service; "crash" = process exits and Docker restarts it (restart policy), demonstrating downtime + recovery.
  3. **Queue saturation** — orchestrator floods fulfillment queue with synthetic messages and/or pauses worker consumption; DLQ visibility.
  4. **Cache outage** — cache treated as unavailable (service bypasses/fails cache calls per flag); demonstrates origin stampede.
- DB kill (stop Postgres container) — **local-only stretch**, documented, not required for MVP acceptance.
- Event-sourced incident timeline: append-only Postgres log; immutable events (injected, detected, cascade, mitigated, resolved).
- Live dashboard (WS): metrics charts (RPS, error %, p95 latency, queue depth, cache hit rate), blast-radius graph (dependency graph lighting up cascades), incident timeline, post-incident report view, minimal trigger UI (inject/mitigate buttons).
- Post-incident report: root cause, blast radius, timeline, MTTR — generated from the event store.
- Health checks + graceful shutdown on all services.
- Seed script + demo script (one command starts the world; documented 60-second demo path).
- Tests: pytest per service, contract tests for API_SPEC, frontend lint/typecheck.

### 6.2 Out of scope — MVP

- Circuit breakers, bulkheads, backpressure, retries-with-jitter (Phase 2).
- Kubernetes / k3s / Chaos Mesh / Litmus (roadmap).
- Multi-user, accounts, auth beyond shared-secret chaos token.
- Intensity sliders, scheduled game-days, scoring, role-based war rooms (stretch).
- Packet-loss injection (network-level; needs k8s — roadmap).
- Real payment/PII anything.
- Cloud-managed Redis/RabbitMQ (live deploy uses containers for these, or Fly internal services — decided in Phase 5).

### 6.3 Roadmap (post-MVP, ordered)

1. **Phase 5 — Live deploy:** Fly.io/Koyeb (orchestrator + SUT + data services as containers), Vercel frontend, injection gated by `CHAOS_TOKEN`, demo video in README.
2. **Phase 6 — Resiliency features:** circuit breakers + backpressure as *mitigations* toggleable per incident (the "fix" half of the story).
3. **Phase 7 — Local k3s:** manifests for kind/k3s; pod-kill failure becomes real; Ingress + graceful drain.
4. **Phase 8 — Chaos Mesh:** network delay/partition experiments once k8s exists.
5. Stretch: game-day scoring, war-room roles, incident comparison (two incidents diffed).

## 7. Failure Taxonomy (MVP)

| # | Failure | Injection mechanism (app-level) | Observable symptoms | Blast-radius edges affected |
|---|---|---|---|---|
| F1 | Injected latency | Redis flag `inj:latency:{service}` = ms; middleware sleeps per request | p95 latency spike, timeouts downstream, queue backlog if producer blocked | checkout-api → inventory-api (sync call stalls) |
| F2 | Error rate / crash | Redis flag `inj:error:{service}` = failure_ratio; middleware returns 5xx. Crash mode: admin endpoint triggers `os._exit`/SIGTERM self-kill → Docker restarts container | Error % spike, client-visible failures, brief connection refused during restart | inventory-api errors → checkout-api 5xx; checkout crash → all traffic fails |
| F3 | Queue saturation | Orchestrator floods queue with synthetic fulfill messages + optional worker pause flag (`inj:pause_worker`) | Queue depth climbs, order fulfillment lag, memory pressure, DLQ if poison injected | checkout-api keeps accepting (buffering) until backpressure absent → growing lag |
| F4 | Cache outage | Redis flag `inj:cache_outage` = "1"; services treat cache as miss-only (and optionally add small latency to mimic timeout) | Cache hit rate → 0, Postgres QPS spike, latency rise | checkout-api → Postgres overload risk |

| Stretch | DB kill (local-only) | `docker compose stop postgres` via documented command (or orchestrator exec API local-only) | All DB-backed operations fail; recovery on restart | Whole graph |

**Detection:** orchestrator polls service `/stats` + `/health`; thresholds (error % > X, latency > Y, queue depth > Z) raise SYMPTOM_DETECTED events. Detection is heuristic, not magic.

## 8. Phased Definition of "Done"

Each phase ends in a **shippable, demoable state**. Agents must not skip ahead.

### Phase 0 — Scaffold & healthy world
- Repo layout per AGENTS.md; `docker compose up` starts 5 backend containers (3 SUT + orchestrator stub + data services) all green.
- Seed script loads SKUs/stock. Smoke test: create order end-to-end → fulfillment completes.
- **Accept:** `make smoke` (or script) passes: POST order → worker fulfills → GET shows `fulfilled`.

### Phase 1 — Orchestrator core
- Load generator (httpx, asyncio) drives configurable traffic.
- Service stats endpoints (`/stats`, `/health`) instrumented; orchestrator polls and stores 1s rollups.
- Injection engine: set/clear the 4 failure flags; events appended to incident store.
- **Accept:** via curl, inject latency on inventory-api; observe checkout p95 rise in orchestrator logs/API; event appears in `incident_events`.

### Phase 2 — Dashboard
- Next.js dashboard over WS: live metrics charts, blast-radius graph, minimal trigger UI (inject/mitigate buttons per service per failure type).
- **Accept:** inject each of 4 failures from the UI and see metrics + graph respond in real time.

### Phase 3 — Incident timeline & reports
- Incident lifecycle (open on first injection or threshold breach; close on clear), timeline view, generated post-incident report (markdown-rendered in UI + `GET` endpoint).
- DLQ inspection view (count + sample messages).
- **Accept:** run a full demo incident; timeline shows ordered immutable events; report includes root cause, blast radius, MTTR.

### Phase 4 — Hardening & demo polish
- Graceful shutdown, healthchecks in compose, README with demo script, 60-second demo path, Mermaid architecture diagram, tradeoffs section.
- pytest suites green; frontend `lint` + `typecheck` green.
- **Accept:** cold-clone → `cp .env.example .env` → `docker compose up --build` → follow README demo → works. Checklist in Appendix of PRD passes.

### Phase 5 — Live deploy (post-MVP)
- Fly/Koyeb deployment of backend stack; Vercel frontend; injection requires `CHAOS_TOKEN` or is disabled; rate-limited.
- **Accept:** public URL shows live dashboard; README embeds demo clip.

## 9. Success Metrics

Portfolio signals (primary):
- **Time-to-demo < 5 minutes** from cold clone for a stranger.
- **4/4 failure types** each produce a *visible, distinct* cascade on the dashboard.
- **Replayability:** any past incident reconstructable from the event store alone (events are sufficient to rebuild the report).
- Advanced patterns **actually implemented, not named:** event-sourced timeline, DLQ with replay, blast-radius propagation model, health-based detection, graceful shutdown.

Engagement (secondary, if live):
- Demo video views / GitHub stars — vanity, tracked but not gating.

## 10. Key Design Constraints (for implementers)

1. **Event store is append-only.** No UPDATE/DELETE on `incident_events`. Corrections are new events.
2. **Injection flags live in Redis** with TTL (default 300s, renewable) so a crashed orchestrator can't permanently break the SUT.
3. **Services must be injection-unaware of the orchestrator** — they only read Redis flags; no direct orchestrator→service coupling except crash admin endpoint (localhost/admin network).
4. **Orchestrator never lies:** dashboard data comes from polled real service stats + real queue depth (RabbitMQ management API or client), not fabricated series.
5. **Everything runs offline** with Docker Compose; no paid API keys, no cloud dependency for MVP.
6. **Frontend is dumb:** all incident logic, detection, aggregation server-side in orchestrator.

## 11. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Crash-mode restart loops thrash compose | Restart policy `on-failure` with max retries documented; crash injection capped in duration; UI warns |
| Queue flood fills disk/memory | Flood messages small; cap flood volume; alert at depth threshold; always allow clear-injection |
| p95 computation naive/wrong | Use histogram buckets in service stats (explicit buckets in API_SPEC); document approximation |
| Agents build UI before event store | Phase gating in AGENTS.md checklists; PRD acceptance is phase-ordered |
| Live deploy costs/quotas | Fly/Koyeb free-tier with always-on budget check; fall back to demo video; documented in README |
| Scope creep (sliders, war rooms) | §6.2 out-of-scope list is authoritative; stretch only after Phase 4 acceptance |

## 12. Demo Script (60-second path, for README)

1. `docker compose up --build` → open dashboard.
2. Start load (default profile: 10 rps mixed).
3. Inject **latency** on inventory-api → watch p95 climb, blast radius light up checkout→inventory edge.
4. Escalate: inject **cache outage** on checkout-api → cache hit rate drops, Postgres load rises.
5. Inject **queue saturation** → depth chart climbs, fulfillment lag visible.
6. Clear all → recovery; open **incident timeline** → scrub events; open **post-incident report** → MTTR + blast radius summary.

## 13. Open Questions (deliberately deferred)

- Exact Fly vs Koyeb choice (Phase 5; decide at deploy time).
- Whether inventory stock persists in Postgres or stays in-memory (default: in-memory; document).
- Prometheus/Grafana as optional sidecar (roadmap; MVP uses own `/stats` + orchestrator aggregation to keep the story in one UI).
