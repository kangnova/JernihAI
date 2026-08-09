"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { JobUploader } from "@/components/JobUploader";
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
      <nav className="mx-auto flex max-w-5xl items-center justify-between px-6 py-6">
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

      <section className="mx-auto max-w-5xl px-6 pt-10">
        <div className="mb-8">
          <h1 className="text-3xl font-bold">Halo, {user.name ?? user.email} 👋</h1>
          <p className="mt-2 text-slate-400">
            Unggah gambar dan tingkatkan kualitasnya — gratis 3 gambar per
            hari.
          </p>
        </div>

        <JobUploader />
      </section>
    </main>
  );
}
