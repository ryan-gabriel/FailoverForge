import type {
  IncidentDetail,
  IncidentListItem,
  IncidentReport,
  Injection,
  MetricsTick,
  QueueSnapshot,
  Topology,
} from "./types";

export const ORCHESTRATOR_URL =
  process.env.NEXT_PUBLIC_ORCHESTRATOR_URL ?? "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${ORCHESTRATOR_URL}${path}`, {
    cache: "no-store",
    ...init,
    headers: { "content-type": "application/json", ...init?.headers },
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => res.statusText);
    throw new Error(`${init?.method ?? "GET"} ${path} → ${res.status}: ${detail}`);
  }
  return (await res.json()) as T;
}

export function getLatestMetrics(): Promise<MetricsTick> {
  return request<MetricsTick>("/api/metrics/latest");
}

export function getInjections(): Promise<Injection[]> {
  return request<Injection[]>("/api/injections");
}

export function getTopology(): Promise<Topology> {
  return request<Topology>("/api/topology");
}

export function inject(service: string, failure_type: string, intensity: number): Promise<Injection> {
  return request<Injection>("/api/injections", {
    method: "POST",
    body: JSON.stringify({ service, failure_type, intensity }),
  });
}

export function clearInjection(injection_id: string): Promise<Injection> {
  return request<Injection>(`/api/injections/${injection_id}`, { method: "DELETE" });
}

export function clearAllInjections(): Promise<{ cleared: number }> {
  return request<{ cleared: number }>("/api/injections", { method: "DELETE" });
}

export function getIncidents(limit = 50): Promise<IncidentListItem[]> {
  return request<IncidentListItem[]>(`/api/incidents?limit=${limit}`);
}

export function getIncident(incidentId: string): Promise<IncidentDetail> {
  return request<IncidentDetail>(`/api/incidents/${incidentId}`);
}

export function getIncidentReport(incidentId: string): Promise<IncidentReport> {
  return request<IncidentReport>(`/api/incidents/${incidentId}/report`);
}

export function getQueue(): Promise<QueueSnapshot> {
  return request<QueueSnapshot>("/api/queue");
}
