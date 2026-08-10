"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import type {
  BillingPackage,
  BillingTransaction,
} from "@/lib/api";
import {
  createCheckout,
  getBillingPackages,
  listBillingTransactions,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";

// Midtrans Snap: sandbox vs production endpoint.
const SNAP_SRC = (isProd: boolean) =>
  isProd
    ? "https://app.midtrans.com/snap/snap.js"
    : "https://app.sandbox.midtrans.com/snap/snap.js";

function formatIdr(n: number): string {
  return `Rp${n.toLocaleString("id-ID")}`;
}

function formatDate(iso: string | null): string {
  if (!iso) return "—";
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

const TXN_LABEL: Record<string, string> = {
  pending: "Menunggu pembayaran",
  paid: "Lunas",
  failed: "Gagal",
  expired: "Kedaluwarsa",
};

declare global {
  interface Window {
    snap?: { pay: (token: string) => void };
  }
}

function loadSnapScript(): Promise<void> {
  return new Promise((resolve, reject) => {
    if (window.snap) return resolve();
    const s = document.createElement("script");
    s.src = SNAP_SRC(process.env.NEXT_PUBLIC_MIDTRANS_PRODUCTION === "true");
    s.async = true;
    s.onload = () => resolve();
    s.onerror = () => reject(new Error("Gagal memuat Snap Midtrans"));
    document.body.appendChild(s);
  });
}

export default function BillingPage() {
  const { status, user } = useAuth();
  const router = useRouter();
  const [packages, setPackages] = useState<BillingPackage[] | null>(null);
  const [creditBalance, setCreditBalance] = useState(0);
  const [transactions, setTransactions] = useState<BillingTransaction[]>([]);
  const [busySlug, setBusySlug] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [pkg, txn] = await Promise.all([
        getBillingPackages(),
        listBillingTransactions(),
      ]);
      setPackages(pkg.packages);
      setCreditBalance(pkg.credit_balance);
      setTransactions(txn.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal memuat billing");
    }
  }, []);

  useEffect(() => {
    if (status === "unauthenticated") {
      router.push("/login");
      return;
    }
    if (status === "authenticated") load();
  }, [status, router, load]);

  async function handleBuy(pkg: BillingPackage) {
    setBusySlug(pkg.slug);
    setError(null);
    try {
      const checkout = await createCheckout(pkg.slug);
      await loadSnapScript();
      if (!window.snap) throw new Error("Snap belum siap");
      window.snap.pay(checkout.snap_token);
      // Saldo hanya berubah setelah notifikasi webhook; segarkan saat kembali.
      setTimeout(load, 8000);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal memulai pembayaran");
    } finally {
      setBusySlug(null);
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
        <h1 className="text-3xl font-bold">Kredit &amp; Langganan</h1>
        <p className="mt-2 text-slate-400">
          1 kredit = 1 gambar. Kredit dipakai otomatis saat kuota gratis habis.
        </p>

        {/* Saldo */}
        <div className="mt-6 rounded-2xl border border-white/10 bg-white/[0.03] p-6">
          <p className="text-sm text-slate-400">Saldo kredit</p>
          <p className="mt-1 text-4xl font-bold text-indigo-300">
            {creditBalance}
            <span className="ml-2 text-lg font-medium text-slate-400">kredit</span>
          </p>
        </div>

        {error && (
          <p className="mt-6 rounded-lg border border-rose-500/30 bg-rose-500/10 px-4 py-2 text-sm text-rose-300">
            {error}
          </p>
        )}

        {/* Paket */}
        <h2 className="mt-10 text-lg font-semibold">Pilih paket</h2>
        <div className="mt-4 grid gap-4 sm:grid-cols-3">
          {packages === null ? (
            <p className="text-slate-400">Memuat paket…</p>
          ) : (
            packages.map((pkg) => (
              <div
                key={pkg.slug}
                className="flex flex-col rounded-2xl border border-white/10 bg-white/[0.03] p-5"
              >
                <p className="text-2xl font-bold text-slate-100">
                  {pkg.credits}{" "}
                  <span className="text-sm font-medium text-slate-400">
                    kredit
                  </span>
                </p>
                <p className="mt-1 text-lg text-slate-300">
                  {formatIdr(pkg.price_idr)}
                </p>
                <button
                  type="button"
                  onClick={() => handleBuy(pkg)}
                  disabled={busySlug !== null}
                  className="mt-4 rounded-xl bg-gradient-to-r from-indigo-500 to-fuchsia-500 py-2.5 text-sm font-semibold text-white transition-opacity hover:opacity-90 disabled:opacity-50"
                >
                  {busySlug === pkg.slug ? "Menyiapkan…" : "Beli"}
                </button>
                <p className="mt-2 text-xs text-slate-500">
                  QRIS · e-wallet · Virtual Account
                </p>
              </div>
            ))
          )}
        </div>

        {/* Riwayat transaksi */}
        <h2 className="mt-10 text-lg font-semibold">Riwayat transaksi</h2>
        <div className="mt-4 rounded-2xl border border-white/10 bg-white/[0.03] p-5">
          {transactions.length === 0 ? (
            <p className="text-sm text-slate-500">Belum ada transaksi.</p>
          ) : (
            <ul className="space-y-3">
              {transactions.map((t) => (
                <li
                  key={t.id}
                  className="flex items-center justify-between gap-4 text-sm"
                >
                  <div>
                    <p className="text-slate-200">
                      {t.credits} kredit · {formatIdr(t.amount_idr)}
                    </p>
                    <p className="text-xs text-slate-500">
                      {t.package_slug} · {formatDate(t.created_at)}
                    </p>
                  </div>
                  <span
                    className={`shrink-0 rounded-full border px-2 py-0.5 text-xs ${
                      t.status === "paid"
                        ? "border-emerald-400/30 bg-emerald-400/10 text-emerald-300"
                        : t.status === "pending"
                          ? "border-amber-400/30 bg-amber-400/10 text-amber-300"
                          : "border-rose-500/30 bg-rose-500/10 text-rose-300"
                    }`}
                  >
                    {TXN_LABEL[t.status] ?? t.status}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>

        <p className="mt-8 text-xs text-slate-500">
          Pembayaran diproses oleh Midtrans (QRIS, e-wallet, dan Virtual
          Account). Transaksi di lingkungan sandbox tidak memotong saldo
          sungguhan.
        </p>
      </section>
    </main>
  );
}
