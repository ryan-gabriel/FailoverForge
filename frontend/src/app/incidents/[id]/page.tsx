"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { ORCHESTRATOR_URL, getIncident, getIncidentReport } from "../../../lib/api";
import type {
  IncidentDetail,
  IncidentReport,
  WSMessage,
} from "../../../lib/types";
import { subscribe } from "../../../lib/ws";
import IncidentTimeline from "../../../components/IncidentTimeline";
import ReportView from "../../../components/ReportView";

export default function IncidentDetailPage() {
  const params = useParams<{ id: string }>();
  const incidentId = params.id;
  const [detail, setDetail] = useState<IncidentDetail | null>(null);
  const [report, setReport] = useState<IncidentReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [incident, incidentReport] = await Promise.all([
        getIncident(incidentId),
        getIncidentReport(incidentId),
      ]);
      setDetail(incident);
      setReport(incidentReport);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [incidentId]);

  useEffect(() => {
    void refresh();
    const wsUrl = ORCHESTRATOR_URL.replace(/^http/, "ws") + "/ws";
    return subscribe(wsUrl, {
      onConnectionChange: () => {},
      onReconnect: () => void refresh(),
      onMessage: (msg: WSMessage) => {
        if (msg.type === "timeline.event" && msg.data.incident_id === incidentId) {
          setDetail((prev) =>
            prev ? { ...prev, events: [...prev.events, msg.data] } : prev,
          );
          if (msg.data.event_type === "INCIDENT_CLOSED") void refresh();
        } else if (
          msg.type === "incident.status" &&
          msg.data.incident_id === incidentId
        ) {
          void refresh();
        }
      },
    });
  }, [incidentId, refresh]);

  if (error) {
    return (
      <div className="mx-auto max-w-4xl px-6 py-10">
        <div className="rounded-lg border border-amber-800 bg-amber-950/30 px-3 py-2 font-mono text-xs text-amber-300">
          {error}
        </div>
        <Link
          href="/incidents"
          className="mt-4 inline-block text-sm text-sky-400 hover:text-sky-300"
        >
          ← All incidents
        </Link>
      </div>
    );
  }

  if (!detail) {
    return <div className="px-6 py-10 text-sm text-slate-600">Loading…</div>;
  }

  return (
    <div className="mx-auto max-w-4xl px-6 py-10">
      <header className="mb-6 flex items-start justify-between gap-4">
        <div>
          <div className="mb-1 flex items-center gap-2">
            <span
              className={`rounded-full border px-2 py-0.5 font-mono text-[11px] ${
                detail.status === "open"
                  ? "border-red-800 bg-red-950/40 text-red-300"
                  : "border-emerald-800 bg-emerald-950/40 text-emerald-300"
              }`}
            >
              {detail.status}
            </span>
            <span className="font-mono text-[11px] text-slate-600">{detail.incident_id}</span>
          </div>
          <h1 className="text-2xl font-bold text-slate-100">
            {detail.title ?? `incident ${detail.incident_id.slice(0, 8)}`}
          </h1>
          <div className="mt-1 font-mono text-xs text-slate-500">
            opened {detail.opened_at ?? "—"} · closed {detail.closed_at ?? "— (still open)"}
            {detail.mttr_s != null && <> · mttr {detail.mttr_s}s</>}
          </div>
        </div>
        <Link href="/incidents" className="shrink-0 text-sm text-sky-400 hover:text-sky-300">
          ← Incidents
        </Link>
      </header>

      <section className="mb-8">
        <h2 className="mb-3 text-sm font-medium uppercase tracking-wide text-slate-500">
          Report
        </h2>
        {report ? (
          <ReportView report={report} />
        ) : (
          <div className="text-sm text-slate-600">Generating…</div>
        )}
      </section>

      <section>
        <h2 className="mb-3 text-sm font-medium uppercase tracking-wide text-slate-500">
          Timeline
        </h2>
        <IncidentTimeline events={detail.events} />
      </section>
    </div>
  );
}
