import type { ReactNode } from "react";

type GlassProps = {
  children: ReactNode;
  className?: string;
  strong?: boolean;
  interactive?: boolean;
  as?: "div" | "section" | "aside" | "header" | "article";
};

export default function Glass({
  children,
  className = "",
  strong = false,
  interactive = false,
  as = "div",
}: GlassProps) {
  const Tag = as;
  const base = strong ? "glass-strong" : "glass";
  const inter = interactive ? "glass-interactive" : "";
  return (
    <Tag className={`${base} ${inter} ${className}`.trim()}>{children}</Tag>
  );
}

export function GlassPill({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return <span className={`glass-pill ${className}`.trim()}>{children}</span>;
}