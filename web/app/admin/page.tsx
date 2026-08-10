"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import type { AdminJob, AdminStats, JobStatus } from "@/lib/api";
import { getAdminStats, listAdminJobs } from "@/lib/api";
import { useAuth } from "@/lib/auth";

const STATUS_LABEL: Record<JobStatus, string> = {
  queued: "Antre",
  processing: "Memproses",
  completed: "Selesai",
  failed: "Gagal",
};

const STATUS_STYLE: Record<JobStatus, string> = {
  queued: "border-amber-400/30 bg-amber-400/10 text-amber-300",
  processing: "border-sky-400/30 bg-sky-400/10 text-sky-300",
  completed: "border-emerald-400/30 bg-emerald-400/10 text-emerald-300",
  failed: "border-rose-500/30 bg-rose-500/10 text-rose-300",
};

function StatCard({
  label,
  value,
  accent,
}: {
  label: string;
  value: string | number;
  accent: string;
}) {
  return (
    <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-5">
      <p className="text-xs uppercase tracking-wide text-slate-500">{label}</p>
      <p className={`mt-2 text-3xl font-bold ${accent}`}>{value}</p>
    </div>
  );
}

export default function AdminPage() {
  const { status, user } = useAuth();
  const router = useRouter();
  const [stats, setStats] = useState<AdminStats | null>(null);
  const [jobs, setJobs] = useState<AdminJob[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (status === "unauthenticated") {
      router.push("/login");
      return;
    }
    if (status === "authenticated" && !user?.is_admin) {
      setError("Halaman ini khusus admin.");
      return;
    }
    if (status !== "authenticated" || !user?.is_admin) return;
    setError(null);
    getAdminStats()
      .then(setStats)
      .catch((err) =>
        setError(err instanceof Error ? err.message : "Gagal memuat statistik"),
      );
    listAdminJobs(10, 0)
      .then((d) => setJobs(d.items))
      .catch((err) =>
        setError(err instanceof Error ? err.message : "Gagal memuat job"),
      );
  }, [status, user?.is_admin, router]);

  if (status !== "authenticated" || !user) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-slate-950 text-slate-100">
        <p className="text-slate-400">Memeriksa sesi…</p>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-slate-950 text-slate-100">
      <nav className="mx-auto flex max-w-6xl items-center justify-between px-6 py-6">
        <Link href="/dashboard" className="flex items-center gap-2 font-semibold">
          <span className="grid size-8 place-items-center rounded-lg bg-gradient-to-br from-indigo-500 to-fuchsia-500 text-sm font-bold">
            J
          </span>
          JernihAI
        </Link>
        <Link
          href="/dashboard"
          className="rounded-full border border-white/10 bg-white/5 px-4 py-1.5 text-sm text-slate-300 transition-colors hover:bg-white/10"
        >
          ← Dashboard
        </Link>
      </nav>

      <section className="mx-auto max-w-6xl px-6 pt-10">
        <h1 className="text-3xl font-bold">Admin</h1>
        <p className="mt-2 text-slate-400">
          Monitoring platform: user, job, dan kesehatan queue (FR-13).
        </p>

        {error && (
          <p className="mt-6 rounded-lg border border-rose-500/30 bg-rose-500/10 px-4 py-2 text-sm text-rose-300">
            {error}
          </p>
        )}

        {stats && (
          <div className="mt-8 grid grid-cols-2 gap-4 lg:grid-cols-3">
            <StatCard label="Total user" value={stats.total_users} accent="text-indigo-300" />
            <StatCard label="User baru hari ini" value={stats.users_today} accent="text-sky-300" />
            <StatCard label="Total job" value={stats.total_jobs} accent="text-emerald-300" />
            <StatCard label="Job hari ini" value={stats.jobs_today} accent="text-amber-300" />
            <StatCard label="Revenue (IDR)" value={stats.revenue_idr} accent="text-fuchsia-300" />
            <StatCard
              label="Kuota gratis/user"
              value={stats.free_quota_limit}
              accent="text-slate-200"
            />
          </div>
        )}

        {stats && (
          <div className="mt-6 rounded-2xl border border-white/10 bg-white/[0.03] p-5">
            <p className="mb-3 text-sm font-medium text-slate-300">
              Status job
            </p>
            <div className="flex flex-wrap gap-2">
              {Object.entries(stats.jobs_by_status).map(([statusKey, count]) => (
                <span
                  key={statusKey}
                  className={`rounded-full border px-3 py-1 text-sm ${STATUS_STYLE[statusKey as JobStatus]}`}
                >
                  {STATUS_LABEL[statusKey as JobStatus]}: {count}
                </span>
              ))}
            </div>
          </div>
        )}

        <div className="mt-8">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-lg font-semibold">Job terbaru</h2>
            <Link
              href="/history"
              className="text-sm text-slate-400 hover:text-slate-200"
            >
              Riwayat →
            </Link>
          </div>
          {jobs === null ? (
            <p className="text-slate-400">Memuat…</p>
          ) : jobs.length === 0 ? (
            <p className="text-slate-500">Belum ada job.</p>
          ) : (
            <ul className="space-y-2">
              {jobs.map((j) => (
                <li
                  key={j.id}
                  className="flex flex-col gap-2 rounded-xl border border-white/10 bg-white/[0.03] px-4 py-3 text-sm sm:flex-row sm:items-center sm:justify-between"
                >
                  <div className="min-w-0">
                    <p className="truncate font-medium text-slate-200">
                      {j.original_name}{" "}
                      <span className="text-slate-500">
                        — {j.user_email ?? "?"}
                      </span>
                    </p>
                    <p className="text-xs text-slate-500">
                      {j.scale}× · {j.output_format.toUpperCase()} ·{" "}
                      {j.created_at ?? "—"}
                    </p>
                    {j.error && (
                      <p className="mt-1 truncate text-xs text-rose-300/80">
                        {j.error}
                      </p>
                    )}
                  </div>
                  <span
                    className={`shrink-0 rounded-full border px-2 py-0.5 text-xs ${STATUS_STYLE[j.status]}`}
                  >
                    {STATUS_LABEL[j.status]}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>
    </main>
  );
}
