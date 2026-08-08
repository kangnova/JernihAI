# PRD — JernihAI: Platform Peningkatan Kualitas Gambar Berbasis AI

| | |
|---|---|
| **Nama Produk** | JernihAI (working title) |
| **Versi Dokumen** | 1.1 (revisi: review senior software engineer) |
| **Status** | Draft |
| **Penulis** | [Nama Anda] — Alumni Sistem Informasi |
| **Tanggal** | 2026 |
| **Stakeholder** | Founder, Developer, (calon) Investor |

---

## 1. Executive Summary

JernihAI adalah layanan berbasis web yang meningkatkan kualitas foto/gambar menjadi kualitas premium menggunakan model AI (super-resolution, denoising, face restoration, color enhancement). Produk ditujukan untuk pasar Indonesia dengan harga lokal, pembayaran lokal (QRIS/e-wallet), dan kepatuhan terhadap UU PDP. Model bisnis: **freemium + kredit + subscription**, dengan ekspansi ke **API B2B**.

## 2. Latar Belakang & Masalah

1. Banyak foto lama/beresolusi rendah (dokumen keluarga, arsip UMKM, foto produk) yang tidak layak pakai untuk kebutuhan digital modern.
2. Solusi existing (Remini, Topaz, dll) berharga valuta asing, mahal untuk pasar Indonesia, dan metode pembayarannya tidak lokal.
3. Tools gratis konvensional (interpolation/bicubic) menghasilkan gambar blur tanpa penambahan detail.
4. Belum ada pemain lokal dominan di niche "AI photo enhancement Indonesia".

**Peluang:** harga lokal + pembayaran lokal + bahasa Indonesia + niche restorasi foto lama.

## 3. Tujuan & Metrik Sukses (KPI)

| Tujuan | KPI | Target (6 bulan pertama) |
|---|---|---|
| Akuisisi pengguna | Registered users | 1.000 user |
| Monetisasi | Konversi free → paid | ≥ 3% |
| Retensi | Retention D7 | ≥ 20% |
| Kualitas layanan | Inference time (4x, 1080p, warm GPU) | < 15 detik |
| Kepuasan | Rating user (in-app, post-download) | ≥ 4.5 / 5 |
| Efisiensi | Gross margin per gambar | ≥ 60% |

> **Catatan KPI waktu:** "inference time" ≠ end-to-end. Target end-to-end (upload → download) < 60 detik; mitigasi cold start serverless (warm pool / keep-alive) — lihat NFR-01.

## 4. Target Pengguna & Persona

| Persona | Kebutuhan | Fitur Kunci |
|---|---|---|
| **Keluarga / umum (35–60 th)** | Restorasi foto lama keluarga | Face restoration, denoise, UI sederhana |
| **Penjual online / UMKM** | Foto produk tajam untuk marketplace | Upscale 4x, batch, color enhance |
| **Kreator konten / fotografer** | Output premium untuk klien | 4x upscale, format WebP/PNG, API |
| **Mahasiswa / profesional** | Perbaikan pas foto & portofolio | Upscale 2x, cepat & murah |

## 5. Scope

**In-Scope (MVP):** web app, auth, upload, upscale 2x/4x (Real-ESRGAN), before-after preview, download, kuota gratis, auto-delete otomatis (original 24 jam, hasil proses 7 hari — lihat FR-07/FR-10).
**Out-of-Scope (MVP):** mobile app native, video enhancement, API publik, editing manual (crop/filter), generative edit (prompt-based).

## 6. Kebutuhan Fungsional

| ID | Kebutuhan | Deskripsi | Prioritas | Acceptance Criteria |
|---|---|---|---|---|
| FR-01 | Registrasi & Login | Email + Google OAuth | Must | User dapat login < 10 detik; sesi aman (JWT) |
| FR-02 | Upload Gambar | JPG/PNG/WebP, maks 10 MB | Must | Upload sukses di koneksi 4G; validasi format & ukuran (cek magic bytes, bukan hanya ekstensi) |
| FR-03 | Proses Enhancement | Upscale 2x/4x via Real-ESRGAN | Must | Hasil tajam, tanpa artefak berat; status proses real-time (polling cukup untuk MVP) |
| FR-04 | Preview Before-After | Slider perbandingan | Must | Slider responsif di mobile |
| FR-05 | Download Hasil | PNG/JPG/WebP | Must | Download dimulai < 3 detik setelah proses selesai |
| FR-06 | Kuota Gratis | 3 gambar/hari untuk user free | Must | Kuota reset harian (WIB); notifikasi saat habis |
| FR-07 | Privasi | Auto-delete original 24 jam & hasil sesuai retensi + consent | Must | Original & metadata terhapus terjadwal (cron/R2 lifecycle) ≤ 24 jam; hasil proses terhapus sesuai retensi; log tanpa menyimpan gambar |
| FR-08 | Face Restoration | GFPGAN/CodeFormer (opsi toggle) | Should | Wajah lebih jelas tanpa distorsi identitas |
| FR-09 | Denoise & Color Enhance | Opsi pra-pemrosesan | Should | Noise berkurang; warna lebih hidup natural |
| FR-10 | Riwayat Proses | Daftar hasil user + unduh ulang (hasil disimpan 7 hari free / 30 hari paid) | Should | Riwayat tampil < 2 detik; unduh ulang valid selama masa retensi hasil |
| FR-11 | Pembayaran | Kredit & subscription via Midtrans/Xendit (QRIS, e-wallet, VA) | Should (Fase 2) | Transaksi sukses end-to-end di sandbox & production; webhook idempotent |
| FR-12 | Batch Processing | Multi-upload hingga 10 gambar | Could (Fase 2) | Queue memproses berurutan/paralel tanpa gagal |
| FR-13 | Admin Dashboard | Monitoring user, job, revenue | Should (Fase 2) | Metrik harian tersedia |
| FR-14 | API Publik (B2B) | REST API ber-autentikasi untuk developer | Could (Fase 3) | Dokumentasi OpenAPI lengkap; rate limit per tier |

## 7. User Flow (MVP)

```
Landing → Daftar/Login → Dashboard
→ Upload gambar → Pilih mode (2x/4x, [face restore])
→ Masuk queue → Status: memproses… (polling, MVP)
→ Preview before-after (slider)
→ Download / Simpan ke riwayat
→ (Kuota habis) → Upsell halaman kredit
```

## 8. Kebutuhan Non-Fungsional

| ID | Kategori | Kebutuhan |
|---|---|---|
| NFR-01 | Performa | Inference time 4x foto 1080p ≤ 15 detik (GPU T4/A10G, warm); end-to-end (upload→download) target < 60 detik; mitigasi cold start serverless (warm pool / keep-alive); upload feedback progresif |
| NFR-02 | Skalabilitas | Worker GPU horizontal-scale berbasis queue; target 100 job simultan di Fase 3 |
| NFR-03 | Reliabilitas | Availability 99.5%; retry otomatis job gagal (max 2x); timeout per job (mis. 120 detik) + heartbeat worker |
| NFR-04 | Keamanan | HTTPS everywhere; enkripsi at-rest (SSE); token auth; rate limiting; OWASP Top 10 |
| NFR-05 | Privasi | Kepatuhan **UU PDP No. 27/2022**: consent eksplisit, auto-delete, hak subjek data (akses/koreksi/hapus), tanpa penjualan data |
| NFR-06 | Usability | Mobile-first; Bahasa Indonesia default; Lighthouse Performance ≥ 90 |
| NFR-07 | Kompatibilitas | Chrome/Firefox/Safari/WebView Android versi 2 tahun terakhir; perangkat Android mid-range |
| NFR-08 | Observability | Logging terpusat, monitoring GPU & queue, alerting dasar (termasuk alert biaya cloud) |

## 9. Arsitektur & Tech Stack

```
[Client: Next.js + Tailwind + shadcn/ui]
│ HTTPS
[API Gateway: Nginx]
│
[Backend: FastAPI] ── [PostgreSQL]
│ [Redis: queue/cache]
[GPU Worker: Celery + ONNX Runtime]
├── Real-ESRGAN (2x/4x)
└── GFPGAN / CodeFormer
│
[Storage: Cloudflare R2 + CDN]
```

| Layer | Pilihan | Alasan |
|---|---|---|
| Frontend | Next.js 15, Tailwind, shadcn/ui, Zustand/React Query | SSR/SEO, dev cepat, UI modern |
| Backend | FastAPI (Python) | Ekosistem AI native, async |
| Queue | Celery + Redis | Job GPU asinkron & retry |
| Database | PostgreSQL | Reliabel; SQLite hanya untuk dev lokal |
| Storage | Cloudflare R2 + CDN | Bebas egress fee, cepat di Indonesia |
| AI Serving | ONNX Runtime (CUDA EP) — atau PyTorch 2.x + torch.compile (keputusan di Fase 0) | Inference cepat; tiling + FP16 wajib untuk input besar |
| Auth | NextAuth / Clerk | Implementasi cepat |
| Payment | Midtrans / Xendit | QRIS, e-wallet, VA (lokal) |
| Infra | Docker; dev lokal → GPU cloud (**long-running worker** Vast.ai/RunPod Secure Cloud + Celery, atau **serverless per-request** — keputusan arsitektur diselesaikan di Fase 0) → AWS/GCP saat scale | Biaya efisien bertahap |

> **Catatan arsitektur GPU:** Celery (worker persisten) dan serverless GPU (request-based) adalah model yang berbeda — jangan dicampur. Keputusan wajib dibuat di Fase 0. GPU worker harus menggunakan pool `solo` (bukan prefork) karena CUDA context.

## 10. Kebutuhan Model AI

| Model | Fungsi | Format | Catatan |
|---|---|---|---|
| Real-ESRGAN (x2/x4) | Super-resolution utama | ONNX | Default untuk foto umum |
| GFPGAN / CodeFormer | Restorasi wajah | ONNX | Opsi toggle |
| (Opsional) Denoise/Color | Pra-pemrosesan | ONNX/OpenCV | Ringan, jalan di CPU |

- **Target kualitas:** tajam natural, tanpa artefak wajah mengerikan (uncanny); evaluasi visual + metrik (PSNR/SSIM, plus metrik no-reference NIQE/BRISQUE karena foto real tanpa ground truth) pada set uji internal.
- **Pipeline wajib untuk input besar:** export ONNX dengan dynamic axes; tiling + overlap padding (10–32 px) untuk mencegah OOM di GPU (1080p 4x → output 8K melebihi VRAM T4 16GB); FP16; pertimbangkan batas output resolusi + format default WebP/JPEG (PNG 8K bisa 50–100 MB).
- **Fallback:** bila GPU utama down, rute ke provider API pihak ketiga (Replicate/WaveSpeed).
- **Dev lokal:** CPU laptop dev **tidak mendukung AVX2** → ONNX Runtime resmi tidak dapat berjalan lokal; gunakan mock pipeline (stub OpenCV resize) di laptop, uji model di Google Colab (lihat §12).

## 11. Monetisasi

| Paket | Harga | Isi |
|---|---|---|
| Free | Rp 0 | 3 gambar/hari, maks 2x |
| Kredit | Rp 10.000 | 20 kredit (1 kredit = 1 gambar 4x) |
| Lite | Rp 29.000/bln | 100 kredit + 4x |
| Pro | Rp 79.000/bln | 500 kredit + batch + prioritas queue |
| B2B API | Custom | Pay-per-call, SLA |

## 12. Constraint & Asumsi Pengembangan

- **Perangkat developer:** HP Notebook, AMD A8-7410 (4 core ~2.2GHz), RAM 12GB, GPU integrated Radeon R5, Windows 10.
  - **Implikasi kritis:** CPU A8-7410 **tidak mendukung AVX2**, sedangkan wheel resmi `onnxruntime` (Windows) **wajib AVX2** dan akan crash (illegal instruction) saat memuat model. **Semua inference ML dilarang berjalan di laptop ini** — termasuk model INT8/quantized.
  - **Strategi:** laptop hanya untuk frontend/backend + unit test dengan *mock pipeline* (stub OpenCV resize); semua uji & prototyping model di **Google Colab**; integrasi end-to-end dan production di **GPU cloud** (keputusan arsitektur GPU diselesaikan di Fase 0 — lihat §9).
- **Cost model per gambar (dasar KPI gross margin ≥ 60%):** GPU T4 serverless ≈ $0,5–0,8/jam; 1080p 4x (tiling, FP16) ≈ 4–10 detik inference → **±Rp 15–25/gambar** biaya GPU. Revenue efektif ±Rp 500/gambar (paket kredit) → **gross margin ±95%**; target 60% aman, tetapi diverifikasi di Fase 1 dengan pengukuran aktual (GPU-hour + cold start + R2 egress).
- Budget awal terbatas → prioritas layanan dengan free tier / pay-per-use.
- Asumsi: user memiliki koneksi mobile minimal 4G; server region Singapore (latensi rendah ke Indonesia).

## 13. Roadmap

| Fase | Durasi | Deliverable |
|---|---|---|
| **Fase 0 – Persiapan** | Minggu 0–1 | Repo, CI/CD, desain UI, setup env dev, set uji gambar, **keputusan arsitektur GPU (worker vs serverless) + baseline cost model** |
| **Fase 1 – MVP** | Minggu 1–6 | Auth, upload, upscale 2x/4x, preview, download, kuota gratis, auto-delete, deploy (VPS + GPU cloud) |
| **Fase 2 – Monetize & Enhance** | Minggu 7–12 | Face restore, denoise/color, batch, kredit & subscription (Midtrans), riwayat, admin dashboard |
| **Fase 3 – Scale** | Bulan 4–6 | Multi-GPU autoscale, mobile PWA/app, API publik B2B, fine-tune model niche (foto lama/dokumen), analytics |

## 14. Risiko & Mitigasi

| Risiko | Dampak | Mitigasi |
|---|---|---|
| Biaya GPU membengkak | Tinggi | Serverless GPU pay-per-use, queue + limit ukuran, model quantized, **alert biaya cloud + cost model aktual (lihat §12)** |
| Kompetitor global (Remini dkk) | Tinggi | Harga lokal, pembayaran lokal, niche restorasi foto lama, bahasa Indonesia |
| Kepatuhan privasi (UU PDP) | Tinggi | Consent eksplisit, auto-delete, kebijakan privasi tertulis, hak subjek data |
| Kualitas model tidak konsisten | Sedang | Set uji internal (PSNR/SSIM + NIQE/BRISQUE), evaluasi berkala, opsi fallback provider, rencana fine-tune |
| Perangkat dev terbatas | Sedang | Strategi dev tanpa inference lokal (mock pipeline + Colab + cloud production — lihat §12) |
| Latensi upload user mobile | Sedang | Kompresi client-side sebelum upload, CDN, progres upload |

## 15. Appendix

- **Glossarium:** Super-resolution, Upscale, GAN, ONNX, Queue, Kredit, Egress fee.
- **Referensi:** Real-ESRGAN (github.com/xinntao/Real-ESRGAN), GFPGAN, CodeFormer, ONNX Runtime, UU No. 27/2022 (PDP), dokumentasi Midtrans/Xendit, Cloudflare R2.

---
*Dokumen ini living document — diperbarui setiap akhir fase.*