import type { Metadata } from "next";
import type { ReactNode } from "react";
import "./globals.css";

export const metadata: Metadata = {
  title: "Failover Forge",
  description: "Chaos engineering training ground: break a real system, watch the blast radius",
};

export default function RootLayout({
  children,
}: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="en">
      <body className="bg-slate-950 text-slate-100 antialiased">{children}</body>
    </html>
  );
}
