# DECISIONS — Log Keputusan Arsitektur (Fase 0)

Format: ADR ringan. Status: `proposed` (belum dieksekusi) / `accepted` / `superseded`.

## ADR-001: Pola Serving GPU (Long-running Worker vs Serverless)

- **Status:** `accepted` — Fase 1 memakai **long-running worker (Vast.ai on-demand) + Celery**; serverless ditunda ke Fase 3.
- **Konteks:** PRD memakai Celery (worker persisten) tetapi menyebut GPU serverless untuk produksi awal. Dua model ini berbeda: Celery butuh proses worker berjalan lama; serverless GPU (Replicate/RunPod Serverless/Modal) berbasis request.
- **Data harga aktual (riset, 2026):**
  - Vast.ai: T4 $0,05–0,12/jam (median ~$0,05); RTX 4090 $0,13–0,47/jam (median ~$0,36); spot 30–50% lebih murah (hanya untuk eksperimen/benchmark).
  - RunPod: T4 community ~$0,20–0,25/jam, secure ~$0,39/jam; RTX 4090 community ~$0,34/jam, secure ~$0,69/jam.
  - Serverless: Modal T4 ~$0,59/jam (+$30/bln free credit); Replicate T4 ~$0,81/jam; cold start 2–60 dtk — berisiko melanggar KPI end-to-end < 60 dtk tanpa warm pool.
- **Keputusan:** **long-running worker Celery di Vast.ai on-demand (1× T4 atau RTX 4090)** — model selalu warm (tanpa cold start), retry & queue prioritas native (Celery/Redis), kontrol biaya via idle-timeout/auto-pause instance. Ini menurunkan cost model PRD §12 dari ±Rp 15–25 menjadi **±Rp 2–6/gambar**.
- **Konsekuensi:** pengelolaan instance manual (perlu runbook); ketersediaan Vast.ai fluktuatif → **RunPod Secure Cloud sebagai fallback**; spot hanya untuk benchmark. **Serverless (Modal/Replicate)** dievaluasi ulang di Fase 3 untuk API B2B & spike.

## ADR-002: Runtime Model (ONNX Runtime vs PyTorch 2.x)

- **Status:** `accepted` — Fase 1 memakai **PyTorch 2.x (official Real-ESRGAN repo)**; ONNX ditunda ke Fase 2/3, hanya bila portabilitas/throughput menjadi bottleneck.
- **Konteks:** Real-ESRGAN/GFPGAN dapat diexport ke ONNX (wajib dynamic axes + tiling + FP16) atau dijalankan langsung dengan PyTorch (`torch.compile`).
- **Temuan riset:**
  - Official `RealESRGANer` sudah menyediakan **tiling (`--tile`), FP16, alpha channel, integrasi GFPGAN face enhance (`--face_enhance`), dan denoise (`realesr-general-x4v3`, flag `-dn`)** out-of-the-box → langsung memenuhi FR-03/FR-08/FR-09.
  - Export ONNX untuk RRDBNet tergolong lurus, tetapi tiling/padding harus diimplementasi ulang di sisi runtime (effort beberapa hari) — tidak sebanding untuk MVP solo dev.
  - VRAM dengan tiling 400–512 px: < 6–8 GB → aman di T4 16GB.
- **Keputusan:** pipeline Fase 1 = **PyTorch 2.x + RealESRGANer** (tiling + FP16 tetap wajib). Benchmark ONNX hanya jika dibutuhkan di Fase 2/3.
- **Konsekuensi:** image worker lebih besar (`pytorch/pytorch:2.x-cuda12.x` + `basicsr`/`facexlib`/`gfpgan`); butuh system deps OpenCV di container (`libgl1-mesa-glx`, `libglib2.0-0`); mock pipeline lokal (stub OpenCV resize) tidak berubah (PRD §12).

## ADR-003: Auth (NextAuth vs Clerk)

- **Status:** `accepted` (Fase 1 — dipilih: **JWT di FastAPI + httpOnly cookie**)
- **Konteks:** Opsi awal adalah NextAuth/Clerk (frontend), tapi backend FastAPI adalah otoritas bisnis (kuota, riwayat, job) sehingga perlu mengenali user dari token yang ia validasi sendiri.
- **Keputusan:** FastAPI terbitkan **JWT HS256** yang ditaruh di **httpOnly cookie** (`SameSite=Lax`, `Secure` di produksi). Web (Next.js) cukup kirim request dengan `credentials: include` — satu sumber kebenaran, tanpa library auth tambahan di frontend.
- **Google OAuth:** alur klasik (redirect → callback → tukar code → set cookie) dihandle langsung oleh FastAPI via `httpx`.
- **Password:** bcrypt (bukan plaintext). Migrasi ke Clerk/NextAuth tetap dimungkinkan karena `provider` dicatat di model User.
- **Konsekuensi:** CORS harus `allow_credentials=true`; cookie `SameSite=Lax` cukup karena web & API berbagi origin domain di produksi (via Nginx gateway).

## ADR-004: Format Output Default

- **Status:** `accepted` — Fase 1: **WebP q90 default**; JPEG sebagai opsi kompatibilitas; PNG lossless hanya atas permintaan eksplisit.
- **Konteks:** Output 4x 1080p = 7680×4320 (8K); PNG lossless bisa 20–100 MB (prd.md §10).
- **Benchmark aktual (Pillow 12.3, output 4x 4096×3072 dari gambar uji `samples/pattern_1024.png`):**
  - PNG lossless: **10,4 MB** (encode 5,8s) — baseline
  - **WebP q90: 0,7 MB** (6,5s) → **±15× lebih kecil dari PNG**, kualitas visual praktis identik (NFR-07 kompatibel semua browser modern)
  - JPEG q90: 1,3 MB (0,2s — encode tercepat)
  - WebP q95: 1,4 MB; WebP q100: 1,9 MB; WebP lossless: 7,9 MB (44s — tidak layak, hindari)
  - 8K (7680×4320): PNG **20,1 MB** vs WebP q90 **1,6 MB** vs JPEG q90 2,7 MB
- **Keputusan:**
  - **Default: WebP q90** — rasio kualitas/ukuran terbaik; file kecil menurunkan biaya R2 & mempercepat FR-05 (download < 3 dtk).
  - **JPEG q92** sebagai opsi kompatibilitas (pas foto, upload marketplace, penerima format lama).
  - **PNG lossless** hanya atas permintaan eksplisit, dengan warning ukuran; **dibatasi ≤ 4096 px** sisi terpanjang (8K PNG tidak didukung default).
  - **Batas output:** maks 7680×4320 (8K) untuk format lossy; ditetapkan sebagai konstanta pipeline.
- **Konsekuensi:** worker menambah langkah encode WebP (beban CPU kecil di GPU instance; **jangan gunakan WebP lossless** — 6× lebih lambat); storage R2 tetap kecil selama retensi 7–30 hari.
