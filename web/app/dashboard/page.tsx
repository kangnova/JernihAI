"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { useAuth } from "@/lib/auth";

export default function DashboardPage() {
  const { status, user, logout } = useAuth();
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
      <nav className="mx-auto flex max-w-4xl items-center justify-between px-6 py-6">
        <div className="flex items-center gap-2 font-semibold">
          <span className="grid size-8 place-items-center rounded-lg bg-gradient-to-br from-indigo-500 to-fuchsia-500 text-sm font-bold">
            J
          </span>
          JernihAI
        </div>
        <button
          onClick={async () => {
            await logout();
            router.push("/");
          }}
          className="rounded-full border border-white/10 bg-white/5 px-4 py-1.5 text-sm text-slate-300 transition-colors hover:bg-white/10"
        >
          Keluar
        </button>
      </nav>

      <section className="mx-auto max-w-4xl px-6 pt-12 text-center">
        <h1 className="text-3xl font-bold">Halo, {user.name ?? user.email} 👋</h1>
        <p className="mt-3 text-slate-400">
          Dashboard ini masih kosong — fitur upload & peningkatan gambar
          menyusul di milestone berikutnya Fase 1.
        </p>
        <div className="mx-auto mt-8 max-w-md rounded-2xl border border-white/10 bg-white/[0.03] p-6 text-left">
          <h2 className="text-sm font-semibold uppercase tracking-widest text-slate-400">
            Akun kamu
          </h2>
          <dl className="mt-4 space-y-3 text-sm">
            <div className="flex justify-between">
              <dt className="text-slate-400">Email</dt>
              <dd>{user.email}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-slate-400">Nama</dt>
              <dd>{user.name ?? "—"}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-slate-400">Login via</dt>
              <dd className="capitalize">{user.provider}</dd>
            </div>
          </dl>
        </div>
      </section>
    </main>
  );
}
