"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import type { Job, JobStatus, QuotaInfo } from "@/lib/api";
import {
  createBatchJobs,
  createJob,
  fetchJobResult,
  getJob,
  getQuota,
} from "@/lib/api";
import { BeforeAfterSlider } from "@/components/BeforeAfterSlider";

const MAX_BYTES = 10 * 1024 * 1024; // 10 MB (sama dengan FR-02 di API)
const MAX_BATCH = 10; // FR-12: maksimal gambar per batch
const ALLOWED_TYPES = ["image/jpeg", "image/png", "image/webp"];
const POLL_INTERVAL_MS = 1500;

const STATUS_LABEL: Record<JobStatus, string> = {
  queued: "Dalam antrean…",
  processing: "Memproses…",
  completed: "Selesai",
  failed: "Gagal",
};

const STATUS_BADGE: Record<JobStatus, string> = {
  queued: "border-amber-400/30 bg-amber-400/10 text-amber-300",
  processing: "border-sky-400/30 bg-sky-400/10 text-sky-300",
  completed: "border-emerald-400/30 bg-emerald-400/10 text-emerald-300",
  failed: "border-rose-500/30 bg-rose-500/10 text-rose-300",
};

function ToggleCard({
  title,
  activeLabel,
  description,
  checked,
  onChange,
  activeClass,
  knobClass,
}: {
  title: string;
  activeLabel: string;
  description: string;
  checked: boolean;
  onChange: (v: boolean) => void;
  activeClass: string;
  knobClass: string;
}) {
  return (
    <div>
      <p className="mb-2 text-sm font-medium text-slate-300">{title}</p>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        onClick={() => onChange(!checked)}
        className={`flex w-full items-center justify-between rounded-lg border px-4 py-2 text-sm transition-colors ${
          checked
            ? activeClass
            : "border-white/10 bg-white/5 text-slate-300 hover:bg-white/10"
        }`}
      >
        <span>{checked ? activeLabel : "Nonaktif"}</span>
        <span
          className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
            checked ? knobClass : "bg-white/15"
          }`}
        >
          <span
            className={`inline-block size-4 transform rounded-full bg-white transition-transform ${
              checked ? "translate-x-4" : "translate-x-0.5"
            }`}
          />
        </span>
      </button>
      <p className="mt-1.5 text-xs text-slate-500">{description}</p>
    </div>
  );
}

export function JobUploader() {
  const [files, setFiles] = useState<File[]>([]);
  const [beforeUrl, setBeforeUrl] = useState<string | null>(null);
  const [scale, setScale] = useState(2);
  const [outputFormat, setOutputFormat] = useState<"webp" | "jpeg" | "png">(
    "webp",
  );
  const [faceEnhance, setFaceEnhance] = useState(false);
  const [denoise, setDenoise] = useState(false);
  const [colorEnhance, setColorEnhance] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [job, setJob] = useState<Job | null>(null); // alur 1 gambar
  const [batchJobs, setBatchJobs] = useState<Job[] | null>(null); // FR-12
  const [afterUrl, setAfterUrl] = useState<string | null>(null);
  // Dimensi asli vs hasil (dibaca dari blob) — bukti visual peningkatan.
  const [dims, setDims] = useState<{
    before: [number, number] | null;
    after: [number, number] | null;
  }>({ before: null, after: null });
  const [dragging, setDragging] = useState(false);
  const [quota, setQuota] = useState<QuotaInfo | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const isBatch = files.length > 1;

  // Kuota gratis hari ini (FR-06) — dimuat sekali saat mount, di-refresh
  // setelah upload sukses.
  useEffect(() => {
    getQuota()
      .then(setQuota)
      .catch(() => setQuota(null));
  }, []);

  // Buat object URL preview untuk 1 gambar; bersihkan saat ganti/lepas.
  useEffect(() => {
    if (files.length !== 1) {
      setBeforeUrl(null);
      return;
    }
    const url = URL.createObjectURL(files[0]);
    setBeforeUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [files]);

  // Ambil blob hasil → afterUrl. Dipakai saat job sudah selesai saat respons
  // tiba (mode eager dev: CELERY_TASK_ALWAYS_EAGER) maupun saat transisi
  // dari polling.
  const loadResult = useCallback((jobId: string) => {
    fetchJobResult(jobId)
      .then((blob) => setAfterUrl(URL.createObjectURL(blob)))
      .catch((err) =>
        setError(err instanceof Error ? err.message : "Gagal mengunduh hasil"),
      );
  }, []);

  // Polling status job tunggal sampai selesai, lalu ambil blob hasil.
  // Mode eager (dev lokal): respons upload SUDAH `completed` — hasil diambil
  // segera tanpa menunggu transisi status.
  useEffect(() => {
    if (!job) return;
    if (job.status === "failed") {
      // Job gagal -> kuota di-refund server; refresh badge biar akurat.
      getQuota()
        .then(setQuota)
        .catch(() => {});
      return;
    }
    if (job.status === "completed") {
      loadResult(job.id);
      return;
    }
    const timer = setInterval(async () => {
      try {
        const updated = await getJob(job.id);
        setJob(updated);
        if (updated.status === "completed") loadResult(updated.id);
        if (updated.status === "failed") {
          getQuota()
            .then(setQuota)
            .catch(() => {});
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "Gagal memeriksa status");
      }
    }, POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [job, loadResult]);

  // Baca dimensi asli & hasil dari blob (bukti resolusi berlipat).
  useEffect(() => {
    if (!beforeUrl || !afterUrl) return;
    const read = (url: string, key: "before" | "after") => {
      const img = new Image();
      img.onload = () =>
        setDims((d) => ({ ...d, [key]: [img.naturalWidth, img.naturalHeight] }));
      img.src = url;
    };
    read(beforeUrl, "before");
    read(afterUrl, "after");
  }, [beforeUrl, afterUrl]);

  // Polling status job batch (FR-12): refresh per job yang masih berjalan.
  useEffect(() => {
    if (
      !batchJobs ||
      batchJobs.every(
        (j) => j.status === "completed" || j.status === "failed",
      )
    ) {
      return;
    }
    const timer = setInterval(async () => {
      try {
        const pending = batchJobs.filter(
          (j) => j.status === "queued" || j.status === "processing",
        );
        const updated = await Promise.all(pending.map((j) => getJob(j.id)));
        setBatchJobs((prev) =>
          prev
            ? prev.map((j) => updated.find((u) => u.id === j.id) ?? j)
            : prev,
        );
      } catch (err) {
        setError(err instanceof Error ? err.message : "Gagal memeriksa status");
      }
    }, POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [batchJobs]);

  // Batch selesai semua: refresh kuota (job gagal di-refund server).
  useEffect(() => {
    if (
      batchJobs &&
      batchJobs.some((j) => j.status === "failed") &&
      batchJobs.every(
        (j) => j.status === "completed" || j.status === "failed",
      )
    ) {
      getQuota()
        .then(setQuota)
        .catch(() => {});
    }
  }, [batchJobs]);

  function validateFile(f: File): string | null {
    if (!ALLOWED_TYPES.includes(f.type)) {
      return "Format harus JPG, PNG, atau WebP";
    }
    if (f.size > MAX_BYTES) {
      return `Ukuran maksimal 10 MB (file ini ${(f.size / (1024 * 1024)).toFixed(1)} MB)`;
    }
    return null;
  }

  function handleFiles(list: FileList | null) {
    setError(null);
    setJob(null);
    setBatchJobs(null);
    setAfterUrl(null);
    if (!list || list.length === 0) return;
    if (list.length > MAX_BATCH) {
      setError(`Maksimal ${MAX_BATCH} gambar per batch — sisanya diabaikan.`);
    }
    const arr = Array.from(list).slice(0, MAX_BATCH);
    for (const f of arr) {
      const msg = validateFile(f);
      if (msg) {
        setError(msg);
        return;
      }
    }
    setFiles(arr);
  }

  function removeFile(index: number) {
    setFiles((prev) => prev.filter((_, i) => i !== index));
  }

  function resetAll() {
    setFiles([]);
    setJob(null);
    setBatchJobs(null);
    setAfterUrl(null);
    setDims({ before: null, after: null });
    setError(null);
  }

  async function handleUpload() {
    if (files.length === 0 || uploading) return;
    if (quota && quota.total_slots < files.length) {
      setError(
        `Slot tersisa ${quota.total_slots} — batch ini butuh ${files.length}. Kuota gratis reset besok (00:00 WIB), atau beli kredit di halaman Billing.`,
      );
      return;
    }
    setError(null);
    setUploading(true);
    setJob(null);
    setBatchJobs(null);
    setAfterUrl(null);
    const opts = { scale, outputFormat, faceEnhance, denoise, colorEnhance };
    try {
      if (files.length === 1) {
        const created = await createJob({ file: files[0], ...opts });
        setJob(created);
      } else {
        const data = await createBatchJobs({ files, ...opts });
        setBatchJobs(data.items);
      }
      // Refresh sisa kuota setelah upload berhasil.
      getQuota()
        .then(setQuota)
        .catch(() => {});
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload gagal");
    } finally {
      setUploading(false);
    }
  }

  const handleDownload = useCallback(() => {
    if (!job) return;
    fetchJobResult(job.id)
      .then((blob) => {
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `${job.original_name.split(".")[0]}-${job.scale}x.${job.output_format}`;
        a.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      })
      .catch(() => setError("Gagal mengunduh hasil"));
  }, [job]);

  async function handleBatchDownload(batchJob: Job) {
    try {
      const blob = await fetchJobResult(batchJob.id);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${batchJob.original_name.split(".")[0]}-${batchJob.scale}x.${batchJob.output_format}`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch {
      setError("Gagal mengunduh hasil");
    }
  }

  const isProcessing = job !== null && (job.status === "queued" || job.status === "processing");
  const batchPending = batchJobs?.some(
    (j) => j.status === "queued" || j.status === "processing",
  );

  return (
    <div className="space-y-6">
      {/* Kuota gratis (FR-06) + kredit berbayar (FR-11) */}
      {quota && (
        <div
          className={`flex items-center justify-between gap-4 rounded-xl border px-4 py-3 ${
            quota.total_slots === 0
              ? "border-rose-500/30 bg-rose-500/10"
              : "border-white/10 bg-white/[0.03]"
          }`}
        >
          <div>
            <p
              className={`text-sm font-medium ${
                quota.total_slots === 0 ? "text-rose-300" : "text-slate-200"
              }`}
            >
              {quota.total_slots === 0
                ? "Kuota gratis & kredit habis"
                : quota.remaining > 0
                  ? `Kuota gratis tersisa: ${quota.remaining} dari ${quota.limit} gambar`
                  : `Kuota gratis habis — memakai ${quota.credit_balance} kredit berbayar`}
            </p>
            <p className="text-xs text-slate-400">
              Reset kuota gratis otomatis 00:00 WIB
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-3">
            <div className="h-1.5 w-28 overflow-hidden rounded-full bg-white/10">
              <div
                className={`h-full rounded-full transition-all ${
                  quota.total_slots === 0
                    ? "bg-rose-500"
                    : quota.total_slots === 1
                      ? "bg-amber-400"
                      : "bg-emerald-400"
                }`}
                style={{
                  width: `${Math.min(100, (quota.remaining / quota.limit) * 100)}%`,
                }}
              />
            </div>
            {quota.remaining === 0 && (
              <Link
                href="/billing"
                className="shrink-0 rounded-full border border-indigo-400/30 bg-indigo-500/10 px-3 py-1.5 text-xs font-medium text-indigo-300 transition-colors hover:bg-indigo-500/20"
              >
                {quota.credit_balance > 0
                  ? `${quota.credit_balance} kredit · Beli lagi`
                  : "Beli kredit"}
              </Link>
            )}
          </div>
        </div>
      )}

      {/* Dropzone */}
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          handleFiles(e.dataTransfer.files);
        }}
        onClick={() => inputRef.current?.click()}
        className={`cursor-pointer rounded-2xl border-2 border-dashed p-8 text-center transition-colors ${
          dragging
            ? "border-indigo-400 bg-indigo-500/10"
            : "border-white/15 bg-white/[0.03] hover:border-indigo-400/60 hover:bg-white/[0.05]"
        }`}
      >
        <input
          ref={inputRef}
          type="file"
          multiple
          accept="image/jpeg,image/png,image/webp"
          className="hidden"
          onChange={(e) => handleFiles(e.target.files)}
        />
        {files.length === 0 ? (
          <div className="text-slate-400">
            <svg
              className="mx-auto mb-3 size-10 opacity-60"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth="1.5"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5m-13.5-9L12 3m0 0 4.5 4.5M12 3v13.5"
              />
            </svg>
            <p className="font-medium text-slate-200">
              Seret gambar ke sini, atau klik untuk memilih
            </p>
            <p className="mt-1 text-sm">
              JPG · PNG · WebP — maks 10 MB per gambar, hingga {MAX_BATCH}{" "}
              sekaligus (FR-12)
            </p>
          </div>
        ) : files.length === 1 ? (
          <div className="flex items-center justify-center gap-4">
            {/* Preview hanya dirender saat object URL siap (useEffect setelah
                render) — menghindari src="" yang memicu warning Next. */}
            {beforeUrl ? (
              <>
                {/* Blob URL lokal — tidak bisa dioptimasi next/image */}
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={beforeUrl}
                  alt="Pratinjau"
                  className="h-24 w-24 rounded-xl border border-white/10 object-cover"
                />
              </>
            ) : (
              <div
                aria-hidden
                className="grid h-24 w-24 place-items-center rounded-xl border border-white/10 bg-white/5 text-xs text-slate-500"
              >
                …
              </div>
            )}
            <div className="text-left">
              <p className="font-medium text-slate-100">{files[0].name}</p>
              <p className="text-sm text-slate-400">
                {(files[0].size / (1024 * 1024)).toFixed(2)} MB · klik untuk
                ganti
              </p>
            </div>
          </div>
        ) : (
          <div className="text-left">
            <p className="mb-3 text-center font-medium text-slate-200">
              {files.length} gambar dipilih — klik untuk tambah
            </p>
            <ul className="mx-auto max-w-md space-y-2">
              {files.map((f, i) => (
                <li
                  key={`${f.name}-${i}`}
                  className="flex items-center justify-between gap-3 rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-sm"
                >
                  <span className="truncate text-slate-200">{f.name}</span>
                  <span className="shrink-0 text-xs text-slate-500">
                    {(f.size / (1024 * 1024)).toFixed(2)} MB
                  </span>
                  <button
                    type="button"
                    aria-label={`Hapus ${f.name}`}
                    onClick={(e) => {
                      e.stopPropagation();
                      removeFile(i);
                    }}
                    className="shrink-0 rounded-md px-2 py-0.5 text-slate-400 transition-colors hover:bg-rose-500/20 hover:text-rose-300"
                  >
                    ✕
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {error && (
        <p className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-4 py-2 text-sm text-rose-300">
          {error}
        </p>
      )}

      {/* Opsi + tombol (saat belum ada job aktif) */}
      {!job && !batchJobs && files.length > 0 && (
        <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-5">
          <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
            <div>
              <p className="mb-2 text-sm font-medium text-slate-300">Skala</p>
              <div className="flex gap-2">
                {[2, 4].map((s) => (
                  <button
                    key={s}
                    type="button"
                    onClick={() => setScale(s)}
                    className={`flex-1 rounded-lg border px-4 py-2 text-sm font-medium transition-colors ${
                      scale === s
                        ? "border-indigo-400 bg-indigo-500/15 text-indigo-200"
                        : "border-white/10 bg-white/5 text-slate-300 hover:bg-white/10"
                    }`}
                  >
                    {s}×
                  </button>
                ))}
              </div>
            </div>
            <div>
              <p className="mb-2 text-sm font-medium text-slate-300">
                Format hasil
              </p>
              <div className="flex gap-2">
                {(["webp", "jpeg", "png"] as const).map((f) => (
                  <button
                    key={f}
                    type="button"
                    onClick={() => setOutputFormat(f)}
                    className={`flex-1 rounded-lg border px-4 py-2 text-sm font-medium uppercase transition-colors ${
                      outputFormat === f
                        ? "border-indigo-400 bg-indigo-500/15 text-indigo-200"
                        : "border-white/10 bg-white/5 text-slate-300 hover:bg-white/10"
                    }`}
                  >
                    {f}
                  </button>
                ))}
              </div>
            </div>
            <ToggleCard
              title="Restorasi wajah"
              activeLabel="GFPGAN"
              description="Perjelas wajah pada foto lama / potret"
              checked={faceEnhance}
              onChange={setFaceEnhance}
              activeClass="border-fuchsia-400 bg-fuchsia-500/15 text-fuchsia-200"
              knobClass="bg-fuchsia-500"
            />
            <ToggleCard
              title="Denoise"
              activeLabel="Aktif"
              description="Kurangi noise / grain (realesr-general-x4v3)"
              checked={denoise}
              onChange={setDenoise}
              activeClass="border-sky-400 bg-sky-500/15 text-sky-200"
              knobClass="bg-sky-500"
            />
            <ToggleCard
              title="Pertegas warna"
              activeLabel="Aktif"
              description="Pulihkan warna foto lama yang pudar"
              checked={colorEnhance}
              onChange={setColorEnhance}
              activeClass="border-amber-400 bg-amber-500/15 text-amber-200"
              knobClass="bg-amber-500"
            />
          </div>
          <button
            type="button"
            onClick={handleUpload}
            disabled={uploading || (quota !== null && quota.total_slots === 0)}
            className="mt-5 w-full rounded-xl bg-gradient-to-r from-indigo-500 to-fuchsia-500 py-3 text-sm font-semibold text-white transition-opacity hover:opacity-90 disabled:opacity-50"
          >
            {uploading
              ? "Mengunggah…"
              : quota !== null && quota.total_slots === 0
                ? "Kuota & kredit habis"
                : isBatch
                  ? `Tingkatkan ${files.length} gambar`
                  : "Tingkatkan gambar"}
          </button>
        </div>
      )}

      {/* Status proses — 1 gambar */}
      {isProcessing && (
        <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-6 text-center">
          <div className="mx-auto mb-3 size-8 animate-spin rounded-full border-2 border-indigo-400 border-t-transparent" />
          <p className="font-medium text-slate-100">
            {STATUS_LABEL[job.status]}
          </p>
          <p className="mt-1 text-sm text-slate-400">
            Meningkatkan {job.original_name} ke {job.scale}×
            {job.face_enhance ? " + restorasi wajah" : ""}
            {job.denoise ? " + denoise" : ""}
            {job.color_enhance ? " + pertegas warna" : ""}…
          </p>
        </div>
      )}

      {/* Gagal — 1 gambar */}
      {job?.status === "failed" && (
        <div className="rounded-2xl border border-rose-500/30 bg-rose-500/10 p-6 text-center">
          <p className="font-medium text-rose-300">Proses gagal</p>
          <p className="mt-1 text-sm text-rose-200/80">{job.error}</p>
          <button
            type="button"
            onClick={resetAll}
            className="mt-4 rounded-lg border border-white/10 bg-white/5 px-4 py-2 text-sm text-slate-200 hover:bg-white/10"
          >
            Coba gambar lain
          </button>
        </div>
      )}

      {/* Hasil — 1 gambar: before-after slider + download */}
      {job?.status === "completed" && beforeUrl && afterUrl && (
        <div className="space-y-4">
          <BeforeAfterSlider
            beforeUrl={beforeUrl}
            afterUrl={afterUrl}
            beforeLabel={
              dims.before
                ? `Sebelum · ${dims.before[0]}×${dims.before[1]}`
                : "Sebelum"
            }
            afterLabel={
              dims.after
                ? `Sesudah · ${dims.after[0]}×${dims.after[1]}`
                : "Sesudah"
            }
          />
          {/* Bukti peningkatan: resolusi berlipat sesuai skala (FR-03) */}
          <p className="text-center text-sm text-slate-400">
            {dims.before && dims.after
              ? `Resolusi: ${dims.before[0]}×${dims.before[1]} → ${dims.after[0]}×${dims.after[1]} (${job.scale}×, ${job.output_format.toUpperCase()})`
              : `Diperbesar ${job.scale}× — format ${job.output_format.toUpperCase()}`}
          </p>
          <div className="flex flex-col gap-3 sm:flex-row">
            <button
              type="button"
              onClick={handleDownload}
              className="flex-1 rounded-xl bg-gradient-to-r from-indigo-500 to-fuchsia-500 py-3 text-sm font-semibold text-white transition-opacity hover:opacity-90"
            >
              Unduh hasil (
              {job.output_format.toUpperCase()} {job.scale}×
              {job.face_enhance ? " + wajah" : ""}
              {job.denoise ? " + denoise" : ""}
              {job.color_enhance ? " + warna" : ""})
            </button>
            <button
              type="button"
              onClick={resetAll}
              className="rounded-xl border border-white/10 bg-white/5 px-6 py-3 text-sm font-medium text-slate-200 transition-colors hover:bg-white/10"
            >
              Gambar lain
            </button>
          </div>
        </div>
      )}

      {/* Hasil — batch (FR-12): daftar status + unduh per gambar */}
      {batchJobs && (
        <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-5">
          <div className="mb-4 flex items-center justify-between">
            <p className="font-medium text-slate-100">
              {batchJobs.length} gambar
              {batchPending ? " — memproses…" : " — selesai"}
            </p>
            {batchPending && (
              <div className="size-5 animate-spin rounded-full border-2 border-indigo-400 border-t-transparent" />
            )}
          </div>
          <ul className="space-y-2">
            {batchJobs.map((j) => (
              <li
                key={j.id}
                className="flex flex-col gap-3 rounded-xl border border-white/10 bg-white/5 p-3 sm:flex-row sm:items-center"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <p className="truncate text-sm font-medium text-slate-200">
                      {j.original_name}
                    </p>
                    <span
                      className={`shrink-0 rounded-full border px-2 py-0.5 text-xs ${STATUS_BADGE[j.status]}`}
                    >
                      {STATUS_LABEL[j.status]}
                    </span>
                  </div>
                  {j.status === "failed" && j.error && (
                    <p className="mt-1 truncate text-xs text-rose-300/80">
                      {j.error}
                    </p>
                  )}
                </div>
                {j.status === "completed" && j.result_deleted_at === null && (
                  <button
                    type="button"
                    onClick={() => handleBatchDownload(j)}
                    className="shrink-0 rounded-lg border border-emerald-400/30 bg-emerald-500/10 px-4 py-1.5 text-sm font-medium text-emerald-300 transition-colors hover:bg-emerald-500/20"
                  >
                    Unduh
                  </button>
                )}
              </li>
            ))}
          </ul>
          {!batchPending && (
            <button
              type="button"
              onClick={resetAll}
              className="mt-4 w-full rounded-xl border border-white/10 bg-white/5 py-3 text-sm font-medium text-slate-300 transition-colors hover:bg-white/10"
            >
              Proses gambar lain
            </button>
          )}
        </div>
      )}
    </div>
  );
}
