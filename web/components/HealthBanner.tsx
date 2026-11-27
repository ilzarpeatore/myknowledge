"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

type Check = { key: string; label: string; status: "ok" | "warn" | "fail"; detail: string };

/** A one-line warning on the home page when something needs attention. */
export function HealthBanner() {
  const [bad, setBad] = useState<Check[]>([]);
  useEffect(() => {
    api<{ checks: Check[] }>("/api/system/health").then((h) => setBad(h.checks.filter((c) => c.status !== "ok"))).catch(() => {});
  }, []);
  if (bad.length === 0) return null;
  const fail = bad.some((c) => c.status === "fail");
  return (
    <p className="card" style={{ borderLeft: `4px solid ${fail ? "#c62828" : "#b26a00"}` }}>
      {fail ? "Hay un problema" : "Hay algo que revisar"}: {bad.map((c) => `${c.label} (${c.detail})`).join(" · ")}. <Link href="/system">Ver Sistema</Link>
    </p>
  );
}
