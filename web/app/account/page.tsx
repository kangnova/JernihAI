"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { deleteAccount, exportAccountData } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export default function AccountPage() {
  const { status, user } = useAuth();
  const router = useRouter();
  const [busy, setBusy] = useState<"export" | "delete" | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function handleExport() {
    setBusy("export");
    setError(null);
    setNotice(null);
    try {
      const blob = await exportAccountData();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `jernihai-data-${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      setNotice("Data pribadi kamu sedang diunduh (JSON).");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal mengekspor data");
    } finally {
      setBusy(null);
    }
  }

  async function handleDelete() {
    setBusy("delete");
    setError(null);
    try {
      await deleteAccount();
      router.push("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal menghapus akun");
      setBusy(null);
    }
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
      <nav className="mx-auto flex max-w-3xl items-center justify-between px-6 py-6">
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

      <section className="mx-auto max-w-3xl px-6 pt-10">
        <h1 className="text-3xl font-bold">Data &amp; Privasi</h1>
        <p className="mt-2 text-sm text-slate-400">
          Sesuai UU PDP No. 27/2022, kamu berhak mengakses dan menghapus data
          pribadimu kapan saja.
        </p>

        {error && (
          <p className="mt-6 rounded-lg border border-rose-500/30 bg-rose-500/10 px-4 py-2 text-sm text-rose-300">
            {error}
          </p>
        )}
        {notice && (
          <p className="mt-6 rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-4 py-2 text-sm text-emerald-300">
            {notice}
          </p>
        )}

        {/* Ekspor data */}
        <div className="mt-8 rounded-2xl border border-white/10 bg-white/[0.03] p-6">
          <h2 className="text-lg font-semibold">Ekspor data pribadi</h2>
          <p className="mt-1 text-sm text-slate-400">
            Unduh salinan data kamu: profil, riwayat proses, dan metadata job.
            Gambar asli/hasil tunduk retensi otomatis (original 24 jam, hasil
            7 hari) dan tidak disertakan sebagai biner.
          </p>
          <button
            type="button"
            onClick={handleExport}
            disabled={busy !== null}
            className="mt-4 rounded-xl bg-gradient-to-r from-indigo-500 to-fuchsia-500 px-5 py-2.5 text-sm font-semibold text-white transition-opacity hover:opacity-90 disabled:opacity-50"
          >
            {busy === "export" ? "Menyiapkan…" : "Unduh data saya (JSON)"}
          </button>
        </div>

        {/* Hapus akun */}
        <div className="mt-6 rounded-2xl border border-rose-500/20 bg-rose-500/[0.04] p-6">
          <h2 className="text-lg font-semibold text-rose-200">Hapus akun</h2>
          <p className="mt-1 text-sm text-slate-400">
            Menghapus akun secara permanen: seluruh riwayat proses dan file
            di server ikut terhapus. Tindakan ini tidak bisa dibatalkan.
          </p>
          {!confirmDelete ? (
            <button
              type="button"
              onClick={() => setConfirmDelete(true)}
              disabled={busy !== null}
              className="mt-4 rounded-xl border border-rose-500/40 bg-rose-500/10 px-5 py-2.5 text-sm font-semibold text-rose-200 transition-colors hover:bg-rose-500/20 disabled:opacity-50"
            >
              Hapus akun saya…
            </button>
          ) : (
            <div className="mt-4 flex flex-wrap items-center gap-3">
              <p className="text-sm text-rose-300">
                Yakin? Semua data akan hilang permanen.
              </p>
              <button
                type="button"
                onClick={handleDelete}
                disabled={busy === "delete"}
                className="rounded-xl bg-rose-600 px-5 py-2.5 text-sm font-semibold text-white transition-opacity hover:opacity-90 disabled:opacity-50"
              >
                {busy === "delete" ? "Menghapus…" : "Ya, hapus permanen"}
              </button>
              <button
                type="button"
                onClick={() => setConfirmDelete(false)}
                disabled={busy !== null}
                className="rounded-xl border border-white/10 bg-white/5 px-5 py-2.5 text-sm text-slate-300 transition-colors hover:bg-white/10 disabled:opacity-50"
              >
                Batal
              </button>
            </div>
          )}
        </div>

        <div className="mt-8 rounded-2xl border border-white/10 bg-white/[0.02] p-6 text-sm text-slate-400">
          <h3 className="font-medium text-slate-300">Ringkasan kebijakan</h3>
          <ul className="mt-2 list-inside list-disc space-y-1">
            <li>Gambar asli dihapus otomatis setelah 24 jam.</li>
            <li>Hasil proses disimpan 7 hari (akun gratis), lalu dihapus.</li>
            <li>Data tidak pernah dijual ke pihak ketiga.</li>
            <li>
              Kebijakan lengkap:{" "}
              <Link href="/privacy" className="text-indigo-300 hover:underline">
                halaman privasi
              </Link>
              .
            </li>
          </ul>
        </div>
      </section>
    </main>
  );
}
