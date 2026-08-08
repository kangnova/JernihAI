"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { useAuth } from "@/lib/auth";
import { googleLoginUrl } from "@/lib/api";

interface AuthFormProps {
  mode: "login" | "register";
}

export function AuthForm({ mode }: AuthFormProps) {
  const { login, register } = useAuth();
  const router = useRouter();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const isLogin = mode === "login";

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      if (isLogin) {
        await login(email, password);
      } else {
        await register(name, email, password);
      }
      router.push("/dashboard");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Terjadi kesalahan");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="w-full max-w-sm rounded-2xl border border-white/10 bg-white/[0.03] p-8">
      <h1 className="text-xl font-semibold">
        {isLogin ? "Masuk" : "Daftar akun"}
      </h1>
      <p className="mt-1 text-sm text-slate-400">
        {isLogin
          ? "Selamat datang kembali di JernihAI"
          : "Gratis 3 gambar/hari untuk pemula"}
      </p>

      {error && (
        <p className="mt-4 rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-300">
          {error}
        </p>
      )}

      <form onSubmit={handleSubmit} className="mt-6 space-y-4">
        {!isLogin && (
          <div>
            <label htmlFor="name" className="mb-1 block text-sm text-slate-400">
              Nama
            </label>
            <input
              id="name"
              type="text"
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full rounded-lg border border-white/10 bg-slate-900 px-3 py-2 text-sm outline-none focus:border-indigo-400"
              placeholder="Nama kamu"
            />
          </div>
        )}
        <div>
          <label htmlFor="email" className="mb-1 block text-sm text-slate-400">
            Email
          </label>
          <input
            id="email"
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="w-full rounded-lg border border-white/10 bg-slate-900 px-3 py-2 text-sm outline-none focus:border-indigo-400"
            placeholder="nama@email.com"
          />
        </div>
        <div>
          <label htmlFor="password" className="mb-1 block text-sm text-slate-400">
            Password
          </label>
          <input
            id="password"
            type="password"
            required
            minLength={8}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full rounded-lg border border-white/10 bg-slate-900 px-3 py-2 text-sm outline-none focus:border-indigo-400"
            placeholder={isLogin ? "Password kamu" : "Minimal 8 karakter"}
          />
        </div>
        <button
          type="submit"
          disabled={loading}
          className="w-full rounded-lg bg-gradient-to-r from-indigo-500 to-fuchsia-500 py-2 text-sm font-semibold text-white transition-opacity hover:opacity-90 disabled:opacity-50"
        >
          {loading ? "Tunggu…" : isLogin ? "Masuk" : "Daftar"}
        </button>
      </form>

      <div className="my-5 flex items-center gap-3 text-xs text-slate-500">
        <span className="h-px flex-1 bg-white/10" />
        atau
        <span className="h-px flex-1 bg-white/10" />
      </div>

      <a
        href={googleLoginUrl()}
        className="flex w-full items-center justify-center gap-2 rounded-lg border border-white/10 bg-white/5 py-2 text-sm font-medium text-slate-200 transition-colors hover:bg-white/10"
      >
        Lanjut dengan Google
      </a>

      <p className="mt-6 text-center text-sm text-slate-400">
        {isLogin ? "Belum punya akun? " : "Sudah punya akun? "}
        <Link
          href={isLogin ? "/register" : "/login"}
          className="text-indigo-300 hover:underline"
        >
          {isLogin ? "Daftar" : "Masuk"}
        </Link>
      </p>
    </div>
  );
}
