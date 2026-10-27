"use client";

import { useEffect, useState } from "react";
import { getQueue } from "../lib/api";
import type { QueueSnapshot } from "../lib/types";

/** Queue + DLQ panel (PRD §6.1 "DLQ inspection view"): depth, consumers,
 *  DLQ count, and sample dead-lettered messages. Polls every 5s. */
export default function QueuePanel() {
  const [queue, setQueue] = useState<QueueSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      try {
        const snapshot = await getQueue();
        if (!cancelled) {
          setQueue(snapshot);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      }
    };
    void poll();
    const timer = setInterval(() => void poll(), 5000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
      <h3 className="mb-2 text-sm font-medium text-slate-300">Queue &amp; DLQ</h3>
      {error && <div className="mb-2 font-mono text-[11px] text-amber-400">{error}</div>}
      <div className="grid grid-cols-3 gap-2 text-center">
        <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-2">
          <div className="font-mono text-lg text-slate-100">{queue?.depth ?? "–"}</div>
          <div className="text-[10px] uppercase tracking-wide text-slate-500">depth</div>
        </div>
        <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-2">
          <div className="font-mono text-lg text-slate-100">{queue?.consumers ?? "–"}</div>
          <div className="text-[10px] uppercase tracking-wide text-slate-500">consumers</div>
        </div>
        <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-2">
          <div
            className={`font-mono text-lg ${(queue?.dlq_depth ?? 0) > 0 ? "text-red-400" : "text-slate-100"}`}
          >
            {queue?.dlq_depth ?? "–"}
          </div>
          <div className="text-[10px] uppercase tracking-wide text-slate-500">dlq</div>
        </div>
      </div>
      {(queue?.dlq_samples.length ?? 0) > 0 && (
        <div className="mt-3 border-t border-slate-800 pt-2">
          <div className="mb-1 text-[10px] uppercase tracking-wide text-slate-500">
            DLQ samples
          </div>
          <ul className="space-y-1">
            {queue!.dlq_samples.map((sample, i) => (
              <li key={i} className="truncate font-mono text-[11px] text-slate-400">
                <span className="text-red-300">{sample.reason}</span>
                {sample.order_id && <span className="text-slate-500"> · {sample.order_id}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
