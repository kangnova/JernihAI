"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import type { AdminJob, AdminStats, AdminUser, JobStatus } from "@/lib/api";
import {
  deleteAdminJob,
  getAdminStats,
  listAdminJobs,
  listAdminUsers,
  resetAdminQuota,
} from "@/lib/api";
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

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("id-ID", {
      day: "2-digit",
      month: "short",
      year: "numeric",
    });
  } catch {
    return iso;
  }
}

export default function AdminPage() {
  const { status, user } = useAuth();
  const router = useRouter();
  const [stats, setStats] = useState<AdminStats | null>(null);
  const [jobs, setJobs] = useState<AdminJob[] | null>(null);
  const [users, setUsers] = useState<AdminUser[] | null>(null);
  const [usersTotal, setUsersTotal] = useState(0);
  const [userSearch, setUserSearch] = useState("");
  const [selectedEmail, setSelectedEmail] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [resetEmail, setResetEmail] = useState("");
  const [toolMsg, setToolMsg] = useState<string | null>(null);
  const [busyJobId, setBusyJobId] = useState<string | null>(null);

  const loadUsers = useCallback(async (search?: string) => {
    const d = await listAdminUsers(search || undefined, 20, 0);
    setUsers(d.items);
    setUsersTotal(d.total);
  }, []);

  const loadJobs = useCallback(async (email?: string) => {
    const d = await listAdminJobs(20, 0, email || undefined);
    setJobs(d.items);
  }, []);

  const loadStats = useCallback(async () => {
    setStats(await getAdminStats());
  }, []);

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
    Promise.allSettled([loadStats(), loadUsers(), loadJobs()]);
  }, [status, user?.is_admin, router, loadStats, loadUsers, loadJobs]);

  // Alat admin (pengelola/pengembang): reset kuota & hapus job uji coba.
  async function handleQuotaReset(all: boolean) {
    setToolMsg(null);
    try {
      const res = await resetAdminQuota({
        email: all ? undefined : resetEmail.trim() || undefined,
        all,
      });
      setToolMsg(
        res.email
          ? `Kuota ${res.email} di-reset (${res.reset} user).`
          : `Kuota di-reset untuk ${res.reset} user.`,
      );
      await loadUsers(); // refresh angka kuota di direktori user
    } catch (err) {
      setToolMsg(
        `Gagal reset: ${err instanceof Error ? err.message : "terjadi kesalahan"}`,
      );
    }
  }

  async function handleDeleteJob(j: AdminJob) {
    if (!window.confirm(`Hapus job "${j.original_name}" beserta file-nya?`)) return;
    setBusyJobId(j.id);
    setToolMsg(null);
    try {
      await deleteAdminJob(j.id);
      setToolMsg(`Job ${j.original_name} dihapus (file ikut terhapus).`);
      await Promise.all([loadJobs(selectedEmail ?? undefined), loadUsers(), loadStats()]);
    } catch (err) {
      setToolMsg(
        `Gagal hapus: ${err instanceof Error ? err.message : "terjadi kesalahan"}`,
      );
    } finally {
      setBusyJobId(null);
    }
  }

  async function handleUserSearch() {
    await loadUsers(userSearch.trim());
  }

  async function showUserHistory(email: string) {
    setSelectedEmail(email);
    await loadJobs(email);
  }

  async function clearUserHistory() {
    setSelectedEmail(null);
    await loadJobs();
  }

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
          Monitoring platform: user, kuota, kredit, dan riwayat job (FR-13).
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
            <p className="mb-3 text-sm font-medium text-slate-300">Status job</p>
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

        {/* Direktori user: email, kuota, kredit, consent, jumlah riwayat */}
        <div className="mt-8 rounded-2xl border border-white/10 bg-white/[0.03] p-5">
          <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="text-sm font-medium text-slate-300">
                User &amp; kuota{" "}
                <span className="text-xs text-slate-500">
                  ({usersTotal} total — {users?.length ?? 0} ditampilkan)
                </span>
              </p>
              <p className="mt-0.5 text-xs text-slate-500">
                Email, pemakaian kuota gratis, saldo kredit, consent privasi,
                dan jumlah riwayat per user.
              </p>
            </div>
            <div className="flex gap-2">
              <input
                type="text"
                value={userSearch}
                onChange={(e) => setUserSearch(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleUserSearch()}
                placeholder="Cari email…"
                className="w-52 rounded-lg border border-white/10 bg-white/5 px-3 py-1.5 text-sm text-slate-200 placeholder:text-slate-600 focus:border-indigo-400 focus:outline-none"
              />
              <button
                type="button"
                onClick={handleUserSearch}
                className="rounded-lg border border-white/10 bg-white/5 px-3 py-1.5 text-sm text-slate-200 transition-colors hover:bg-white/10"
              >
                Cari
              </button>
            </div>
          </div>
          {users === null ? (
            <p className="text-slate-400">Memuat…</p>
          ) : users.length === 0 ? (
            <p className="text-slate-500">Tidak ada user.</p>
          ) : (
            <ul className="space-y-2">
              {users.map((u) => (
                <li
                  key={u.id}
                  className="flex flex-col gap-2 rounded-xl border border-white/10 bg-white/5 px-4 py-3 text-sm lg:flex-row lg:items-center lg:justify-between"
                >
                  <div className="min-w-0">
                    <p className="truncate font-medium text-slate-100">
                      {u.email}
                      {u.provider === "google" && (
                        <span className="ml-2 rounded-full border border-white/10 bg-white/5 px-2 py-0.5 text-[10px] text-slate-400">
                          Google
                        </span>
                      )}
                    </p>
                    <p className="text-xs text-slate-500">
                      {u.name ?? "—"} · daftar {formatDate(u.created_at)}
                    </p>
                  </div>
                  <div className="flex flex-wrap items-center gap-2 text-xs">
                    <span
                      className={`rounded-full border px-2 py-0.5 ${
                        u.quota_remaining > 0
                          ? "border-emerald-400/30 bg-emerald-400/10 text-emerald-300"
                          : "border-rose-500/30 bg-rose-500/10 text-rose-300"
                      }`}
                    >
                      Kuota {u.quota_used}/{u.quota_limit} · sisa{" "}
                      {u.quota_remaining}
                    </span>
                    <span className="rounded-full border border-indigo-400/30 bg-indigo-500/10 px-2 py-0.5 text-indigo-300">
                      {u.credit_balance} kredit
                    </span>
                    <span
                      className={`rounded-full border px-2 py-0.5 ${
                        u.privacy_consent_at
                          ? "border-slate-400/30 bg-slate-400/10 text-slate-300"
                          : "border-amber-400/30 bg-amber-400/10 text-amber-300"
                      }`}
                    >
                      {u.privacy_consent_at ? "Consent ✓" : "Tanpa consent"}
                    </span>
                    <span className="rounded-full border border-white/10 bg-white/5 px-2 py-0.5 text-slate-300">
                      {u.job_count} job
                    </span>
                    <button
                      type="button"
                      onClick={() => showUserHistory(u.email)}
                      className="rounded-md border border-white/10 bg-white/5 px-2 py-1 text-slate-300 transition-colors hover:border-indigo-400/40 hover:bg-indigo-500/10 hover:text-indigo-200"
                    >
                      Riwayat
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* Alat admin (pengelola/pengembang): reset kuota & hapus job uji */}
        <div className="mt-8 rounded-2xl border border-white/10 bg-white/[0.03] p-5">
          <p className="mb-1 text-sm font-medium text-slate-300">Alat admin</p>
          <p className="mb-4 text-xs text-slate-500">
            Reset kuota gratis untuk melanjutkan uji coba, dan hapus job uji
            beserta file-nya di disk.
          </p>
          <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
            <div className="flex-1">
              <p className="mb-1 text-xs text-slate-400">
                Reset kuota user (email)
              </p>
              <input
                type="email"
                value={resetEmail}
                onChange={(e) => setResetEmail(e.target.value)}
                placeholder="user@example.com — kosongkan untuk semua"
                className="w-full rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-sm text-slate-200 placeholder:text-slate-600 focus:border-indigo-400 focus:outline-none"
              />
            </div>
            <button
              type="button"
              onClick={() => handleQuotaReset(false)}
              disabled={!resetEmail.trim()}
              className="rounded-lg border border-indigo-400/30 bg-indigo-500/10 px-4 py-2 text-sm font-medium text-indigo-300 transition-colors hover:bg-indigo-500/20 disabled:opacity-40"
            >
              Reset kuota
            </button>
            <button
              type="button"
              onClick={() => handleQuotaReset(true)}
              className="rounded-lg border border-white/10 bg-white/5 px-4 py-2 text-sm font-medium text-slate-200 transition-colors hover:bg-white/10"
            >
              Reset semua user
            </button>
          </div>
          {toolMsg && <p className="mt-3 text-sm text-slate-300">{toolMsg}</p>}
        </div>

        <div className="mt-8">
          <div className="mb-3 flex items-center justify-between gap-3">
            <h2 className="text-lg font-semibold">
              {selectedEmail ? `Riwayat: ${selectedEmail}` : "Job terbaru"}
            </h2>
            <div className="flex items-center gap-3">
              {selectedEmail && (
                <button
                  type="button"
                  onClick={clearUserHistory}
                  className="text-sm text-slate-400 transition-colors hover:text-slate-200"
                >
                  ✕ Hapus filter
                </button>
              )}
              <Link
                href="/history"
                className="text-sm text-slate-400 hover:text-slate-200"
              >
                Riwayat →
              </Link>
            </div>
          </div>
          {jobs === null ? (
            <p className="text-slate-400">Memuat…</p>
          ) : jobs.length === 0 ? (
            <p className="text-slate-500">
              {selectedEmail ? "User ini belum punya job." : "Belum ada job."}
            </p>
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
                  <div className="flex shrink-0 items-center gap-2">
                    <span
                      className={`rounded-full border px-2 py-0.5 text-xs ${STATUS_STYLE[j.status]}`}
                    >
                      {STATUS_LABEL[j.status]}
                    </span>
                    <button
                      type="button"
                      aria-label={`Hapus job ${j.original_name}`}
                      disabled={busyJobId !== null}
                      onClick={() => handleDeleteJob(j)}
                      className="rounded-md border border-white/10 bg-white/5 px-2 py-1 text-xs text-slate-400 transition-colors hover:border-rose-500/40 hover:bg-rose-500/10 hover:text-rose-300 disabled:opacity-40"
                    >
                      {busyJobId === j.id ? "…" : "Hapus"}
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>
    </main>
  );
}
