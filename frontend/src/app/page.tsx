"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import BlastRadiusGraph from "../components/BlastRadiusGraph";
import MetricsCharts from "../components/MetricsCharts";
import QueuePanel from "../components/QueuePanel";
import TriggerPanel from "../components/TriggerPanel";
import {
  ORCHESTRATOR_URL,
  clearAllInjections,
  clearInjection,
  getInjections,
  getLatestMetrics,
  getTopology,
  inject,
} from "../lib/api";
import type { IncidentStatus, Injection, MetricsTick, Topology, WSMessage } from "../lib/types";
import { subscribe } from "../lib/ws";

const HISTORY_MAX = 120; // 2 minutes at 1 tick/s

const SERVICE_ORDER = ["checkout-api", "inventory-api", "fulfillment-worker"] as const;

export default function DashboardPage() {
  const [connected, setConnected] = useState(false);
  const [tick, setTick] = useState<MetricsTick | null>(null);
  const [history, setHistory] = useState<MetricsTick[]>([]);
  const [injections, setInjections] = useState<Injection[]>([]);
  const [incident, setIncident] = useState<IncidentStatus | null>(null);
  const [topology, setTopology] = useState<Topology | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const historyRef = useRef<MetricsTick[]>([]);

  const applyTick = useCallback((next: MetricsTick) => {
    setTick(next);
    historyRef.current = [...historyRef.current, next].slice(-HISTORY_MAX);
    setHistory(historyRef.current);
  }, []);

  const refetchState = useCallback(async () => {
    const results = await Promise.allSettled([getLatestMetrics(), getInjections()]);
    if (results[0].status === "fulfilled") applyTick(results[0].value);
    if (results[1].status === "fulfilled") setInjections(results[1].value);
  }, [applyTick]);

  useEffect(() => {
    void getTopology().then(setTopology).catch(() => setTopology(null));
    void refetchState();

    const wsUrl = ORCHESTRATOR_URL.replace(/^http/, "ws") + "/ws";
    const unsubscribe = subscribe(wsUrl, {
      onConnectionChange: setConnected,
      onReconnect: () => void refetchState(),
      onMessage: (msg: WSMessage) => {
        if (msg.type === "metrics.tick") applyTick(msg.data);
        else if (msg.type === "injection.status") setInjections(msg.data);
        else if (msg.type === "incident.status") {
          setIncident(msg.data.status === "open" ? msg.data : null);
        }
        // queue.update / timeline.event: consumed by Phase 3 views
      },
    });
    return unsubscribe;
  }, [applyTick, refetchState]);

  const run = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setActionError(null);
    try {
      await action();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const onInject = (service: string, failureType: string, intensity: number) =>
    run(() => inject(service, failureType, intensity));
  const onClear = (injectionId: string) => run(() => clearInjection(injectionId));
  const onClearAll = () => run(() => clearAllInjections());

  const injectedServices = [...new Set(injections.map((i) => i.service))];
  const nodeStates = tick?.node_states ?? {};

  return (
    <main className="mx-auto max-w-7xl px-6 py-8">
      <header className="mb-6 flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Failover Forge</h1>
          <p className="mt-1 text-sm text-slate-400">
            Chaos engineering training ground: break a real system, watch the blast radius.
          </p>
        </div>
        <div className="flex items-center gap-3 text-xs">
          {incident && (
            <span className="rounded-full border border-red-700 bg-red-950/60 px-3 py-1 font-mono text-red-300">
              incident {incident.incident_id.slice(0, 8)} · open
            </span>
          )}
          <Link
            href="/incidents"
            className="rounded-full border border-slate-700 px-3 py-1 text-slate-400 transition-colors hover:border-slate-500 hover:text-slate-200"
          >
            Incidents →
          </Link>
          <span className="flex items-center gap-2 rounded-full border border-slate-700 px-3 py-1 text-slate-400">
            <span
              className={`h-2 w-2 rounded-full ${connected ? "bg-emerald-400" : "animate-pulse bg-amber-400"}`}
            />
            {connected ? "live" : "reconnecting…"}
          </span>
        </div>
      </header>

      {topology === null ? (
        <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-10 text-center text-sm text-slate-500">
          Waiting for orchestrator topology…
        </div>
      ) : (
        <div className="grid gap-4 lg:grid-cols-3">
          <section className="space-y-4 lg:col-span-2">
            <BlastRadiusGraph
              topology={topology}
              nodeStates={nodeStates}
              injectedServices={injectedServices}
            />
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              {SERVICE_ORDER.map((service) => {
                const m = tick?.services[service];
                return (
                  <div key={service} className="rounded-xl border border-slate-800 bg-slate-900/60 p-3">
                    <div className="truncate font-mono text-[11px] text-slate-500">{service}</div>
                    <div className="mt-1 font-mono text-lg text-slate-100">
                      {m ? m.rps.toFixed(1) : "–"}
                      <span className="ml-1 text-xs text-slate-500">rps</span>
                    </div>
                    <div className="flex justify-between font-mono text-[11px]">
                      <span className={m && m.error_rate > 0.05 ? "text-red-400" : "text-slate-500"}>
                        {m ? `${(m.error_rate * 100).toFixed(1)}% err` : "–"}
                      </span>
                      <span className={m && (m.p95_ms ?? 0) > 500 ? "text-amber-400" : "text-slate-500"}>
                        {m?.p95_ms != null ? `${Math.round(m.p95_ms)}ms p95` : "–"}
                      </span>
                    </div>
                  </div>
                );
              })}
              <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-3">
                <div className="font-mono text-[11px] text-slate-500">rabbitmq</div>
                <div className="mt-1 font-mono text-lg text-slate-100">
                  {tick?.queue.depth ?? "–"}
                  <span className="ml-1 text-xs text-slate-500">msgs</span>
                </div>
                <div className="flex justify-between font-mono text-[11px] text-slate-500">
                  <span>hit {tick ? `${Math.round(tick.cache.hit_rate * 100)}%` : "–"}</span>
                  <span className={tick && (tick.queue.dlq_depth ?? 0) > 0 ? "text-red-400" : ""}>
                    dlq {tick?.queue.dlq_depth ?? 0}
                  </span>
                </div>
              </div>
            </div>
            <MetricsCharts history={history} />
          </section>

          <aside className="space-y-4">
            <TriggerPanel
              active={injections}
              busy={busy}
              onInject={onInject}
              onClear={onClear}
              onClearAll={onClearAll}
            />
            {actionError && (
              <div className="rounded-xl border border-red-900 bg-red-950/40 p-3 font-mono text-xs text-red-300">
                {actionError}
              </div>
            )}
            <QueuePanel />
          </aside>
        </div>
      )}
    </main>
  );
}
