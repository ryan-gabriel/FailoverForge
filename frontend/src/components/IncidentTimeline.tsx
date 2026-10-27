"use client";

import { useState } from "react";
import type { StoreEvent } from "../lib/types";

const EVENT_STYLES: Record<string, string> = {
  INCIDENT_OPENED: "border-red-700 text-red-300",
  FAILURE_INJECTED: "border-orange-700 text-orange-300",
  SYMPTOM_DETECTED: "border-amber-700 text-amber-300",
  MITIGATION_APPLIED: "border-emerald-700 text-emerald-300",
  INCIDENT_CLOSED: "border-sky-700 text-sky-300",
  LOAD_STARTED: "border-slate-600 text-slate-400",
  LOAD_STOPPED: "border-slate-600 text-slate-400",
};

export default function IncidentTimeline({ events }: { events: StoreEvent[] }) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  const toggle = (seq: number) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(seq)) next.delete(seq);
      else next.add(seq);
      return next;
    });
  };

  if (events.length === 0) {
    return <div className="text-sm text-slate-600">No events recorded.</div>;
  }

  return (
    <ol className="space-y-1">
      {events.map((event) => {
        const isOpen = expanded.has(event.seq);
        return (
          <li key={event.id} className="rounded-lg border border-slate-800 bg-slate-950/60">
            <button
              type="button"
              onClick={() => toggle(event.seq)}
              className="flex w-full items-center gap-3 px-3 py-2 text-left"
            >
              <span className="font-mono text-xs text-slate-600">#{event.seq}</span>
              <span
                className={`rounded border px-1.5 py-0.5 font-mono text-[11px] ${EVENT_STYLES[event.event_type] ?? "border-slate-700 text-slate-400"}`}
              >
                {event.event_type}
              </span>
              <span className="ml-auto font-mono text-[11px] text-slate-500">
                {event.ts.slice(11, 23)}
              </span>
              <span className="w-4 text-right text-slate-600">{isOpen ? "−" : "+"}</span>
            </button>
            {isOpen && (
              <pre className="overflow-x-auto border-t border-slate-800 px-3 py-2 font-mono text-[11px] leading-relaxed text-slate-400">
                {JSON.stringify(event.payload, null, 2)}
              </pre>
            )}
          </li>
        );
      })}
    </ol>
  );
}
