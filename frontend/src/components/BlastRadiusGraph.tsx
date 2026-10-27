"use client";

import type { Topology } from "../lib/types";

/**
 * Blast-radius graph. Decision (Phase 2, locked): plain SVG — no graph
 * library. The topology is static (API_SPEC §2.1) with fixed hand-placed
 * layout; only node/edge status changes live. Layout:
 *
 *   inventory ──▶ checkout ──▶ rabbitmq ──▶ fulfillment-worker
 *                    │                        │
 *                    ├──▶ redis               │
 *                    └────────▶ postgres ◀────┘
 */

const VIEW_W = 720;
const VIEW_H = 260;

const LAYOUT: Record<string, { x: number; y: number }> = {
  "inventory-api": { x: 75, y: 55 },
  "checkout-api": { x: 275, y: 55 },
  rabbitmq: { x: 475, y: 55 },
  "fulfillment-worker": { x: 645, y: 55 },
  redis: { x: 245, y: 190 },
  postgres: { x: 505, y: 190 },
};

const HEALTH_COLORS: Record<string, string> = {
  healthy: "#10b981",
  degraded: "#f59e0b",
  down: "#ef4444",
  recovering: "#38bdf8",
};

const NODE_W = 132;
const NODE_H = 52;

function healthOf(topology: Topology, states: Record<string, string>, id: string): string {
  return states[id] ?? topology.nodes.find((n) => n.id === id)?.health ?? "healthy";
}

function isUnhealthy(health: string): boolean {
  return health === "degraded" || health === "down";
}

function edgePath(from: string, to: string): string | null {
  const a = LAYOUT[from];
  const b = LAYOUT[to];
  if (!a || !b) return null;
  // Start/end at box borders instead of centers.
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const horizontal = Math.abs(dx) > Math.abs(dy);
  const startX = a.x + (horizontal ? Math.sign(dx) * (NODE_W / 2 + 4) : 0);
  const startY = a.y + (horizontal ? 0 : Math.sign(dy) * (NODE_H / 2 + 4));
  const endX = b.x - (horizontal ? Math.sign(dx) * (NODE_W / 2 + 12) : 0);
  const endY = b.y - (horizontal ? 0 : Math.sign(dy) * (NODE_H / 2 + 12));
  const midX = horizontal ? (startX + endX) / 2 : startX;
  const midY = horizontal ? startY : (startY + endY) / 2;
  if (horizontal) {
    return `M ${startX} ${startY} L ${midX} ${midY} L ${midX} ${endY} L ${endX} ${endY}`;
  }
  return `M ${startX} ${startY} L ${midX} ${midY} L ${endX} ${midY} L ${endX} ${endY}`;
}

export default function BlastRadiusGraph({
  topology,
  nodeStates,
  injectedServices,
}: {
  topology: Topology;
  nodeStates: Record<string, string>;
  injectedServices: string[];
}) {
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
      <div className="mb-2 flex items-baseline justify-between">
        <h3 className="text-sm font-medium text-slate-300">Blast radius</h3>
        <span className="text-xs text-slate-500">live</span>
      </div>
      <svg viewBox={`0 0 ${VIEW_W} ${VIEW_H}`} className="w-full" role="img" aria-label="service topology">
        <defs>
          <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#475569" />
          </marker>
          <marker id="arrow-hot" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#f59e0b" />
          </marker>
        </defs>
        {topology.edges.map((edge) => {
          const d = edgePath(edge.from, edge.to);
          if (!d) return null;
          // Cascade highlight: propagation path is lit when both ends are
          // unhealthy — upstream fault visibly travelling downstream.
          const cascading =
            isUnhealthy(healthOf(topology, nodeStates, edge.from)) &&
            isUnhealthy(healthOf(topology, nodeStates, edge.to));
          return (
            <path
              key={`${edge.from}->${edge.to}`}
              d={d}
              fill="none"
              stroke={cascading ? "#f59e0b" : "#475569"}
              strokeWidth={cascading ? 2.5 : 1.5}
              strokeDasharray={cascading ? "6 4" : undefined}
              markerEnd={cascading ? "url(#arrow-hot)" : "url(#arrow)"}
              className={cascading ? "animate-[dashmove_1.2s_linear_infinite]" : undefined}
            />
          );
        })}
        {topology.nodes.map((node) => {
          const pos = LAYOUT[node.id];
          if (!pos) return null;
          const health = healthOf(topology, nodeStates, node.id);
          const color = HEALTH_COLORS[health] ?? HEALTH_COLORS.healthy;
          const injected = injectedServices.includes(node.id);
          const isService = node.kind === "service";
          return (
            <g key={node.id} transform={`translate(${pos.x - NODE_W / 2}, ${pos.y - NODE_H / 2})`}>
              {injected && (
                <rect
                  x={-4}
                  y={-4}
                  width={NODE_W + 8}
                  height={NODE_H + 8}
                  rx={12}
                  fill="none"
                  stroke="#ef4444"
                  strokeWidth={2}
                  strokeDasharray="4 3"
                  className="animate-pulse"
                />
              )}
              <rect
                width={NODE_W}
                height={NODE_H}
                rx={8}
                fill={isService ? "#0f172a" : "#020617"}
                stroke={color}
                strokeWidth={2}
              />
              <circle cx={14} cy={15} r={4} fill={color} />
              <text x={26} y={19} fontSize={11} fill="#e2e8f0" fontFamily="ui-monospace, monospace">
                {node.id}
              </text>
              <text x={14} y={38} fontSize={9} fill={color} className="uppercase">
                {health}
              </text>
            </g>
          );
        })}
      </svg>
      <div className="mt-1 flex gap-3 text-[10px] text-slate-500">
        {Object.entries(HEALTH_COLORS).map(([name, color]) => (
          <span key={name} className="flex items-center gap-1">
            <span className="inline-block h-2 w-2 rounded-full" style={{ background: color }} />
            {name}
          </span>
        ))}
        <span className="flex items-center gap-1">
          <span className="inline-block h-2 w-3 rounded-sm border-2 border-dashed border-red-500" />
          injected
        </span>
      </div>
      <style>{`@keyframes dashmove { to { stroke-dashoffset: -20; } }`}</style>
    </div>
  );
}
