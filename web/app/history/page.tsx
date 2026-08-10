"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import type { Job, JobStatus } from "@/lib/api";
import { fetchJobResult, listJobs } from "@/lib/api";
import { useAuth } from "@/lib/auth";

const STATUS_STYLE: Record<JobStatus, string> = {
  queued: "border-amber-400/30 bg-amber-400/10 text-amber-300",
  processing: "border-sky-400/30 bg-sky-400/10 text-sky-300",
  completed: "border-emerald-400/30 bg-emerald-400/10 text-emerald-300",
  failed: "border-rose-500/30 bg-rose-500/10 text-rose-300",
};

const STATUS_LABEL: Record<JobStatus, string> = {
  queued: "Antre",
  processing: "Memproses",
  completed: "Selesai",
  failed: "Gagal",
};

const PAGE_SIZE = 20;

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleString("id-ID", {
      day: "2-digit",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

export default function HistoryPage() {
  const { status: authStatus, user } = useAuth();
  const router = useRouter();
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [downloadingId, setDownloadingId] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      const data = await listJobs(PAGE_SIZE, 0);
      setJobs(data.items);
      setTotal(data.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal memuat riwayat");
    }
  }, []);

  async function loadMore() {
    if (!jobs || loadingMore) return;
    setLoadingMore(true);
    setError(null);
    try {
      const data = await listJobs(PAGE_SIZE, jobs.length);
      setJobs((prev) => [...(prev ?? []), ...data.items]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal memuat riwayat");
    } finally {
      setLoadingMore(false);
    }
  }

  useEffect(() => {
    if (authStatus === "unauthenticated") {
      router.push("/login");
      return;
    }
    if (authStatus === "authenticated") load();
  }, [authStatus, router, load]);

  // Auto-refresh saat ada job yang masih berjalan (sama seperti JobUploader).
  useEffect(() => {
    if (authStatus !== "authenticated" || !jobs) return;
    const hasPending = jobs.some(
      (j) => j.status === "queued" || j.status === "processing",
    );
    if (!hasPending) return;
    const timer = setInterval(load, 3000);
    return () => clearInterval(timer);
  }, [authStatus, jobs, load]);

  async function handleDownload(job: Job) {
    if (downloadingId) return;
    setDownloadingId(job.id);
    setError(null);
    try {
      const blob = await fetchJobResult(job.id);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${job.original_name.split(".")[0]}-${job.scale}x.${job.output_format}`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal mengunduh hasil");
    } finally {
      setDownloadingId(null);
    }
  }

  const canDownload = (job: Job) =>
    job.status === "completed" && job.result_deleted_at === null;

  if (authStatus !== "authenticated" || !user) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-slate-950 text-slate-100">
        <p className="text-slate-400">Memeriksa sesi…</p>
      </main>
    );
  }

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
          href="/dashboard"
          className="rounded-full border border-white/10 bg-white/5 px-4 py-1.5 text-sm text-slate-300 transition-colors hover:bg-white/10"
        >
          ← Dashboard
        </Link>
      </nav>

      <section className="mx-auto max-w-5xl px-6 pt-10">
        <div className="mb-8 flex items-end justify-between gap-4">
          <div>
            <h1 className="text-3xl font-bold">Riwayat proses</h1>
            <p className="mt-2 text-slate-400">
              Hasil tersimpan maksimal 7 hari untuk akun gratis, lalu dihapus
              otomatis (UU PDP).
            </p>
          </div>
          <button
            type="button"
            onClick={load}
            className="rounded-lg border border-white/10 bg-white/5 px-4 py-2 text-sm text-slate-300 transition-colors hover:bg-white/10"
          >
            Segarkan
          </button>
        </div>

        {error && (
          <p className="mb-6 rounded-lg border border-rose-500/30 bg-rose-500/10 px-4 py-2 text-sm text-rose-300">
            {error}
          </p>
        )}

        {jobs === null ? (
          <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-12 text-center text-slate-400">
            <div className="mx-auto mb-3 size-8 animate-spin rounded-full border-2 border-indigo-400 border-t-transparent" />
            Memuat riwayat…
          </div>
        ) : jobs.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-white/15 bg-white/[0.03] p-12 text-center">
            <p className="text-slate-300">Belum ada riwayat proses.</p>
            <p className="mt-1 text-sm text-slate-500">
              Unggah gambar pertamamu untuk melihat hasilnya di sini.
            </p>
            <Link
              href="/dashboard"
              className="mt-5 inline-block rounded-xl bg-gradient-to-r from-indigo-500 to-fuchsia-500 px-5 py-2 text-sm font-semibold text-white transition-opacity hover:opacity-90"
            >
              Unggah gambar
            </Link>
          </div>
        ) : (
          <>
            <ul className="space-y-3">
              {jobs.map((job) => (
                <li
                  key={job.id}
                  className="flex flex-col gap-4 rounded-2xl border border-white/10 bg-white/[0.03] p-5 sm:flex-row sm:items-center"
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <p className="truncate font-medium text-slate-100">
                        {job.original_name}
                      </p>
                      <span
                        className={`shrink-0 rounded-full border px-2 py-0.5 text-xs ${STATUS_STYLE[job.status]}`}
                      >
                        {STATUS_LABEL[job.status]}
                      </span>
                    </div>
                    <p className="mt-1 text-xs text-slate-500">
                      {job.scale}× · {job.output_format.toUpperCase()}
                      {job.face_enhance ? " · restorasi wajah" : ""}
                      {job.denoise ? " · denoise" : ""}
                      {job.color_enhance ? " · pertegas warna" : ""} ·{" "}
                      {formatDate(job.created_at)}
                    </p>
                    {job.status === "failed" && job.error && (
                      <p className="mt-1 text-xs text-rose-300/80">{job.error}</p>
                    )}
                    {job.status === "completed" && job.result_deleted_at !== null && (
                      <p className="mt-1 text-xs text-amber-300/80">
                        Hasil sudah dihapus oleh retensi otomatis (7 hari).
                      </p>
                    )}
                  </div>

                  <button
                    type="button"
                    onClick={() => handleDownload(job)}
                    disabled={!canDownload(job) || downloadingId === job.id}
                    className={`shrink-0 rounded-lg px-4 py-2 text-sm font-medium transition-colors ${
                      canDownload(job)
                        ? "border border-emerald-400/30 bg-emerald-500/10 text-emerald-300 hover:bg-emerald-500/20"
                        : "cursor-not-allowed border border-white/10 bg-white/5 text-slate-500"
                    }`}
                  >
                    {downloadingId === job.id ? "Mengunduh…" : "Unduh ulang"}
                  </button>
                </li>
              ))}
            </ul>
            <p className="mt-4 text-xs text-slate-500">
              Menampilkan {jobs.length} dari {total} riwayat.
            </p>
            {jobs.length < total && (
              <button
                type="button"
                onClick={loadMore}
                disabled={loadingMore}
                className="mt-4 w-full rounded-xl border border-white/10 bg-white/5 py-3 text-sm font-medium text-slate-300 transition-colors hover:bg-white/10 disabled:opacity-50"
              >
                {loadingMore ? "Memuat…" : "Muat lebih banyak"}
              </button>
            )}
          </>
        )}
      </section>
    </main>
  );
}
