import { HealthBadge } from "@/components/HealthBadge";
import Link from "next/link";

const services = [
  {
    name: "Web",
    tech: "Next.js 15 · Tailwind v4",
    port: ":3000",
    icon: "◈",
    desc: "Landing, upload, preview before-after",
  },
  {
    name: "API",
    tech: "FastAPI · SQLAlchemy async",
    port: ":8000",
    icon: "⚡",
    desc: "Auth, upload, job queue — docs di /docs",
  },
  {
    name: "Database",
    tech: "PostgreSQL 16",
    port: ":5432",
    icon: "▦",
    desc: "Users, jobs, credits, transactions",
  },
  {
    name: "Queue",
    tech: "Redis 7 · Celery",
    port: ":6379",
    icon: "⇄",
    desc: "Job GPU asinkron + retry (pool solo)",
  },
];

const stack = ["Next.js 15", "FastAPI", "PostgreSQL", "Redis", "Celery", "Docker"];

const roadmap = [
  { fase: "Fase 0", label: "Persiapan — repo, CI/CD, scaffolding ini", active: true },
  { fase: "Fase 1", label: "MVP — auth, upload, upscale 2x/4x, kuota gratis", active: false },
  { fase: "Fase 2", label: "Face restore, batch, kredit & subscription", active: false },
  { fase: "Fase 3", label: "Scale — API B2B, multi-GPU, fine-tune model", active: false },
];

export default function Home() {
  return (
    <main className="min-h-screen bg-slate-950 text-slate-100">
      <div className="pointer-events-none fixed inset-0 bg-[radial-gradient(ellipse_at_top,rgba(99,102,241,0.22),transparent_60%)]" />

      <nav className="relative mx-auto flex max-w-5xl items-center justify-between px-6 py-6">
        <div className="flex items-center gap-2 font-semibold tracking-tight">
          <span className="grid size-8 place-items-center rounded-lg bg-gradient-to-br from-indigo-500 to-fuchsia-500 text-sm font-bold text-white">
            J
          </span>
          JernihAI
        </div>
        <span className="rounded-full border border-indigo-400/30 bg-indigo-400/10 px-3 py-1 text-xs font-medium text-indigo-300">
          Fase 0 — Scaffold
        </span>
      </nav>

      <section className="relative mx-auto max-w-5xl px-6 pt-16 pb-12 text-center">
        <p className="text-sm font-medium uppercase tracking-widest text-indigo-300">
          Platform AI untuk foto Indonesia
        </p>
        <h1 className="mx-auto mt-4 max-w-3xl text-4xl font-bold tracking-tight text-balance sm:text-6xl">
          Peningkatan{" "}
          <span className="bg-gradient-to-r from-indigo-400 via-violet-400 to-fuchsia-400 bg-clip-text text-transparent">
            kualitas gambar
          </span>{" "}
          berbasis AI.
        </h1>
        <p className="mx-auto mt-6 max-w-xl text-slate-400">
          Upscale, denoise, dan restorasi wajah untuk foto keluarga, UMKM, dan kreator.
          Dibangun untuk pasar Indonesia — harga lokal, pembayaran lokal.
        </p>

        <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
          <HealthBadge />
          <Link
            href="/login"
            className="rounded-full bg-gradient-to-r from-indigo-500 to-fuchsia-500 px-5 py-2 text-sm font-semibold text-white transition-opacity hover:opacity-90"
          >
            Masuk
          </Link>
          {stack.map((t) => (
            <span
              key={t}
              className="rounded-full border border-white/10 bg-white/5 px-3 py-1 text-xs text-slate-300 transition-colors hover:border-indigo-400/40 hover:text-white"
            >
              {t}
            </span>
          ))}
        </div>
      </section>

      <section className="relative mx-auto max-w-5xl px-6 pb-16">
        <h2 className="mb-4 text-sm font-semibold uppercase tracking-widest text-slate-400">
          Layanan dev
        </h2>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {services.map((s) => (
            <article
              key={s.name}
              className="group rounded-2xl border border-white/10 bg-white/[0.03] p-5 transition-all hover:-translate-y-1 hover:border-indigo-400/40 hover:bg-white/[0.06]"
            >
              <div className="flex items-center justify-between">
                <span className="text-2xl">{s.icon}</span>
                <code className="text-xs text-slate-500">{s.port}</code>
              </div>
              <h3 className="mt-3 font-semibold">{s.name}</h3>
              <p className="text-xs text-indigo-300/80">{s.tech}</p>
              <p className="mt-2 text-sm text-slate-400">{s.desc}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="relative mx-auto max-w-5xl px-6 pb-20">
        <h2 className="mb-4 text-sm font-semibold uppercase tracking-widest text-slate-400">
          Roadmap
        </h2>
        <ol className="space-y-3">
          {roadmap.map((r) => (
            <li
              key={r.fase}
              className={`flex items-center gap-4 rounded-xl border p-4 transition-colors ${
                r.active
                  ? "border-indigo-400/40 bg-indigo-400/10"
                  : "border-white/10 bg-white/[0.03] opacity-70"
              }`}
            >
              <span
                className={`w-16 shrink-0 text-sm font-semibold ${
                  r.active ? "text-indigo-300" : "text-slate-400"
                }`}
              >
                {r.fase}
              </span>
              <span className="text-sm text-slate-300">{r.label}</span>
              {r.active && (
                <span className="ml-auto rounded-full bg-indigo-400/20 px-2 py-0.5 text-xs text-indigo-200">
                  sekarang
                </span>
              )}
            </li>
          ))}
        </ol>
      </section>

      <footer className="relative border-t border-white/5 py-8 text-center text-xs text-slate-500">
        <p>
          Baca: <span className="text-slate-400">prd.md</span> ·{" "}
          <span className="text-slate-400">DECISIONS.md</span> — API docs:{" "}
          <a
            href="http://localhost:8000/docs"
            className="text-indigo-300 transition-colors hover:text-indigo-200 hover:underline"
          >
            localhost:8000/docs
          </a>
        </p>
      </footer>
    </main>
  );
}
