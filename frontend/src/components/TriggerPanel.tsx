"use client";

import { useState } from "react";
import type { Injection } from "../lib/types";

/**
 * Trigger panel: per-service failure buttons (API_SPEC §2.3). No
 * confirmation — this is a demo tool (AGENTS Phase 2). Worker only offers
 * failures that actually act: latency/error flags are inert on the worker
 * (no per-request hot path), queue_saturation pauses the consumer.
 */

type Props = {
  active: Injection[];
  busy: boolean;
  onInject: (service: string, failureType: string, intensity: number) => Promise<void>;
  onClear: (injectionId: string) => Promise<void>;
  onClearAll: () => Promise<void>;
};

const SERVICE_FAILURES: Record<string, string[]> = {
  "checkout-api": ["latency", "error", "crash", "cache_outage"],
  "inventory-api": ["latency", "error", "crash"],
  "fulfillment-worker": ["queue_saturation", "crash"],
};

const FAILURE_LABELS: Record<string, string> = {
  latency: "Latency",
  error: "Errors",
  crash: "Crash",
  cache_outage: "Cache outage",
  queue_saturation: "Flood queue",
};

const FAILURE_STYLES: Record<string, string> = {
  latency: "border-sky-700 text-sky-300 hover:bg-sky-950",
  error: "border-amber-700 text-amber-300 hover:bg-amber-950",
  crash: "border-red-700 text-red-300 hover:bg-red-950",
  cache_outage: "border-fuchsia-700 text-fuchsia-300 hover:bg-fuchsia-950",
  queue_saturation: "border-orange-700 text-orange-300 hover:bg-orange-950",
};

export default function TriggerPanel({ active, busy, onInject, onClear, onClearAll }: Props) {
  const [latencyMs, setLatencyMs] = useState(400);
  const [errorRatio, setErrorRatio] = useState(0.5);
  const [floodCount, setFloodCount] = useState(100);

  const intensityFor = (failureType: string): number => {
    if (failureType === "latency") return latencyMs;
    if (failureType === "error") return errorRatio;
    if (failureType === "queue_saturation") return floodCount;
    return 0;
  };

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-sm font-medium text-slate-300">Trigger failures</h3>
        <button
          type="button"
          onClick={() => void onClearAll()}
          disabled={busy || active.length === 0}
          className="rounded-md border border-slate-700 px-2 py-1 text-xs text-slate-300 transition hover:border-emerald-600 hover:text-emerald-300 disabled:opacity-40"
        >
          Clear all
        </button>
      </div>

      <div className="mb-3 grid grid-cols-3 gap-2 text-xs">
        <label className="flex flex-col gap-1 text-slate-500">
          latency ms
          <input
            type="number"
            min={1}
            max={10000}
            value={latencyMs}
            onChange={(e) => setLatencyMs(Number(e.target.value))}
            className="rounded-md border border-slate-700 bg-slate-950 px-2 py-1 text-slate-200"
          />
        </label>
        <label className="flex flex-col gap-1 text-slate-500">
          error ratio
          <input
            type="number"
            min={0}
            max={1}
            step={0.1}
            value={errorRatio}
            onChange={(e) => setErrorRatio(Number(e.target.value))}
            className="rounded-md border border-slate-700 bg-slate-950 px-2 py-1 text-slate-200"
          />
        </label>
        <label className="flex flex-col gap-1 text-slate-500">
          flood msgs
          <input
            type="number"
            min={1}
            max={5000}
            value={floodCount}
            onChange={(e) => setFloodCount(Number(e.target.value))}
            className="rounded-md border border-slate-700 bg-slate-950 px-2 py-1 text-slate-200"
          />
        </label>
      </div>

      <div className="grid gap-3">
        {Object.entries(SERVICE_FAILURES).map(([service, failures]) => (
          <div key={service} className="rounded-lg border border-slate-800 bg-slate-950/60 p-3">
            <div className="mb-2 font-mono text-xs text-slate-400">{service}</div>
            <div className="flex flex-wrap gap-2">
              {failures.map((failureType) => (
                <button
                  key={failureType}
                  type="button"
                  disabled={busy}
                  onClick={() => void onInject(service, failureType, intensityFor(failureType))}
                  className={`rounded-md border px-2 py-1 text-xs transition disabled:opacity-40 ${FAILURE_STYLES[failureType] ?? "border-slate-700 text-slate-300 hover:bg-slate-800"}`}
                >
                  {FAILURE_LABELS[failureType] ?? failureType}
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>

      <div className="mt-4 border-t border-slate-800 pt-3">
        <div className="mb-2 text-xs uppercase tracking-wide text-slate-500">
          Active flags ({active.length})
        </div>
        {active.length === 0 ? (
          <div className="text-xs text-slate-600">none — system is healthy</div>
        ) : (
          <ul className="space-y-1">
            {active.map((inj) => (
              <li
                key={inj.injection_id}
                className="flex items-center justify-between rounded-md border border-slate-800 bg-slate-950/60 px-2 py-1.5 text-xs"
              >
                <span className="font-mono text-slate-300">
                  {inj.service}
                  <span className="text-slate-500"> / </span>
                  <span className="text-amber-300">{inj.failure_type}</span>
                  <span className="text-slate-500"> = {inj.intensity}</span>
                  <span className="text-slate-600"> ttl {inj.ttl_s}s</span>
                </span>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void onClear(inj.injection_id)}
                  className="ml-2 rounded border border-slate-700 px-1.5 py-0.5 text-[11px] text-slate-400 transition hover:border-emerald-600 hover:text-emerald-300 disabled:opacity-40"
                >
                  clear
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
