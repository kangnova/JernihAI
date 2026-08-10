"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import type { AdminJob, AdminTransaction, AdminUser } from "@/lib/api";
import {
  deleteAdminJob,
  deleteAdminUserJobs,
  getAdminUser,
  listAdminJobs,
  listAdminUserTransactions,
  resetAdminQuota,
} from "@/lib/api";
import {
  STATUS_LABEL,
  STATUS_STYLE,
  TXN_STATUS_LABEL,
  TXN_STATUS_STYLE,
  formatDate,
  formatDateTime,
} from "@/lib/admin-ui";
import { useAuth } from "@/lib/auth";

export default function AdminUserDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { status, user } = useAuth();
  const router = useRouter();
  const [profile, setProfile] = useState<AdminUser | null>(null);
  const [jobs, setJobs] = useState<AdminJob[] | null>(null);
  const [txns, setTxns] = useState<AdminTransaction[] | null>(null);
  const [txnsTotal, setTxnsTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [actionMsg, setActionMsg] = useState<string | null>(null);
  const [busyJobId, setBusyJobId] = useState<string | null>(null);
  const [busyReset, setBusyReset] = useState(false);
  const [busyDeleteAll, setBusyDeleteAll] = useState(false);

  const loadUser = useCallback(async () => {
    const u = await getAdminUser(id);
    setProfile(u);
    return u;
  }, [id]);

  const loadJobs = useCallback(async (email: string) => {
    const d = await listAdminJobs(20, 0, email);
    setJobs(d.items);
  }, []);

  const loadTxns = useCallback(async () => {
    const d = await listAdminUserTransactions(id);
    setTxns(d.items);
    setTxnsTotal(d.total);
  }, [id]);

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
    (async () => {
      try {
        const u = await loadUser();
        await Promise.all([loadJobs(u.email), loadTxns()]);
      } catch (err) {
        setError(
          err instanceof Error
            ? err.message
            : "User tidak ditemukan atau terjadi kesalahan.",
        );
      }
    })();
  }, [status, user?.is_admin, router, loadUser, loadJobs, loadTxns]);

  async function handleDeleteJob(j: AdminJob) {
    if (!window.confirm(`Hapus job "${j.original_name}" beserta file-nya?`)) return;
    setBusyJobId(j.id);
    setActionMsg(null);
    try {
      await deleteAdminJob(j.id);
      setActionMsg(`Job ${j.original_name} dihapus.`);
      if (profile) {
        await Promise.all([loadJobs(profile.email), loadUser()]); // job_count ikut refresh
      }
    } catch (err) {
      setActionMsg(`Gagal hapus: ${err instanceof Error ? err.message : "kesalahan"}`);
    } finally {
      setBusyJobId(null);
    }
  }

  async function handleResetQuota() {
    if (!profile) return;
    setBusyReset(true);
    setActionMsg(null);
    try {
      const res = await resetAdminQuota({ email: profile.email });
      await loadUser();
      setActionMsg(`Kuota ${res.email} di-reset (${res.reset} user).`);
    } catch (err) {
      setActionMsg(
        `Gagal reset: ${err instanceof Error ? err.message : "kesalahan"}`,
      );
    } finally {
      setBusyReset(false);
    }
  }

  async function handleDeleteAllJobs() {
    if (!profile) return;
    if (
      !window.confirm(
        `Hapus SEMUA ${jobs?.length ?? 0} job milik ${profile.email} beserta file-nya?`,
      )
    )
      return;
    setBusyDeleteAll(true);
    setActionMsg(null);
    try {
      const res = await deleteAdminUserJobs(profile.id);
      await Promise.all([loadJobs(profile.email), loadUser()]);
      setActionMsg(`${res.deleted} job dihapus (${res.files_deleted} file).`);
    } catch (err) {
      setActionMsg(`Gagal hapus: ${err instanceof Error ? err.message : "kesalahan"}`);
    } finally {
      setBusyDeleteAll(false);
    }
  }

  if (status !== "authenticated" || !user) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-slate-950 text-slate-100">
        <p className="text-slate-400">Memeriksa sesi…</p>
      </main>
    );
  }

  const txnStatus = (s: string) =>
    (["pending", "paid", "failed", "expired"].includes(s)
      ? s
      : "failed") as AdminTransaction["status"];

  return (
    <main className="min-h-screen bg-slate-950 text-slate-100">
      <nav className="mx-auto flex max-w-5xl items-center justify-between px-6 py-6">
        <Link href="/dashboard" className="flex items-center gap-2 font-semibold">
          <span className="grid size-8 place-items-center rounded-lg bg-gradient-to-br from-indigo-500 to-fuchsia-500 text-sm font-bold">
            J
          </span>
          JernihAI
        </Link>
        <Link
          href="/admin"
          className="rounded-full border border-white/10 bg-white/5 px-4 py-1.5 text-sm text-slate-300 transition-colors hover:bg-white/10"
        >
          ← Admin
        </Link>
      </nav>

      <section className="mx-auto max-w-5xl px-6 pt-10">
        {error && (
          <p className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-4 py-2 text-sm text-rose-300">
            {error}
          </p>
        )}

        {profile ? (
          <>
            {/* Kartu profil user */}
            <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-6">
              <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
                <div className="flex items-center gap-4">
                  <span className="grid size-14 place-items-center rounded-2xl bg-gradient-to-br from-indigo-500 to-fuchsia-500 text-xl font-bold">
                    {(profile.name ?? profile.email)[0]?.toUpperCase() ?? "?"}
                  </span>
                  <div>
                    <h1 className="text-xl font-bold">{profile.email}</h1>
                    <p className="mt-0.5 text-sm text-slate-400">
                      {profile.name ?? "Tanpa nama"} ·{" "}
                      {profile.provider === "google" ? "Google" : "Local"} · daftar{" "}
                      {formatDate(profile.created_at)}
                    </p>
                  </div>
                </div>
                <div className="flex flex-col items-start gap-2 sm:items-end">
                  <div className="flex flex-wrap gap-2 text-xs">
                  <span
                    className={`rounded-full border px-2 py-1 ${
                      profile.quota_remaining > 0
                        ? "border-emerald-400/30 bg-emerald-400/10 text-emerald-300"
                        : "border-rose-500/30 bg-rose-500/10 text-rose-300"
                    }`}
                  >
                    Kuota {profile.quota_used}/{profile.quota_limit} · sisa{" "}
                    {profile.quota_remaining}
                  </span>
                  <span className="rounded-full border border-indigo-400/30 bg-indigo-500/10 px-2 py-1 text-indigo-300">
                    {profile.credit_balance} kredit
                  </span>
                  <span
                    className={`rounded-full border px-2 py-1 ${
                      profile.privacy_consent_at
                        ? "border-slate-400/30 bg-slate-400/10 text-slate-300"
                        : "border-amber-400/30 bg-amber-400/10 text-amber-300"
                    }`}
                  >
                    {profile.privacy_consent_at
                      ? `Consent ✓ ${formatDate(profile.privacy_consent_at)}`
                      : "Tanpa consent"}
                  </span>
                    <span className="rounded-full border border-white/10 bg-white/5 px-2 py-1 text-slate-300">
                      {profile.job_count} job
                    </span>
                  </div>
                  <button
                    type="button"
                    onClick={handleResetQuota}
                    disabled={busyReset}
                    className="rounded-lg border border-indigo-400/30 bg-indigo-500/10 px-3 py-1.5 text-sm font-medium text-indigo-300 transition-colors hover:bg-indigo-500/20 disabled:opacity-40"
                  >
                    {busyReset ? "…" : "Reset kuota"}
                  </button>
                </div>
              </div>
              {actionMsg && (
                <p className="mt-3 text-sm text-slate-300">{actionMsg}</p>
              )}
            </div>

            {/* Riwayat job user */}
            <div className="mt-8">
              <div className="mb-3 flex items-center justify-between gap-3">
                <h2 className="text-lg font-semibold">
                  Riwayat job ({jobs?.length ?? 0})
                </h2>
                {jobs && jobs.length > 0 && (
                  <button
                    type="button"
                    onClick={handleDeleteAllJobs}
                    disabled={busyDeleteAll}
                    className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-1.5 text-sm font-medium text-rose-300 transition-colors hover:bg-rose-500/20 disabled:opacity-40"
                  >
                    {busyDeleteAll ? "…" : "Hapus semua job"}
                  </button>
                )}
              </div>
              {jobs === null ? (
                <p className="text-slate-400">Memuat…</p>
              ) : jobs.length === 0 ? (
                <p className="text-slate-500">User ini belum punya job.</p>
              ) : (
                <ul className="space-y-2">
                  {jobs.map((j) => (
                    <li
                      key={j.id}
                      className="flex flex-col gap-2 rounded-xl border border-white/10 bg-white/[0.03] px-4 py-3 text-sm sm:flex-row sm:items-center sm:justify-between"
                    >
                      <div className="min-w-0">
                        <p className="truncate font-medium text-slate-200">
                          {j.original_name}
                        </p>
                        <p className="text-xs text-slate-500">
                          {j.scale}× · {j.output_format.toUpperCase()} · dibuat{" "}
                          {formatDateTime(j.created_at)}
                          {j.finished_at && ` · selesai ${formatDateTime(j.finished_at)}`}
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

            {/* Transaksi kredit (FR-11) */}
            <div className="mt-8">
              <h2 className="mb-3 text-lg font-semibold">
                Transaksi kredit ({txnsTotal})
              </h2>
              {txns === null ? (
                <p className="text-slate-400">Memuat…</p>
              ) : txns.length === 0 ? (
                <p className="text-slate-500">Belum ada transaksi pembelian kredit.</p>
              ) : (
                <div className="overflow-x-auto rounded-xl border border-white/10">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-white/10 text-left text-xs uppercase tracking-wide text-slate-500">
                        <th className="px-4 py-2.5">Order</th>
                        <th className="px-4 py-2.5">Paket</th>
                        <th className="px-4 py-2.5 text-right">Jumlah (IDR)</th>
                        <th className="px-4 py-2.5 text-right">Kredit</th>
                        <th className="px-4 py-2.5">Status</th>
                        <th className="px-4 py-2.5">Dibuat</th>
                        <th className="px-4 py-2.5">Dibayar</th>
                      </tr>
                    </thead>
                    <tbody>
                      {txns.map((t) => (
                        <tr
                          key={t.id}
                          className="border-b border-white/5 last:border-0"
                        >
                          <td className="px-4 py-2.5 font-mono text-xs text-slate-300">
                            {t.order_id}
                          </td>
                          <td className="px-4 py-2.5 text-slate-300">
                            {t.package_slug}
                          </td>
                          <td className="px-4 py-2.5 text-right text-slate-300">
                            {t.amount_idr.toLocaleString("id-ID")}
                          </td>
                          <td className="px-4 py-2.5 text-right font-medium text-indigo-300">
                            +{t.credits}
                          </td>
                          <td className="px-4 py-2.5">
                            <span
                              className={`rounded-full border px-2 py-0.5 text-xs ${TXN_STATUS_STYLE[txnStatus(t.status)]}`}
                            >
                              {TXN_STATUS_LABEL[txnStatus(t.status)]}
                            </span>
                          </td>
                          <td className="px-4 py-2.5 text-xs text-slate-500">
                            {formatDateTime(t.created_at)}
                          </td>
                          <td className="px-4 py-2.5 text-xs text-slate-500">
                            {formatDateTime(t.paid_at)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </>
        ) : (
          !error && <p className="text-slate-400">Memuat profil…</p>
        )}
      </section>
    </main>
  );
}
