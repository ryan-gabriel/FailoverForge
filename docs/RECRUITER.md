# Failover Forge — Project Case Study

*A one-read summary for reviewers, hiring managers, and recruiters.
Written so someone can decide in five minutes whether to run the demo.*

- **Author:** Ryan Gabriel S.
- **Type:** solo portfolio project, built AI-assisted (opencode) with a
  spec-first workflow — see [Working method](#6-working-method).
- **Status:** Phases 0–4 feature-complete locally on Docker Compose; Phase 5
  (public deploy) pending.
- **Run it:** 3 commands, no cloud account, no keys — see
  [`SETUP.md`](SETUP.md).
- **Verified:** a 19-stage run log from a clean `docker compose down -v && up
  --build -d` boot, including a real latency cascade, queue saturation, and a
  dead-lettered message — [`SETUP.md` §10](SETUP.md#10-verification-record).
- **Live demo:** _add URL after Phase 5_ · **Demo clip:** _add link_
- **Repo:** _add URL_

---

## 1. Elevator pitch

A real three-service order system that you can **break on purpose**, with a live
dashboard showing the damage as it spreads — plus an incident timeline and a
generated post-incident report built from an append-only event store.

Most portfolio projects demonstrate the happy path. This one demonstrates the
failure path: latency, random errors, a crashed container, a backed-up queue, a
dead cache. Metrics are measured by the services themselves and polled by a
control plane; nothing on screen is faked.

**The question it answers:** what does my code do when the system is on fire?

---

## 2. The problem I set myself

Distributed-systems failure modes are usually explained with diagrams, or
simulated with a fake "chaos" UI. I wanted the real mechanics in a small enough
surface that I could hold the whole thing in my head:

- genuine **HTTP timeouts and cascading errors** across a service boundary,
- genuine **queue backpressure** and a **dead-letter queue**,
- genuine **cache-miss amplification** onto the database,
- genuine **process death and restart**,
- and a **durable record** of what happened, from which a report can be rebuilt.

## 3. What the system does

1. `checkout-api` takes an order, reserves stock over synchronous HTTP from
   `inventory-api`, persists it in Postgres, and publishes a message to RabbitMQ.
2. `fulfillment-worker` consumes the queue and marks the order fulfilled, then
   invalidates its Redis cache entry.
3. The **orchestrator** (control plane) generates load at a chosen RPS, injects
   failures by writing expiring flags to Redis, polls every service's `/stats`
   endpoint once per second, derives RPS / error rate / approximate p95 / queue
   depth, detects threshold breaches, appends immutable events, and pushes live
   updates to the browser over WebSocket.
4. The **frontend** renders the metrics charts, a blast-radius graph whose edges
   highlight when a cascade crosses a dependency, the incident timeline, and the
   report.

```
 frontend ──REST/WS──▶ orchestrator ──load──▶ checkout-api ──▶ inventory-api
                            │  flags              │  queue            │
                            ▼                     ▼                   ▼
                        event store           RabbitMQ ──▶ fulfillment-worker
                            │                                 │
                            ▼                                 ▼
                        Postgres  ◀────────── status update ──┘
```

## 4. What it demonstrates, technically

| Area | Evidence in the code |
|---|---|
| Async Python at scale | `asyncio` + FastAPI + `httpx` + `aio-pika` across four services; no blocking calls on the event loop; graceful shutdown with best-effort teardown |
| Event sourcing | append-only `incident_events`; per-incident `seq` with a `UNIQUE` constraint; the report is generated from events alone, never from live state |
| Real metrics engineering | cumulative counters + fixed latency histogram per service; the control plane differentiates counters into rates; percentile approximation from buckets |
| Failure semantics | TTL-expiring flags, fail-open flag reads, local 250 ms flag cache, `restart: on-failure` for crash recovery, bounded retry then manual dead-lettering |
| Detection logic | threshold detector with per-metric cooldown, incident open/close rules, one-incident-at-a-time invariant |
| Frontend craft | Next.js 15 App Router, React 19, TypeScript strict, hand-drawn SVG topology, recharts, WebSocket client with exponential backoff and REST resync on reconnect |
| Ops / DX | one-command Compose stack with healthchecks and `depends_on: service_healthy`, Makefile gate, smoke + seed scripts, docs suite, containerized builds with `uv` |
| Security thinking | injection endpoints gated by a shared `X-Chaos-Token`; admin endpoints separated from public API surface; non-loopback broker user instead of RabbitMQ's `guest` |

## 5. Decisions and tradeoffs I can defend

- **Application-level injection instead of network/OS chaos.** Reading a Redis
  flag inside middleware runs anywhere Compose runs and is deterministic and
  observable. It cannot emulate a real network partition — an honest limitation,
  documented, with k8s/Chaos Mesh explicitly out of scope for MVP.
- **Two planes, one direction.** The system under test never calls the control
  plane, so the observer cannot be dragged down by the thing it is observing.
- **In-memory rollups, persistent events.** Chart history is a 900-tick ring that
  resets on restart; the incident story survives because it lives in Postgres.
  Cheap, and it makes the replayability property obvious.
- **Duplicated injection/stats middleware per service.** Keeps every container
  build trivial; extraction into a shared package is a documented escape hatch,
  not a premature abstraction.
- **Plain SVG graph instead of reactflow.** A six-node graph did not justify the
  dependency, and the cascade rule ("highlight an edge when both ends are
  unhealthy") is easier to reason about in-house.
- **Approximate p95 and a 5 s histogram ceiling.** Chose bounded memory over
  precision; documented that a 6 s injected delay can be invisible in p95.
- **One open incident at a time.** Simplifies the demo and the state machine;
  concurrency would be a real-world next step.

## 6. Working method

The project was built spec-first: a PRD with hard scope boundaries, an
architecture doc, and an API contract written before implementation, then
delivered phase by phase with an acceptance gate at each phase (Compose healthy →
smoke test green → tests/lint/typecheck clean). AI agents (opencode) did
implementation under those documents; the docs remain the shared source of truth
between backend and frontend work, which is why the contract held as the code
grew.

## 7. Debugging stories worth telling

**The broker that was healthy for everyone but its clients.** RabbitMQ looked
fine — container healthy, `ping` passing — while every producer and consumer got
refused. The cause: RabbitMQ restricts the default `guest` user to loopback
connections, and container-to-container traffic is not loopback. That rule applies
to AMQP *and* the management HTTP API, so both the fulfillment path and the queue
panel broke together. Fix: a dedicated non-loopback user seeded from `.env` via
`RABBITMQ_DEFAULT_USER`, with the credentials threaded through the four config
modules, the env template, and the docs — plus a documented reset path
(`docker compose down -v`), because that user is only created on a fresh broker
data directory.

**The race the healthcheck was lying about.** After that fix, cold boots still
logged `amqp connect failed … Connection refused` for a few seconds: the broker's
healthcheck used `rabbitmq-diagnostics -q ping`, which answers once the node is
alive but *before* the AMQP listener accepts sockets — so Compose released its
dependents against a broker that was not yet connectable. Fix: probe actual port
connectivity (`check_port_connectivity`) instead, which is what `depends_on:
service_healthy` is supposed to guarantee. Verified: zero AMQP connection warnings
on a fresh `down -v && up --build -d`.

Both are the kind of thing that only shows up when you actually run the system,
and both are written down with their symptom tables in
[`SETUP.md`](SETUP.md#8-troubleshooting). The most recent full run of every check
— 19 stages, from `docker version` through a real latency cascade with MTTR 16.1 s,
queue saturation, and a dead-lettered message — is recorded in
[`SETUP.md` §10](SETUP.md#10-verification-record).

## 8. How to review me in 90 seconds

Prereq: Docker Desktop running (see [`SETUP.md`](SETUP.md)).

```bash
cp .env.example .env && docker compose up --build -d && ./scripts/smoke.sh
open http://localhost:3000
```

| Time | Action | What you should see |
|---|---|---|
| 0:00 | Dashboard, load running | green graph, flat p95, `X-Cache` hit rate high |
| 0:15 | Inject latency on `inventory-api` (2000 ms) | p95 spike, two nodes degrade, cascade edge highlights, incident opens |
| 0:35 | Inject cache outage on `checkout-api` | hit rate collapses, Postgres-bound reads climb |
| 0:50 | Inject queue saturation | queue depth climbs, orders stick at `pending` |
| 1:05 | **Clear all** | nodes recover, incident auto-closes |
| 1:20 | Incidents → this incident → report | root cause, blast radius, immutable timeline, MTTR |

## 9. Recording the demo

If you capture a clip, this is the shot list that maps to the sections above:

1. Terminal: `docker compose up --build -d` → `docker compose ps` all healthy
   (proves it runs from cold).
2. `./scripts/smoke.sh` printing `SMOKE OK`.
3. Healthy dashboard for ~5 seconds of calm baseline.
4. One injection at a time, pausing on each chart so the cause and effect read
   clearly: p95 → error rate → cache hit rate → queue depth.
5. The blast-radius graph while the cascade edge highlights.
6. The incident timeline scrolling, then the generated report.
7. End on the report: that is the artifact the project produces.

Keep it under two minutes, show the clock, and do not narrate the charts — let
the numbers move on their own.

## 10. Honest limitations

- Single-node Compose; no multi-host or region failure domain.
- Injection is application-level, so no true network partitions or clock skew.
- Orchestrator control state (active-injection list, incident state, chart ring)
  is in-memory and does not survive its own restart; broker flags keep working
  until their TTL, so the UI can briefly under-report reality.
- Detection uses fixed thresholds with a cooldown, not adaptive or statistical
  baselines.
- Stock reservation is not idempotent, and a failed insert after a successful
  reservation leaves stock held — deliberate, documented MVP simplifications.
- The dashboard sends no chaos token, so enabling `REQUIRE_CHAOS_TOKEN=true`
  makes the UI read-only and injections must be driven from `curl`.

## 11. Where it goes next

Phase 5 (public deploy on Fly.io/Koyeb + Vercel, token-gated and rate-limited
injection), then: chaos-lab scenarios with expected-vs-observed grading,
per-incident metric snapshots, real network-level fault injection, and adaptive
detection. All listed in [`PRD.md`](PRD.md) §6.2 as explicitly out of MVP scope.

---

### Document map

| Doc | Audience |
|---|---|
| [`../README.md`](../README.md) | first impression, quick start |
| [`SETUP.md`](SETUP.md) | anyone installing it, plus the verification record |
| [`USER_GUIDE.md`](USER_GUIDE.md) | non-technical walkthrough of concepts |
| [`TECHNICAL_GUIDE.md`](TECHNICAL_GUIDE.md) | plain-English internals + runbook + glossary |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | design intent and failure-propagation model |
| [`API_SPEC.md`](API_SPEC.md) | the endpoint contract |
| [`PRD.md`](PRD.md) | scope, phases, acceptance criteria |
