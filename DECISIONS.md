# DECISIONS — Log Keputusan Arsitektur (Fase 0)

Format: ADR ringan. Status: `proposed` (belum dieksekusi) / `accepted` / `superseded`.

## ADR-001: Pola Serving GPU (Long-running Worker vs Serverless)

- **Status:** `proposed` — WAJIB diputuskan di Fase 0 (lihat prd.md §9)
- **Konteks:** PRD memakai Celery (worker persisten) tetapi menyebut GPU serverless untuk produksi awal. Dua model ini berbeda: Celery butuh proses worker berjalan lama; serverless GPU (Replicate/RunPod Serverless) berbasis request.
- **Rekomendasi:** **long-running worker** (Vast.ai / RunPod Secure Cloud) + Celery untuk Fase 1 — mendukung retry, queue prioritas (paket Pro), model selalu warm (hindari cold start 20–60 dtk), dan kontrol biaya via pause instance.
- **Serverless** tetap jadi opsi untuk API B2B dan beban spike di Fase 3.

## ADR-002: Runtime Model (ONNX Runtime vs PyTorch 2.x)

- **Status:** `proposed`
- **Konteks:** Real-ESRGAN/GFPGAN dapat diexport ke ONNX (wajib dynamic axes + tiling + FP16 untuk input besar) atau dijalankan langsung dengan PyTorch (`torch.compile`).
- **Rekomendasi:** benchmark keduanya di GPU cloud (T4) memakai set uji internal sebelum Fase 1 lock-in. ONNX lebih portabel; PyTorch lebih cepat ke pasar.

## ADR-003: Auth (NextAuth vs Clerk)

- **Status:** `proposed`
- **Rekomendasi:** **NextAuth (Auth.js)** — self-host, tanpa biaya tambahan, kontrol penuh atas data pengguna (selaras UU PDP). Clerk dipertimbangkan bila butuh percepatan development.

## ADR-004: Format Output Default

- **Status:** `proposed`
- **Konteks:** Output 4x 1080p = 7680×4320 (8K); PNG bisa 50–100 MB (prd.md §10).
- **Rekomendasi:** **WebP/JPEG kualitas tinggi** sebagai default; PNG hanya atas permintaan; tetapkan batas resolusi output.
