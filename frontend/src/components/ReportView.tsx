"use client";

import { Fragment, type ReactNode } from "react";
import type { IncidentReport } from "../lib/types";

/** Minimal inline renderer for the controlled markdown subset that
 *  reports.py emits: **bold** and `code`. Display-only, zero deps. */
function renderInline(text: string): ReactNode[] {
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
  return parts.map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**")) {
      return <strong key={i} className="font-semibold text-slate-200">{part.slice(2, -2)}</strong>;
    }
    if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
      return (
        <code key={i} className="rounded bg-slate-800 px-1 font-mono text-[11px] text-sky-300">
          {part.slice(1, -1)}
        </code>
      );
    }
    return <Fragment key={i}>{part}</Fragment>;
  });
}

function MarkdownBlock({ markdown }: { markdown: string }) {
  const lines = markdown.split("\n");
  const blocks: ReactNode[] = [];
  let listBuffer: string[] = [];

  const flushList = (key: string) => {
    if (listBuffer.length === 0) return;
    blocks.push(
      <ul key={key} className="my-1 list-disc space-y-0.5 pl-5 text-sm text-slate-400">
        {listBuffer.map((item, i) => (
          <li key={i}>{renderInline(item)}</li>
        ))}
      </ul>,
    );
    listBuffer = [];
  };

  lines.forEach((line, i) => {
    const key = `l${i}`;
    if (line.startsWith("- ")) {
      listBuffer.push(line.slice(2));
      return;
    }
    flushList(key);
    if (line.startsWith("### ")) {
      blocks.push(
        <h4 key={key} className="mt-3 text-sm font-semibold text-slate-200">{line.slice(4)}</h4>,
      );
    } else if (line.startsWith("## ")) {
      blocks.push(
        <h3 key={key} className="mt-4 border-b border-slate-800 pb-1 text-sm font-semibold text-slate-200">
          {line.slice(3)}
        </h3>,
      );
    } else if (line.startsWith("# ")) {
      blocks.push(
        <h2 key={key} className="text-lg font-bold text-slate-100">{line.slice(2)}</h2>,
      );
    } else if (line.trim() !== "") {
      blocks.push(
        <p key={key} className="my-1 text-sm text-slate-400">{renderInline(line)}</p>,
      );
    }
  });
  flushList("lend");
  return <>{blocks}</>;
}

export default function ReportView({ report }: { report: IncidentReport }) {
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2 text-xs text-slate-500">
        <span className="rounded-full border border-slate-700 px-2 py-0.5 font-mono">
          {report.timeline_summary.length} events
        </span>
        {report.root_cause && (
          <span className="rounded-full border border-orange-800 bg-orange-950/40 px-2 py-0.5 font-mono text-orange-300">
            root: {report.root_cause.service} / {report.root_cause.failure_type}
          </span>
        )}
        {report.mttr_s != null && (
          <span className="rounded-full border border-emerald-800 bg-emerald-950/40 px-2 py-0.5 font-mono text-emerald-300">
            mttr {report.mttr_s}s
          </span>
        )}
      </div>
      <MarkdownBlock markdown={report.markdown} />
    </div>
  );
}
