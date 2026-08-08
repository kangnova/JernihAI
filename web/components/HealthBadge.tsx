"use client";

import { useEffect, useState } from "react";

type Status = "checking" | "ok" | "down";

const styles: Record<Status, { dot: string; text: string; label: string }> = {
  checking: { dot: "bg-amber-400 animate-pulse", text: "text-amber-300", label: "Memeriksa…" },
  ok: { dot: "bg-emerald-400", text: "text-emerald-300", label: "Online" },
  down: { dot: "bg-rose-500", text: "text-rose-300", label: "Offline" },
};

export function HealthBadge() {
  const [status, setStatus] = useState<Status>("checking");

  useEffect(() => {
    const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
    fetch(`${apiUrl}/api/v1/health`)
      .then((res) => setStatus(res.ok ? "ok" : "down"))
      .catch(() => setStatus("down"));
  }, []);

  const s = styles[status];

  return (
    <span
      className={`inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/5 px-3 py-1 text-sm transition-colors ${s.text}`}
    >
      <span className={`size-2 rounded-full ${s.dot}`} />
      API {s.label}
    </span>
  );
}
