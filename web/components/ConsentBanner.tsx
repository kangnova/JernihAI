"use client";

import Link from "next/link";
import { useState } from "react";

interface ConsentBannerProps {
  onAccept: () => Promise<void>;
}

/**
 * FR-07 (UU PDP): banner consent privasi untuk user yang mendaftar lewat
 * Google OAuth (tanpa form register yang meminta centang persetujuan).
 * Idempoten: tombol menyembunyikan banner setelah diproses.
 */
export function ConsentBanner({ onAccept }: ConsentBannerProps) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleAccept() {
    setBusy(true);
    setError(null);
    try {
      await onAccept();
    } catch {
      setError("Gagal menyimpan persetujuan. Coba lagi.");
      setBusy(false);
    }
  }

  return (
    <div className="mb-6 rounded-xl border border-amber-400/30 bg-amber-400/10 p-4">
      <p className="text-sm font-medium text-amber-200">
        Setujui kebijakan privasi untuk menggunakan JernihAI
      </p>
      <p className="mt-1 text-xs leading-relaxed text-amber-200/80">
        Gambar asli dihapus otomatis dari server setelah 24 jam, dan hasil
        proses disimpan maksimal 7 hari. Data tidak dijual ke pihak ketiga.{" "}
        <Link
          href="/privacy"
          target="_blank"
          className="underline underline-offset-2 hover:text-amber-100"
        >
          Baca Kebijakan Privasi
        </Link>
      </p>
      {error && <p className="mt-2 text-xs text-rose-300">{error}</p>}
      <button
        type="button"
        onClick={handleAccept}
        disabled={busy}
        className="mt-3 rounded-lg bg-amber-400 px-4 py-1.5 text-xs font-semibold text-slate-950 transition-colors hover:bg-amber-300 disabled:opacity-50"
      >
        {busy ? "Menyimpan…" : "Saya setuju"}
      </button>
    </div>
  );
}
