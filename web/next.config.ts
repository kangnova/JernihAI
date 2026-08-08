import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Standalone output — dipakai Dockerfile (multi-stage) untuk produksi.
  output: "standalone",
};

export default nextConfig;
