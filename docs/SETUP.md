# Failover Forge — Setup Runbook

A step-by-step, copy-paste guide to get the whole system running from a clean
machine, with a verification checkpoint after every stage. Follow it top to
bottom; each step tells you exactly what you should see. If a step fails, jump to
[Troubleshooting](#8-troubleshooting).

> **Related docs:** [`README.md`](../README.md) (pitch) · [`USER_GUIDE.md`](USER_GUIDE.md)
> (concept walkthrough) · [`TECHNICAL_GUIDE.md`](TECHNICAL_GUIDE.md) (how it works
> inside) · [`API_SPEC.md`](API_SPEC.md) (endpoint contract) ·
> [`RECRUITER.md`](RECRUITER.md) (recruiter-facing case study)

---

## 1. What you are setting up

Eight containers, started by one command:

| # | Container | Your machine's address | Role |
|---|---|---|---|
| 1 | `frontend` | http://localhost:3000 | The dashboard |
| 2 | `orchestrator` | http://localhost:8000 | Control plane: load, injection, metrics, events, reports |
| 3 | `checkout-api` | http://localhost:8001 | Takes orders |
| 4 | `inventory-api` | http://localhost:8002 | Stock + reservations |
| 5 | `fulfillment-worker` | http://localhost:8003 | Consumes the queue, fulfills orders |
| 6 | `postgres` | internal (5432) | Orders + append-only incident events |
| 7 | `redis` | internal (6379) | Order cache + injection flags |
| 8 | `rabbitmq` | mgmt UI http://localhost:15672 | `fulfillment` queue + `fulfillment.dlq` |

Nothing talks to the internet. There are no paid keys and no cloud accounts.

---

## 2. Prerequisites

- **Docker Desktop** (Windows/macOS) or Docker Engine (Linux), with **Compose v2**
  (`docker compose version` must print something).
- **At least 2 GB free memory** for the containers.
- Free ports: `3000, 8000, 8001, 8002, 8003, 15672`.
- `git`, and `curl` for the smoke test.
- Optional, only for code checks on your machine: `uv` (Python) and Node.js 20+
  (`make test`, `make lint`, `make frontend`). Running the app itself needs only
  Docker.

### Windows + WSL note (this repo's actual dev setup)

Run everything from inside the WSL distro where the repo lives. Docker Desktop
must have **WSL integration enabled** for that distro
(Settings → Resources → WSL integration → tick your distro). Verify before you
start:

```bash
docker version --format '{{.Server.Version}}'
```

If this prints `command not found` or
`failed to connect to the docker API at npipe:...`, integration is off — see
[Troubleshooting](#8-troubleshooting) first, because nothing downstream will work.

**Checkpoint 1 — done when:** `docker version` shows a Server version.

---

## 3. Get the code and configure the environment

```bash
git clone <your-repo-url> FailoverForge
cd FailoverForge
cp .env.example .env
```

`.env` is the only required configuration. Defaults are fine for local use. The
variables that matter:

| Variable | Default | Why you would change it |
|---|---|---|
| `RABBITMQ_USER` / `RABBITMQ_PASSWORD` | `forge` / `forge` | Broker login. RabbitMQ restricts `guest` to loopback, so container-to-container traffic needs this non-loopback user. Changing it means re-creating the broker (see troubleshooting). |
| `RABBITMQ_URL` / `RABBITMQ_MGMT_URL` | `amqp://forge:forge@rabbitmq:5672/` · `http://forge:forge@rabbitmq:15672` | Must match the two variables above. |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | `forge` ×3 | Changing them after the first boot requires `docker compose down -v` (the `pgdata` volume keeps the old values). |
| `CHAOS_TOKEN` | `dev-token` | Shared secret for injection endpoints. |
| `REQUIRE_CHAOS_TOKEN` | `false` | Set `true` only for a live/public deploy. The dashboard does not send a token, so the UI becomes read-only when it is on. |
| `INJECTION_TTL_DEFAULT_S` | `300` | How long an injected failure self-expires. |
| `NEXT_PUBLIC_ORCHESTRATOR_URL` | `http://localhost:8000` | The **browser's** path to the orchestrator. Change only if the dashboard runs elsewhere. |

**Checkpoint 2 — done when:** `test -f .env && echo ok` prints `ok`, and
`grep RABBITMQ_USER .env` shows a value.

---

## 4. Build and start the stack

```bash
docker compose up --build -d
```

First build pulls base images and installs dependencies: expect **1–3 minutes**.
Later runs reuse the cache and take seconds.

Then watch it come up. Compose starts the data stores first, waits for each
healthcheck, and only then starts the services that depend on them (checkout
waits for Postgres, Redis, RabbitMQ *and* inventory; the orchestrator waits for
all five):

```bash
docker compose ps
```

**Checkpoint 3 — done when:** all eight services are listed with state
`running (healthy)`. The frontend needs ~40 seconds before it turns healthy
(`start_period`), so re-run `docker compose ps` after a minute if it still says
`starting`.

```bash
docker compose logs -f --tail=30       # Ctrl-C to stop following
```

Expected lines to see in that stream:

- `checkout-api` / `fulfillment-worker`: `consumer started`
- `orchestrator`: uvicorn listening on `0.0.0.0:8000`
- **no** `amqp connect failed` lines. The broker healthcheck now actively probes
  port connectivity (`check_port_connectivity`), so AMQP is accepting sockets
  before dependents start. If you *do* see a short burst of
  `amqp connect failed … Connection refused` that then stops, the services
  retried and recovered — that is expected but should not persist. Anything that
  keeps failing after ~30s is a real problem (usually credentials).

---

## 5. Verify each layer

Run these one at a time. Every command should succeed.

```bash
# 1. SUT services alive
curl -s localhost:8001/health   # {"status":"ok"}
curl -s localhost:8002/health   # {"status":"ok"}
curl -s localhost:8003/health   # {"status":"ok","consumers":1,"paused":false}

# 2. Control plane alive
curl -s localhost:8000/health
curl -s localhost:8000/api/topology | python3 -m json.tool | head   # 6 nodes, 6 edges

# 3. Metrics pipeline running (1-second poll)
curl -s localhost:8000/api/metrics/latest | python3 -m json.tool | head -20

# 4. Broker reachable + authenticated (queue panel works)
curl -s localhost:8000/api/queue | python3 -m json.tool
#    -> {"depth":0,"consumers":1,"dlq_depth":0,"dlq_samples":[]}
docker compose exec rabbitmq rabbitmqctl list_users
#    -> a list containing "forge" with tag [administrator]

# 5. Database schema created
docker compose exec postgres psql -U forge -d forge -c "\dt"
#    -> orders, incident_events, incidents

# 6. Cache + flags store responding
docker compose exec redis redis-cli ping   # PONG

# 7. Management UI
open http://localhost:15672                # log in as forge / forge
```

**Checkpoint 4 — done when:** all seven commands above return, `/api/queue` gives
you a `depth` and `consumers` number (proof the orchestrator's broker credentials
work), and `list_users` shows `forge`.

> If `/api/queue` returns `503 rabbitmq management API unavailable`, the broker
> credentials do not match — go to [Troubleshooting](#8-troubleshooting) before
> continuing. Everything queue-related depends on it.

---

## 6. Seed stock and prove the pipeline end-to-end

```bash
./scripts/seed.sh      # or: make seed
./scripts/smoke.sh     # or: make smoke
```

`seed.sh` resets inventory stock and prints it. `smoke.sh` waits for health,
creates a real order, and polls until the worker fulfills it — that single script
proves the whole path: load path → HTTP reservation → Postgres insert → RabbitMQ
publish → worker consume → Postgres update → cached read.

```
waiting for inventory-api ... 
waiting for checkout-api ...
waiting for fulfillment-worker ...
seeding stock ...
creating order ...
order_id=5f0c…
  status=pending
  status=fulfilled
SMOKE OK
```

One more manual pass so you have seen the cache work:

```bash
curl -s -X POST localhost:8001/orders \
  -H 'content-type: application/json' \
  -d '{"sku":"SKU-1001","qty":1,"customer_ref":"setup-check"}' | python3 -m json.tool

curl -si localhost:8001/orders/<order_id> | grep -i x-cache
# first read:  X-Cache: miss      (read from Postgres, written into Redis)
# second read: X-Cache: hit       (served from Redis)
```

**Checkpoint 5 — done when:** `smoke.sh` prints `SMOKE OK` and you have seen both
`X-Cache: miss` and `X-Cache: hit`.

---

## 7. Prove the chaos layer (optional but recommended)

Start the dashboard and drive one failure through it, so you know the injection
path works too:

```bash
open http://localhost:3000
```

1. **Start load** in the Trigger panel (10 RPS, 30 s, `mixed`).
2. **Inject latency** on `inventory-api`, `2000` ms.
3. Expect within ~5 seconds: p95 chart jumps, `inventory-api` and `checkout-api`
   tiles turn `degraded`, the queue/orders error rate rises, and an incident opens
   with a `FAILURE_INJECTED` event on the timeline.
4. **Clear all** → nodes return to `healthy`, incident closes.
5. Open **Incidents → the incident** → report shows root cause, blast radius,
   timeline, MTTR.

Same thing from the terminal, if you prefer evidence in text form:

```bash
curl -s -X POST localhost:8000/api/load -H 'content-type: application/json' \
  -d '{"profile":"mixed","rps":10,"duration_s":30}'
curl -s -X POST localhost:8000/api/injections -H 'content-type: application/json' \
  -d '{"service":"inventory-api","failure_type":"latency","intensity":2000,"ttl_s":120}'
sleep 15 && curl -s localhost:8000/api/metrics/latest | python3 -m json.tool | grep -A3 p95_ms
curl -s localhost:8000/api/incidents | python3 -m json.tool
curl -s -X DELETE localhost:8000/api/injections
```

**Checkpoint 6 — done when:** you can show a p95 spike, a `degraded` node, and a
generated report.

---

## 8. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `docker: command not found` in WSL, or `failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine` | Docker Desktop not running, or WSL integration off for this distro | Start Docker Desktop, then Settings → Resources → WSL integration → enable your distro. Confirm with `docker version`. |
| `Bind for 0.0.0.0:3000 failed: port is already allocated` (or 8000/8001/…) | Another app holds the port | `docker compose ps` to find an old stack, or change the host side: `"3001:3000"`. On Windows: `netstat -ano \| findstr :3000` then kill the PID. |
| `orders stay pending`, `amqp connect failed (attempt n/15)` in logs | Broker credentials mismatch (most common), or the broker is not up yet | Check `docker compose ps rabbitmq` is healthy, then `docker compose exec rabbitmq rabbitmqctl list_users`. `RABBITMQ_DEFAULT_USER` only applies when the broker's data directory is **new** — reset with `docker compose down -v && docker compose up -d`. |
| Broker log: `PLAIN login refused: user 'guest' can only connect via localhost` | An old `.env` (or an old image default) still uses `guest` | `grep -n guest .env` should only show the comment line. Set `RABBITMQ_USER`/`RABBITMQ_PASSWORD`/`RABBITMQ_URL`/`RABBITMQ_MGMT_URL` to the `forge` values in `.env.example`, then `docker compose down -v`. |
| `queue` panel shows `–`, `/api/queue` → `503 rabbitmq management API unavailable` | Management API rejected the credentials in `RABBITMQ_MGMT_URL` | Make the user/password in that URL match the broker user; confirm with `curl -s -u forge:forge localhost:15672/api/overview`. |
| `Waiting for orchestrator topology…` on the dashboard | Orchestrator not healthy or browser cannot reach port 8000 | `docker compose logs --tail=50 orchestrator`; if it crash-looped, `docker compose up -d orchestrator`. |
| `/api/metrics/latest` → `503 metrics not ready yet` forever | The 1-second poll cannot reach any service | Check the three `/health` URLs and `NEXT_PUBLIC_ORCHESTRATOR_URL` / `*_API_URL` values. |
| `make smoke` fails at "not healthy after 90s" | Cold build still in progress, or a dependency failed | `docker compose ps` + `docker compose logs <service>`; re-run after the stack is healthy. |
| Orders/incidents missing after a restart | Expected — this is *not* a bug | Metrics history and active-injection lists are in-memory by design. Postgres data survives unless you ran `docker compose down -v` (that flag deletes the `pgdata` volume). |
| Frontend shows a blank page or stale UI | Dev-server cache | `docker compose restart frontend`, or `docker compose build --no-cache frontend && docker compose up -d frontend`. |
| `uv: command not found` / `npm: command not found` | Toolchain for code checks not installed | Not needed to run the app. Install `uv` for `make test`/`lint`, Node 20+ for `make frontend`. |

**Reset everything to a clean state:**

```bash
docker compose down -v        # deletes containers, networks, and the pgdata volume
docker compose up --build -d
./scripts/seed.sh && ./scripts/smoke.sh
```

---

## 9. Daily commands

```bash
make up          # docker compose up --build -d
make ps          # container status
make logs        # follow all logs
make smoke       # end-to-end order → fulfilled
make seed        # reset demo stock
make test        # pytest across the four Python services
make lint        # ruff check + format check + frontend eslint/tsc
make typecheck   # mypy on the orchestrator
make fmt         # ruff format + --fix
make frontend    # npm install + lint + typecheck + build
make verify      # lint + typecheck + test + frontend (full gate)
make down        # docker compose down -v
```

---

## 10. Verification record

This is the run log from the most recent full verification. It doubles as the
evidence trail for a reviewer: a runbook with real numbers is far more convincing
than a checklist with only ticks. Keep it current — update it whenever you re-run
the stack on a fresh clone.

| # | Stage | Command | Expected | Result / date | Notes |
|---|---|---|---|---|---|
| 1 | Docker reachable | `docker version` | Server version printed | ✅ 2026-10-26 — server 29.5.2, Compose v5.1.4 | WSL + Docker Desktop |
| 2 | Env configured | `cp .env.example .env` | `.env` exists, `RABBITMQ_USER` set | ✅ `forge` | |
| 3 | Stack up | `docker compose up --build -d && docker compose ps` | 8 services, all healthy | ✅ 8/8 healthy in ~25 s | cold build ~1 min (cached layers) |
| 4 | SUT health | `curl -s localhost:800{1,2,3}/health` | `status: ok` ×3, worker `consumers: 1` | ✅ worker `{"status":"ok","consumers":1,"paused":false}` | |
| 5 | Control plane | `curl -s localhost:8000/api/topology` | 6 nodes, 6 edges | ✅ 6 / 6 | |
| 6 | Metrics poll | `curl -s localhost:8000/api/metrics/latest` | tick with `services` + `queue` | ✅ ticking at 1 s | |
| 7 | Broker auth | `curl -s localhost:8000/api/queue` | `depth`, `consumers`, `dlq_depth` numbers | ✅ `{"depth":0,"consumers":1,"dlq_depth":0,"dlq_samples":[]}` | proves management-API credentials |
| 8 | Broker user | `docker compose exec rabbitmq rabbitmqctl list_users` | `forge` + `[administrator]` | ✅ `forge [administrator]` | the non-loopback fix |
| 9 | Schema | `psql -c "\dt"` | `orders`, `incident_events`, `incidents` | ✅ all 3 present | |
| 10 | Seed | `./scripts/seed.sh` | `SEED OK` + stock map | ✅ `SEED OK` | |
| 11 | End-to-end | `./scripts/smoke.sh` | `SMOKE OK` | ✅ fulfilled on first poll | publish → consume → DB |
| 12 | Cache proof | `curl -si /orders/{id} \| grep -i x-cache` | `miss` then `hit` | ✅ `miss` → `hit` | |
| 13 | Chaos proof | inject 2000 ms latency on `inventory-api` | p95 spike, `degraded` nodes, incident + report | ✅ p95 25 → 2500 ms on inventory **and** checkout; both `degraded`; report: root cause `latency/inventory-api`, blast radius `[inventory-api, checkout-api]`, MTTR 16.1 s | cascade crossed the sync boundary |
| 14 | Queue proof | inject `queue_saturation` (200 msgs) then clear | depth rises, `consumers: 0`, then drains | ✅ depth 19 with `consumers: 0` while paused → `depth: 0, consumers: 1` after clear | |
| 15 | DLQ proof | publish an unparseable body to `fulfillment` | `dlq_depth: 1` + a sample with a reason | ✅ `{"dlq_depth":1,"dlq_samples":[{"reason":"unparseable"}]}` | worker counter `messages_dead_lettered: 1` |
| 16 | Tests | `make test` | all four services pass | ✅ 16 + 16 + 16 + 73 = 121 passed | |
| 17 | Lint/types | `make lint && make typecheck` | clean | ✅ ruff clean (54 files), mypy 18 files no issues | |
| 18 | Frontend gate | `npm run lint && npm run typecheck && npm run build` | clean | ✅ all three clean | `make frontend` |
| 19 | Cold-boot logs | `docker compose logs checkout-api fulfillment-worker orchestrator \| grep -c "amqp connect failed"` | 0 | ✅ 0 | broker healthcheck probes port connectivity |

> This record was produced by following the runbook above on a single machine
> after `docker compose down -v` (fresh Postgres + fresh broker). Re-run it and
> overwrite the cells rather than adding a second table — a record should always
> describe the state of the code you are shipping.

Screenshots worth capturing for step 13 (they are what a reviewer remembers):

1. Healthy dashboard: green graph, flat p95.
2. Mid-incident: `degraded` nodes, highlighted cascade edge, p95 spike, error
   rate rising, active-injection chips.
3. Queue saturation: queue-depth climb and orders stuck `pending`.
4. Incident timeline with the immutable event list.
5. Generated report: root cause, blast radius, MTTR.
