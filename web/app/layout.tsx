import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "JernihAI — Peningkatan Kualitas Gambar Berbasis AI",
  description:
    "Platform peningkatan kualitas foto/gambar berbasis AI untuk pasar Indonesia.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="id">
      <body>{children}</body>
    </html>
  );
}
