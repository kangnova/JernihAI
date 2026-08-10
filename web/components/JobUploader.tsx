"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import type { Job, JobStatus, QuotaInfo } from "@/lib/api";
import { createJob, fetchJobResult, getJob, getQuota } from "@/lib/api";
import { BeforeAfterSlider } from "@/components/BeforeAfterSlider";

const MAX_BYTES = 10 * 1024 * 1024; // 10 MB (sama dengan FR-02 di API)
const ALLOWED_TYPES = ["image/jpeg", "image/png", "image/webp"];
const POLL_INTERVAL_MS = 1500;

const STATUS_LABEL: Record<JobStatus, string> = {
  queued: "Dalam antrean…",
  processing: "Memproses…",
  completed: "Selesai",
  failed: "Gagal",
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
  const [file, setFile] = useState<File | null>(null);
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
  const [job, setJob] = useState<Job | null>(null);
  const [afterUrl, setAfterUrl] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [quota, setQuota] = useState<QuotaInfo | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Kuota gratis hari ini (FR-06) — dimuat sekali saat mount, di-refresh
  // setelah upload sukses.
  useEffect(() => {
    getQuota()
      .then(setQuota)
      .catch(() => setQuota(null));
  }, []);

  // Buat object URL untuk preview asli; bersihkan saat ganti/lepas.
  useEffect(() => {
    if (!file) {
      setBeforeUrl(null);
      return;
    }
    const url = URL.createObjectURL(file);
    setBeforeUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  // Polling status job sampai selesai, lalu ambil blob hasil.
  useEffect(() => {
    if (!job || job.status === "completed" || job.status === "failed") return;
    const timer = setInterval(async () => {
      try {
        const updated = await getJob(job.id);
        setJob(updated);
        if (updated.status === "completed") {
          const blob = await fetchJobResult(updated.id);
          setAfterUrl(URL.createObjectURL(blob));
        }
        if (updated.status === "failed") {
          // Job gagal -> kuota di-refund server; refresh badge biar akurat.
          getQuota()
            .then(setQuota)
            .catch(() => {});
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "Gagal memeriksa status");
      }
    }, POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [job]);

  function validateFile(f: File): string | null {
    if (!ALLOWED_TYPES.includes(f.type)) {
      return "Format harus JPG, PNG, atau WebP";
    }
    if (f.size > MAX_BYTES) {
      return `Ukuran maksimal 10 MB (file ini ${(f.size / (1024 * 1024)).toFixed(1)} MB)`;
    }
    return null;
  }

  function handleFiles(files: FileList | null) {
    setError(null);
    setJob(null);
    setAfterUrl(null);
    const f = files?.[0];
    if (!f) return;
    const msg = validateFile(f);
    if (msg) {
      setError(msg);
      return;
    }
    setFile(f);
  }

  async function handleUpload() {
    if (!file || uploading) return;
    if (quota?.remaining === 0) {
      setError("Kuota gratis hari ini sudah habis — reset besok (00:00 WIB).");
      return;
    }
    setError(null);
    setUploading(true);
    setJob(null);
    setAfterUrl(null);
    try {
      const created = await createJob({
        file,
        scale,
        outputFormat,
        faceEnhance,
        denoise,
        colorEnhance,
      });
      setJob(created);
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

  const isProcessing = job !== null && (job.status === "queued" || job.status === "processing");

  return (
    <div className="space-y-6">
      {/* Kuota gratis (FR-06) */}
      {quota && (
        <div
          className={`flex items-center justify-between gap-4 rounded-xl border px-4 py-3 ${
            quota.remaining === 0
              ? "border-rose-500/30 bg-rose-500/10"
              : "border-white/10 bg-white/[0.03]"
          }`}
        >
          <div>
            <p
              className={`text-sm font-medium ${
                quota.remaining === 0 ? "text-rose-300" : "text-slate-200"
              }`}
            >
              {quota.remaining === 0
                ? "Kuota gratis hari ini habis"
                : `Kuota gratis tersisa: ${quota.remaining} dari ${quota.limit} gambar`}
            </p>
            <p className="text-xs text-slate-400">Reset otomatis 00:00 WIB</p>
          </div>
          <div className="h-1.5 w-28 shrink-0 overflow-hidden rounded-full bg-white/10">
            <div
              className={`h-full rounded-full transition-all ${
                quota.remaining === 0
                  ? "bg-rose-500"
                  : quota.remaining === 1
                    ? "bg-amber-400"
                    : "bg-emerald-400"
              }`}
              style={{ width: `${Math.min(100, (quota.remaining / quota.limit) * 100)}%` }}
            />
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
          accept="image/jpeg,image/png,image/webp"
          className="hidden"
          onChange={(e) => handleFiles(e.target.files)}
        />
        {beforeUrl ? (
          <div className="flex items-center justify-center gap-4">
            {/* Blob URL lokal — tidak bisa dioptimasi next/image */}
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={beforeUrl}
              alt="Pratinjau"
              className="h-24 w-24 rounded-xl border border-white/10 object-cover"
            />
            <div className="text-left">
              <p className="font-medium text-slate-100">{file?.name}</p>
              <p className="text-sm text-slate-400">
                {(file ? file.size / (1024 * 1024) : 0).toFixed(2)} MB · klik
                untuk ganti
              </p>
            </div>
          </div>
        ) : (
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
            <p className="mt-1 text-sm">JPG · PNG · WebP — maks 10 MB</p>
          </div>
        )}
      </div>

      {error && (
        <p className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-4 py-2 text-sm text-rose-300">
          {error}
        </p>
      )}

      {/* Opsi + tombol (saat belum ada job aktif) */}
      {!job && file && (
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
            disabled={uploading || quota?.remaining === 0}
            className="mt-5 w-full rounded-xl bg-gradient-to-r from-indigo-500 to-fuchsia-500 py-3 text-sm font-semibold text-white transition-opacity hover:opacity-90 disabled:opacity-50"
          >
            {uploading
              ? "Mengunggah…"
              : quota?.remaining === 0
                ? "Kuota hari ini habis"
                : "Tingkatkan gambar"}
          </button>
        </div>
      )}

      {/* Status proses */}
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

      {/* Gagal */}
      {job?.status === "failed" && (
        <div className="rounded-2xl border border-rose-500/30 bg-rose-500/10 p-6 text-center">
          <p className="font-medium text-rose-300">Proses gagal</p>
          <p className="mt-1 text-sm text-rose-200/80">{job.error}</p>
          <button
            type="button"
            onClick={() => {
              setJob(null);
              setFile(null);
              setAfterUrl(null);
            }}
            className="mt-4 rounded-lg border border-white/10 bg-white/5 px-4 py-2 text-sm text-slate-200 hover:bg-white/10"
          >
            Coba gambar lain
          </button>
        </div>
      )}

      {/* Hasil: before-after slider + download */}
      {job?.status === "completed" && beforeUrl && afterUrl && (
        <div className="space-y-4">
          <BeforeAfterSlider beforeUrl={beforeUrl} afterUrl={afterUrl} />
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
              onClick={() => {
                setJob(null);
                setFile(null);
                setAfterUrl(null);
              }}
              className="rounded-xl border border-white/10 bg-white/5 px-6 py-3 text-sm font-medium text-slate-200 transition-colors hover:bg-white/10"
            >
              Gambar lain
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
