-- Canonical schema per docs/API_SPEC.md §6.
-- orders (SUT)
CREATE TABLE IF NOT EXISTS orders (
    order_id    UUID PRIMARY KEY,
    sku         TEXT NOT NULL,
    qty         INT NOT NULL,
    customer_ref TEXT,
    status      TEXT NOT NULL DEFAULT 'pending',  -- pending | fulfilled | failed
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    fulfilled_at TIMESTAMPTZ
);

-- incident event store (orchestrator) — APPEND ONLY
CREATE TABLE IF NOT EXISTS incident_events (
    id          BIGSERIAL PRIMARY KEY,
    incident_id UUID NOT NULL,
    seq         BIGINT NOT NULL,
    event_type  TEXT NOT NULL,
    payload     JSONB NOT NULL DEFAULT '{}',
    ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (incident_id, seq)
);

-- optional projection (never source of truth)
CREATE TABLE IF NOT EXISTS incidents (
    incident_id UUID PRIMARY KEY,
    title       TEXT,
    status      TEXT NOT NULL DEFAULT 'open',
    opened_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at   TIMESTAMPTZ
);