import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Standalone output — dipakai Dockerfile (multi-stage) untuk produksi.
  output: "standalone",
  async rewrites() {
    // FR-01 (Google OAuth): teruskan /api/* ke backend.
    //
    // Callback Google (redirect_uri = <WEB_URL>/api/v1/auth/google/callback)
    // masuk ke origin web, lalu di-proxy ke backend di sini — web menjadi
    // origin tunggal untuk seluruh alur (tanpa CORS lintas origin).
    //
    // Aktif hanya bila API_REWRITE_TARGET di-set (lihat docker-compose.yml
    // service `web`). Local dev: export API_REWRITE_TARGET=http://localhost:8000
    // Bila tidak di-set, rewrites kosong → perilaku lama (browser memanggil
    // NEXT_PUBLIC_API_URL langsung, gateway nginx menangani /api/* sendiri).
    const target = process.env.API_REWRITE_TARGET;
    if (!target) return [];
    // Normalisasi trailing slash agar tidak menghasilkan `//api/...`.
    const base = target.replace(/\/$/, "");
    return [
      {
        source: "/api/:path*",
        destination: `${base}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
