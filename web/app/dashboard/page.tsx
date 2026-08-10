"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import Link from "next/link";

import { ConsentBanner } from "@/components/ConsentBanner";
import { JobUploader } from "@/components/JobUploader";
import { useAuth } from "@/lib/auth";

export default function DashboardPage() {
  const { status, user, logout, grantConsent } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (status === "unauthenticated") router.push("/login");
  }, [status, router]);

  if (status !== "authenticated" || !user) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-slate-950 text-slate-100">
        <p className="text-slate-400">Memeriksa sesi…</p>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-slate-950 text-slate-100">
      <nav className="mx-auto flex max-w-5xl items-center justify-between px-6 py-6">
        <div className="flex items-center gap-2 font-semibold">
          <span className="grid size-8 place-items-center rounded-lg bg-gradient-to-br from-indigo-500 to-fuchsia-500 text-sm font-bold">
            J
          </span>
          JernihAI
        </div>
        <div className="flex items-center gap-2">
          <Link
            href="/history"
            className="rounded-full border border-white/10 bg-white/5 px-4 py-1.5 text-sm text-slate-300 transition-colors hover:bg-white/10"
          >
            Riwayat
          </Link>
          <Link
            href="/account"
            className="rounded-full border border-white/10 bg-white/5 px-4 py-1.5 text-sm text-slate-300 transition-colors hover:bg-white/10"
          >
            Akun
          </Link>
          {/* FR-11: top-up kredit berbayar (Midtrans Snap) */}
          <Link
            href="/billing"
            className="rounded-full border border-indigo-400/30 bg-indigo-500/10 px-4 py-1.5 text-sm text-indigo-300 transition-colors hover:bg-indigo-500/20"
          >
            Kredit
          </Link>
          {/* FR-13: link admin hanya untuk user dengan email di ADMIN_EMAILS */}
          {user.is_admin && (
            <Link
              href="/admin"
              className="rounded-full border border-white/10 bg-white/5 px-4 py-1.5 text-sm text-slate-300 transition-colors hover:bg-white/10"
            >
              Admin
            </Link>
          )}
          <button
            onClick={async () => {
              await logout();
              router.push("/");
            }}
            className="rounded-full border border-white/10 bg-white/5 px-4 py-1.5 text-sm text-slate-300 transition-colors hover:bg-white/10"
          >
            Keluar
          </button>
        </div>
      </nav>

      <section className="mx-auto max-w-5xl px-6 pt-10">
        <div className="mb-8">
          <h1 className="text-3xl font-bold">Halo, {user.name ?? user.email} 👋</h1>
          <p className="mt-2 text-slate-400">
            Unggah gambar dan tingkatkan kualitasnya — gratis 3 gambar per
            hari.
          </p>
        </div>

        {/* FR-07: user Google OAuth yang belum menegaskan consent privasi */}
        {user.privacy_consent_at === null && (
          <ConsentBanner onAccept={grantConsent} />
        )}

        <JobUploader />
      </section>
    </main>
  );
}
