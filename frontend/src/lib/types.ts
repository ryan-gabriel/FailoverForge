// Payload types from docs/API_SPEC.md — the single source of truth.

export type ServiceMetrics = {
  rps: number;
  error_rate: number;
  p95_ms: number | null;
  in_flight: number;
};

export type MetricsTick = {
  ts: string;
  services: Record<string, ServiceMetrics>;
  cache: { hit_rate: number; hits: number; misses: number };
  queue: { depth: number | null; consumers: number | null; dlq_depth: number | null };
  node_states: Record<string, string>;
};

export type Injection = {
  injection_id: string;
  service: string;
  failure_type: string;
  intensity: number;
  ttl_s: number;
  created_at: string;
  active: boolean;
};

export type TopologyNode = { id: string; kind: string; health: string };
export type TopologyEdge = { from: string; to: string; kind: string };
export type Topology = { nodes: TopologyNode[]; edges: TopologyEdge[] };

export type IncidentStatus = {
  incident_id: string;
  status: "open" | "closed";
  opened_at: string | null;
  closed_at: string | null;
};

export type TimelineEvent = {
  incident_id: string;
  id: number;
  seq: number;
  event_type: string;
  ts: string;
  payload: Record<string, unknown>;
};

export type WSMessage =
  | { type: "metrics.tick"; ts: string; data: MetricsTick }
  | { type: "queue.update"; ts: string; data: { depth: number | null; dlq_depth: number | null } }
  | { type: "injection.status"; ts: string; data: Injection[] }
  | { type: "incident.status"; ts: string; data: IncidentStatus }
  | { type: "timeline.event"; ts: string; data: TimelineEvent };

export type FailureType = "latency" | "error" | "crash" | "queue_saturation" | "cache_outage";

export type IncidentListItem = {
  incident_id: string;
  status: "open" | "closed";
  opened_at: string | null;
  closed_at: string | null;
  title: string | null;
  event_count: number;
};

export type StoreEvent = {
  id: number;
  seq: number;
  event_type: string;
  ts: string;
  payload: Record<string, unknown>;
};

export type IncidentDetail = {
  incident_id: string;
  title: string | null;
  status: "open" | "closed";
  opened_at: string | null;
  closed_at: string | null;
  mttr_s: number | null;
  events: StoreEvent[];
};

export type IncidentReport = {
  incident_id: string;
  title: string;
  root_cause: { service: string; failure_type: string; intensity: number } | null;
  blast_radius: string[];
  mttr_s: number | null;
  timeline_summary: string[];
  markdown: string;
};

export type QueueSnapshot = {
  depth: number | null;
  consumers: number | null;
  dlq_depth: number | null;
  dlq_samples: { order_id: string | null; reason: string | null }[];
};
