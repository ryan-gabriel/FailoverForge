"use client";

import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { MetricsTick } from "../lib/types";

const SERVICE_COLORS: Record<string, string> = {
  "checkout-api": "#38bdf8",
  "inventory-api": "#a78bfa",
  "fulfillment-worker": "#34d399",
};

type Point = {
  t: string;
  checkout: number | null;
  inventory: number | null;
  worker: number | null;
  queue: number | null;
  hitRate: number | null;
};

function servicePoint(tick: MetricsTick): Point {
  const m = (service: string, pick: (v: { rps: number; error_rate: number; p95_ms: number | null }) => number | null) =>
    tick.services[service] ? pick(tick.services[service]) : null;
  return {
    t: tick.ts.slice(11, 19),
    checkout: m("checkout-api", (v) => v.rps),
    inventory: m("inventory-api", (v) => v.rps),
    worker: m("fulfillment-worker", (v) => v.rps),
    queue: tick.queue.depth,
    hitRate: tick.cache.hit_rate * 100,
  };
}

function ratePoint(tick: MetricsTick, field: "error_rate" | "p95_ms"): Point {
  const pick = (service: string) => {
    const m = tick.services[service];
    if (!m) return null;
    const v = field === "error_rate" ? m.error_rate * 100 : m.p95_ms;
    return v;
  };
  return {
    t: tick.ts.slice(11, 19),
    checkout: pick("checkout-api"),
    inventory: pick("inventory-api"),
    worker: pick("fulfillment-worker"),
    queue: null,
    hitRate: null,
  };
}

function ChartCard({
  title,
  unit,
  rows,
  yKey,
}: {
  title: string;
  unit: string;
  rows: Point[];
  yKey: (keyof Point)[];
}) {
  const lines: { key: keyof Point; name: string; color: string }[] = (
    [
      { key: "checkout", name: "checkout", color: SERVICE_COLORS["checkout-api"] },
      { key: "inventory", name: "inventory", color: SERVICE_COLORS["inventory-api"] },
      { key: "worker", name: "worker", color: SERVICE_COLORS["fulfillment-worker"] },
      { key: "queue", name: "queue", color: "#fbbf24" },
      { key: "hitRate", name: "hit rate", color: "#f472b6" },
    ] satisfies { key: keyof Point; name: string; color: string }[]
  ).filter((l) => yKey.includes(l.key));

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
      <div className="mb-2 flex items-baseline justify-between">
        <h3 className="text-sm font-medium text-slate-300">{title}</h3>
        <span className="text-xs text-slate-500">{unit}</span>
      </div>
      <div className="h-32">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={rows} margin={{ top: 4, right: 8, bottom: 0, left: -18 }}>
            <CartesianGrid stroke="#1e293b" strokeDasharray="3 3" />
            <XAxis dataKey="t" tick={{ fontSize: 10, fill: "#64748b" }} minTickGap={40} />
            <YAxis tick={{ fontSize: 10, fill: "#64748b" }} width={40} />
            <Tooltip
              contentStyle={{
                background: "#0f172a",
                border: "1px solid #1e293b",
                borderRadius: 8,
                fontSize: 12,
              }}
            />
            {lines.map((line) => (
              <Line
                key={line.key}
                dataKey={line.key}
                name={line.name}
                stroke={line.color}
                dot={false}
                strokeWidth={1.5}
                isAnimationActive={false}
                connectNulls
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
      <div className="mt-1 flex gap-3 text-[10px] text-slate-500">
        {lines.map((line) => (
          <span key={line.key} className="flex items-center gap-1">
            <span className="inline-block h-2 w-2 rounded-full" style={{ background: line.color }} />
            {line.name}
          </span>
        ))}
      </div>
    </div>
  );
}

export default function MetricsCharts({ history }: { history: MetricsTick[] }) {
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <ChartCard
        title="Requests / s"
        unit="rps"
        rows={history.map(servicePoint)}
        yKey={["checkout", "inventory", "worker"]}
      />
      <ChartCard
        title="Error rate"
        unit="%"
        rows={history.map((t) => ratePoint(t, "error_rate"))}
        yKey={["checkout", "inventory", "worker"]}
      />
      <ChartCard
        title="Latency p95"
        unit="ms"
        rows={history.map((t) => ratePoint(t, "p95_ms"))}
        yKey={["checkout", "inventory", "worker"]}
      />
      <ChartCard
        title="Queue depth + cache hit rate"
        unit="msgs / %"
        rows={history.map(servicePoint)}
        yKey={["queue", "hitRate"]}
      />
    </div>
  );
}
