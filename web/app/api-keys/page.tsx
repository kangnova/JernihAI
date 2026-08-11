"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import type { B2bApiKey } from "@/lib/api";
import { createApiKey, listApiKeys, revokeApiKey } from "@/lib/api";
import { formatDate } from "@/lib/admin-ui";
import { useAuth } from "@/lib/auth";

export default function ApiKeysPage() {
  const { status, user } = useAuth();
  const router = useRouter();
  const [keys, setKeys] = useState<B2bApiKey[] | null>(null);
  const [name, setName] = useState("");
  const [tier, setTier] = useState<"free" | "pro">("free");
  const [newFullKey, setNewFullKey] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  const load = useCallback(async () => {
    const d = await listApiKeys();
    setKeys(d.items);
  }, []);

  useEffect(() => {
    if (status === "unauthenticated") {
      router.push("/login");
      return;
    }
    if (status !== "authenticated") return;
    setError(null);
    load().catch((err) =>
      setError(err instanceof Error ? err.message : "Gagal memuat API key"),
    );
  }, [status, router, load]);

  async function handleCreate() {
    setMsg(null);
    setError(null);
    setBusy(true);
    try {
      const res = await createApiKey({ name: name.trim(), tier });
      setNewFullKey(res.full_key);
      setCopied(false);
      setMsg(`API key "${res.key.name}" dibuat — salin sekarang!`);
      setName("");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal membuat API key");
    } finally {
      setBusy(false);
    }
  }

  async function handleRevoke(k: B2bApiKey) {
    if (!window.confirm(`Cabut API key "${k.name}" (${k.key_prefix})? Semua permintaan dengan key ini akan ditolak.`)) {
      return;
    }
    setBusy(true);
    try {
      await revokeApiKey(k.id);
      setMsg(`API key "${k.name}" dicabut.`);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal mencabut API key");
    } finally {
      setBusy(false);
    }
  }

  function copyFullKey() {
    if (!newFullKey) return;
    navigator.clipboard.writeText(newFullKey).then(() => setCopied(true));
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
      <nav className="mx-auto flex max-w-4xl items-center justify-between px-6 py-6">
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

      <section className="mx-auto max-w-4xl px-6 pt-10">
        <h1 className="text-3xl font-bold">API Publik (B2B)</h1>
        <p className="mt-2 text-slate-400">
          Integrasikan jernihkan gambar ke aplikasi Anda. 1 job = 1 kredit
          (FR-14).
        </p>

        {error && (
          <p className="mt-6 rounded-lg border border-rose-500/30 bg-rose-500/10 px-4 py-2 text-sm text-rose-300">
            {error}
          </p>
        )}
        {msg && !error && (
          <p className="mt-6 rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-4 py-2 text-sm text-emerald-300">
            {msg}
          </p>
        )}

        {/* Buat key baru */}
        <div className="mt-8 rounded-2xl border border-white/10 bg-white/[0.03] p-5">
          <p className="mb-1 text-sm font-medium text-slate-300">Buat API key</p>
          <p className="mb-4 text-xs text-slate-500">
            Key asli hanya ditampilkan SEKALI — simpan di tempat aman
            (mis. env var server Anda).
          </p>
          <div className="flex flex-col gap-3 sm:flex-row">
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Nama key, mis. Produksi / Staging"
              className="flex-1 rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-sm text-slate-200 placeholder:text-slate-600 focus:border-indigo-400 focus:outline-none"
            />
            <select
              value={tier}
              onChange={(e) => setTier(e.target.value as "free" | "pro")}
              className="rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-sm text-slate-200 focus:border-indigo-400 focus:outline-none"
            >
              <option value="free">Free — 20 req/menit</option>
              <option value="pro">Pro — 120 req/menit</option>
            </select>
            <button
              type="button"
              onClick={handleCreate}
              disabled={busy || !name.trim()}
              className="rounded-lg border border-indigo-400/30 bg-indigo-500/10 px-4 py-2 text-sm font-medium text-indigo-300 transition-colors hover:bg-indigo-500/20 disabled:opacity-40"
            >
              {busy ? "…" : "Buat key"}
            </button>
          </div>

          {newFullKey && (
            <div className="mt-4 rounded-xl border border-amber-400/30 bg-amber-400/10 p-4">
              <p className="text-sm font-medium text-amber-200">
                ⚠️ Key baru Anda (tidak akan ditampilkan lagi)
              </p>
              <div className="mt-2 flex items-center gap-2">
                <code className="min-w-0 flex-1 truncate rounded-lg bg-black/40 px-3 py-2 font-mono text-sm text-amber-100">
                  {newFullKey}
                </code>
                <button
                  type="button"
                  onClick={copyFullKey}
                  className="shrink-0 rounded-lg border border-amber-400/30 bg-amber-400/10 px-3 py-2 text-sm text-amber-200 transition-colors hover:bg-amber-400/20"
                >
                  {copied ? "✓ Tersalin" : "Salin"}
                </button>
              </div>
            </div>
          )}
        </div>

        {/* Daftar key */}
        <div className="mt-8">
          <h2 className="mb-3 text-lg font-semibold">
            Key saya ({keys?.length ?? 0})
          </h2>
          {keys === null ? (
            <p className="text-slate-400">Memuat…</p>
          ) : keys.length === 0 ? (
            <p className="text-slate-500">
              Belum ada API key. Buat satu di atas untuk mulai integrasi.
            </p>
          ) : (
            <ul className="space-y-2">
              {keys.map((k) => (
                <li
                  key={k.id}
                  className="flex flex-col gap-2 rounded-xl border border-white/10 bg-white/[0.03] px-4 py-3 text-sm sm:flex-row sm:items-center sm:justify-between"
                >
                  <div className="min-w-0">
                    <p className="truncate font-medium text-slate-200">
                      {k.name}
                      {!k.is_active && (
                        <span className="ml-2 rounded-full border border-rose-500/30 bg-rose-500/10 px-2 py-0.5 text-[10px] text-rose-300">
                          Dicabut
                        </span>
                      )}
                    </p>
                    <p className="font-mono text-xs text-slate-500">
                      {k.key_prefix}… · tier {k.tier} · dibuat{" "}
                      {formatDate(k.created_at)}
                      {k.last_used_at && ` · terakhir dipakai ${formatDate(k.last_used_at)}`}
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <span
                      className={`rounded-full border px-2 py-0.5 text-xs ${
                        k.is_active
                          ? "border-emerald-400/30 bg-emerald-400/10 text-emerald-300"
                          : "border-white/10 bg-white/5 text-slate-400"
                      }`}
                    >
                      {k.is_active ? "Aktif" : "Nonaktif"}
                    </span>
                    {k.is_active && (
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => handleRevoke(k)}
                        className="rounded-md border border-white/10 bg-white/5 px-2 py-1 text-xs text-slate-400 transition-colors hover:border-rose-500/40 hover:bg-rose-500/10 hover:text-rose-300 disabled:opacity-40"
                      >
                        Cabut
                      </button>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* Contoh penggunaan */}
        <div className="mt-8 rounded-2xl border border-white/10 bg-white/[0.03] p-5">
          <p className="mb-3 text-sm font-medium text-slate-300">
            Contoh integrasi (curl)
          </p>
          <pre className="overflow-x-auto rounded-lg bg-black/40 p-4 font-mono text-xs leading-relaxed text-emerald-200">
            {`# 1. Upload & mulai proses (1 kredit per gambar)\ncurl -X POST https://api.jernihai.id/api/v1/b2b/jobs \\\n  -H "X-API-Key: jn_…" \\\n  -F "file=@foto.jpg" -F "scale=2" -F "output_format=webp"\n\n# 2. Cek status (polling)\ncurl https://api.jernihai.id/api/v1/b2b/jobs/<id> \\\n  -H "X-API-Key: jn_…"\n\n# 3. Unduh hasil\ncurl -o hasil.webp https://api.jernihai.id/api/v1/b2b/jobs/<id>/result \\\n  -H "X-API-Key: jn_…"`}
          </pre>
        </div>
      </section>
    </main>
  );
}
