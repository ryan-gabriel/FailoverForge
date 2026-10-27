"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ORCHESTRATOR_URL, getIncidents } from "../../lib/api";
import type { IncidentListItem, WSMessage } from "../../lib/types";
import { subscribe } from "../../lib/ws";

const STATUS_STYLES: Record<string, string> = {
  open: "border-red-800 bg-red-950/40 text-red-300",
  closed: "border-emerald-800 bg-emerald-950/40 text-emerald-300",
};

export default function IncidentsPage() {
  const [incidents, setIncidents] = useState<IncidentListItem[]>([]);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setIncidents(await getIncidents());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void refresh();
    const wsUrl = ORCHESTRATOR_URL.replace(/^http/, "ws") + "/ws";
    return subscribe(wsUrl, {
      onConnectionChange: () => {},
      onReconnect: () => void refresh(),
      onMessage: (msg: WSMessage) => {
        // A status flip (open/close) or a brand-new opened event changes the list.
        if (msg.type === "incident.status" || msg.type === "timeline.event") {
          void refresh();
        }
      },
    });
  }, [refresh]);

  return (
    <div className="mx-auto max-w-4xl px-6 py-10">
      <header className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-100">Incidents</h1>
          <p className="text-sm text-slate-500">
            Chronological incident record, sourced from the event store.
          </p>
        </div>
        <Link href="/" className="text-sm text-sky-400 hover:text-sky-300">
          ← Dashboard
        </Link>
      </header>

      {error && (
        <div className="mb-4 rounded-lg border border-amber-800 bg-amber-950/30 px-3 py-2 font-mono text-xs text-amber-300">
          {error}
        </div>
      )}

      <div className="space-y-2">
        {incidents.length === 0 && !error && (
          <div className="rounded-xl border border-slate-800 bg-slate-900/60 px-4 py-8 text-center text-sm text-slate-600">
            No incidents yet. Trigger a failure from the dashboard to open one.
          </div>
        )}
        {incidents.map((incident) => (
          <Link
            key={incident.incident_id}
            href={`/incidents/${incident.incident_id}`}
            className="flex items-center gap-4 rounded-xl border border-slate-800 bg-slate-900/60 px-4 py-3 transition-colors hover:border-slate-700"
          >
            <span
              className={`rounded-full border px-2 py-0.5 font-mono text-[11px] ${STATUS_STYLES[incident.status] ?? "border-slate-700 text-slate-400"}`}
            >
              {incident.status}
            </span>
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-medium text-slate-200">
                {incident.title ?? incident.incident_id.slice(0, 8)}
              </div>
              <div className="font-mono text-[11px] text-slate-500">
                {incident.opened_at ?? "—"} · {incident.event_count} events
              </div>
            </div>
            <span className="font-mono text-[11px] text-slate-600">
              {incident.incident_id.slice(0, 8)}
            </span>
          </Link>
        ))}
      </div>
    </div>
  );
}
