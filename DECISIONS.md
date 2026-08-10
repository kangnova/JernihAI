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
- **Validasi aktual (smoke test GPU pertama — Agustus 2026):** RTX 3060 12 GB (Vast.ai spot, Korea; `dph_total` aktual **$0,037/jam**) — warm inference 4x 1080p **10,38 s** (KPI NFR-01 < 15 s ✅); biaya sesi uji penuh (rent ~30 mnt + 5 iterasi + load model + encode WebP) **±$0,02**; estimasi biaya GPU per gambar ≈ **$0,0001 → ±Rp 1,8/gambar** (@ Rp 16.500/USD) — **cost model ±Rp 2–6/gambar (prd.md §12 / NFR-08) terkonfirmasi** ✅. Catatan: T4 tidak tersedia di market saat sesi → RTX 3060 sebagai pengganti (realita ketersediaan Vast.ai; fallback RunPod tetap relevan).

## ADR-002: Runtime Model (ONNX Runtime vs PyTorch 2.x)

- **Status:** `accepted` — Fase 1 memakai **PyTorch 2.x (official Real-ESRGAN repo)**; ONNX ditunda ke Fase 2/3, hanya bila portabilitas/throughput menjadi bottleneck.
- **Konteks:** Real-ESRGAN/GFPGAN dapat diexport ke ONNX (wajib dynamic axes + tiling + FP16) atau dijalankan langsung dengan PyTorch (`torch.compile`).
- **Temuan riset:**
  - Official `RealESRGANer` sudah menyediakan **tiling (`--tile`), FP16, dan denoise (DNI interpolasi `realesr-general-x4v3` + `-wdn`, flag `-dn`)** out-of-the-box → langsung memenuhi FR-03/FR-09. **Koreksi (FR-09):** restorasi wajah BUKAN parameter `face_enhance` di `RealESRGANer.enhance()` pada v0.3.0 — dilakukan via `GFPGANer` terpisah dengan `bg_upsampler` (pola inference_realesrgan.py; lihat ADR-007).
  - Export ONNX untuk RRDBNet tergolong lurus, tetapi tiling/padding harus diimplementasi ulang di sisi runtime (effort beberapa hari) — tidak sebanding untuk MVP solo dev.
  - VRAM dengan tiling 400–512 px: < 6–8 GB → aman di T4 16GB.
- **Keputusan:** pipeline Fase 1 = **PyTorch 2.x + RealESRGANer** (tiling + FP16 tetap wajib). Benchmark ONNX hanya jika dibutuhkan di Fase 2/3.
- **Konsekuensi:** image worker lebih besar (`pytorch/pytorch:2.x-cuda12.x` + `basicsr`/`facexlib`/`gfpgan`); butuh system deps OpenCV di container (`libgl1-mesa-glx`, `libglib2.0-0`); mock pipeline lokal (stub OpenCV resize) tidak berubah (PRD §12).
- **Validasi aktual:** runtime PyTorch + `RealESRGANer` (tiling + FP16) terverifikasi di GPU nyata — 10,38 s warm 4x 1080p, VRAM RTX 3060 12 GB cukup, output 7680×4320 tanpa OOM. Ditemukan incompatibility **basicsr 1.4.2 vs torchvision modern** (`functional_tensor` dihapus) → patch permanen `api/scripts/patch_basicsr.py` (dipanggil di Dockerfile.worker saat build, teruji unit: PATCHED/idempoten/SKIP).

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
- **Validasi aktual (GPU, Agustus 2026):** encode WebP q90 output 8K (7680×4320) = **0,48 MB** dalam 2,98 s di RTX 3060 — konsisten dengan benchmark Pillow (rasio ±15× vs PNG); konfirmasi default WebP aman untuk output 8K di pipeline produksi.

## ADR-005: Retensi Data & Consent Privasi (FR-07 / UU PDP)

- **Status:** `accepted` — Fase 1.
- **Konteks:** PRD FR-07 wajib auto-delete original ≤ 24 jam & hasil sesuai retensi, plus consent eksplisit (UU PDP No. 27/2022).
- **Keputusan:**
  - **Sweep retensi via Celery Beat** (`retention.purge_expired`) berjalan berkala (default 60 menit, env `RETENTION_PURGE_INTERVAL_MINUTES`); proses beat terpisah (service `beat` di docker-compose). Fungsi inti async & idempoten — dipanggil langsung di test tanpa Redis.
  - **Original** dihapus setelah `retention_original_hours` (24 jam) hanya untuk job status completed/failed; job queued/processing dilindungi agar tidak kehilangan file saat diproses.
  - **Hasil** dihapus setelah `retention_result_days` (7 hari) dihitung dari `finished_at`.
  - **Path dipertahankan untuk audit**; kolom `original_deleted_at`/`result_deleted_at` menandai penghapusan → endpoint download menjawab `410 Gone`.
  - **Guard path traversal**: file hanya dihapus bila berada di dalam `upload_dir`/`result_dir`.
  - **Consent eksplisit**: field `privacy_consent` wajib `True` di register (timestamp `privacy_consent_at` disimpan di User); endpoint `POST /auth/consent` untuk user Google OAuth (banner di dashboard); halaman `/privacy` memuat ringkasan kebijakan.
- **Konsekuensi:** butuh proses beat berjalan di produksi; retensi berlaku hanya saat sweep berjalan (interval default 60 mnt); kolom baru di DB (Alembic belum ada — catatan dev: `create_all` hanya membuat tabel baru, kolom baru perlu ALTER untuk DB existing).

## ADR-006: Reliabilitas Job (NFR-03) — Retry Otomatis & Stale-check

- **Status:** `accepted` — Fase 1.
- **Konteks:** NFR-03 wajib retry otomatis job gagal (max 2x) + timeout per job; tanpa itu job yang crash setelah commit `processing` stuck selamanya (kuota hangus, original tak pernah dihapus retensi → bocor disk).
- **Keputusan:**
  - **Retry otomatis (Celery):** task `enhance.process` memakai `max_retries=settings.job_max_retries` (2) + backoff eksponensial (`30 × 2^retries` detik). Percobaan ke-1 memproses job `queued`; percobaan retry (`retries>0`) memproses ulang job `failed` via `force_retry` di `process_job`. Kuota direfund (FR-06) **hanya di percobaan terakhir** (`refund_on_fail`), mencegah refund berlipat.
  - **Stale-check (beat):** `recover_stale_jobs` (task `jobs.recover_stale`, interval `STALE_CHECK_INTERVAL_MINUTES` default 15) menandai job `processing` dengan `updated_at` lebih tua dari `JOB_STALE_MINUTES` (30) menjadi `failed` + error jelas + refund kuota. Idempoten; hanya menyentuh status `processing`.
  - **Efek terhadap retensi (FR-07):** setelah stale-check menandai job `failed`, retensi berhak menghapus original-nya → kebocoran disk job stuck tertutup (retensi hanya menyentuh `completed`/`failed`).
- **Konsekuensi:** mode dev/test eager (`CELERY_TASK_ALWAYS_EAGER`) tidak punya retry (langsung `process_job` sekali) — perilaku ini disengaja; retry hanya aktif di worker + Redis. Stale-check butuh service beat berjalan (sama seperti retensi).

## ADR-007: Restorasi Wajah (FR-08) — GFPGAN via GFPGANer + bg_upsampler

- **Status:** `accepted` — Fase 2 (dikoreksi saat FR-09, Agustus 2026).
- **Konteks:** FR-08 wajib opsi restorasi wajah (GFPGAN) yang bisa di-toggle per job. **Koreksi penting:** `RealESRGANer.enhance()` realesrgan **v0.3.0 TIDAK punya parameter `face_enhance`** — implementasi awal (param `face_enhance=...` ke `upsampler.enhance`) akan crash TypeError di worker produksi. Perilaku sebenarnya di v0.3.0 (inference_realesrgan.py): face restore dipanggil via **`GFPGANer` terpisah** dengan `bg_upsampler=upsampler`.
- **Keputusan:**
  - **Toggle per job**: field `face_enhance` (bool, default False) di model/schema/route Job; form param `face_enhance` di `POST /api/v1/jobs`.
  - **`_get_face_enhancer(upsampler, outscale)`**: memuat `GFPGANer(model_path=gfpgan/weights/GFPGANv1.4.pth, upscale=outscale, arch='clean', channel_multiplier=2, bg_upsampler=upsampler)`, di-cache global per outscale (double-checked lock). Dipanggil hanya saat `face_enhance=True`; hasil `(_, _, restored_bgr)` dipakai langsung (paste_back=True).
  - **Kontrak RGB<->BGR**: RealESRGANer/GFPGANer v0.3.0 memakai numpy HWC **BGR** (konvensi OpenCV). Pipeline real mengonversi Pillow RGB → BGR sebelum inference dan BGR → RGB setelahnya. Kanal alpha di-upscale terpisah (LANCZOS) lalu disatukan kembali (model hanya menerima 3 kanal).
  - **Mock backend** mengabaikan flag dengan log warning (stub dev; jangan menyesatkan saat `ENHANCE_BACKEND=auto` jatuh ke mock).
  - **Weight GFPGANv1.4.pth** diunduh `scripts/download_models.py` ke `<cwd>/gfpgan/weights/` (path relatif hardcoded GFPGANer; di container CWD=/app). Dependensi `gfpgan`/`facexlib` sudah di extra `gpu` pyproject.
  - **Weight deteksi wajah facexlib (retinaface)** juga di-bake saat build (memuat `init_detection_model` sekali) — tanpa ini GFPGANer men-download via gdown/Google Drive SAAT RUNTIME, rentan rate-limit & gagal di tengah pipeline job.
  - **UI**: switch "Restorasi wajah (GFPGAN)" di dashboard, dikirim sebagai `face_enhance`; label hasil menampilkan "+ wajah".
- **Konsekuensi:** image worker makin besar (weight GFPGAN ~333 MB + retinaface + dep); inference face_enhance lebih lambat (GFPGAN dimuat sekali per proses, di-cache per outscale) — KPI NFR-01 diukur TANPA face_enhance; fallback bila weight tidak ada = job failed dengan error jelas.

## ADR-009: Denoise & Color Enhance (FR-09) — Toggle Pra-pemrosesan

- **Status:** `accepted` — Fase 2.
- **Konteks:** FR-09 wajib opsi denoise & color enhance sebagai toggle per job; denoise di PRD via model `realesr-general-x4v3` (flag `-dn` Real-ESRGAN).
- **Keputusan:**
  - **Toggle per job**: field `denoise` & `color_enhance` (bool, default False) di model/schema/route Job; form params `denoise` & `color_enhance` di `POST /api/v1/jobs`.
  - **Denoise (backend real)**: `_get_upsampler(denoise=True)` memuat `SRVGGNetCompact` (num_conv=32) dengan `model_path=[realesr-general-x4v3.pth, realesr-general-wdn-x4v3.pth]` + `dni_weight=[denoise_strength, 1 - denoise_strength]` (DNI interpolasi, pola inference_realesrgan.py v0.3.0 `-dn`). **Quirk upstream (terverifikasi dari source v0.3.0):** `utils.py::dni()` membobotkan `dni_weight[0]` ke model NORMAL (x4v3) dan `dni_weight[1]` ke versi wdn — jadi strength 0 = murni wdn (denoise terkuat), 1 = murni normal (terlemah), **kebalikan help text resmi**; default 0.5 = campuran seimbang (dokumentasi akurat di config.py). Kekuatan dikonfigurasi `DENOISE_STRENGTH` (default 0.5). Cache terpisah dari mode `x4plus` (RRDBNet) — dua model dimuat bila kedua mode dipakai.
  - **Color enhance (pra-pemrosesan)**: `_color_enhance()` murni Pillow (tanpa GPU) — saturasi (`ImageEnhance.Color`), kontras, brightness dengan `COLOR_ENHANCE_STRENGTH` (default 1.2; 1.0 = netral). Konsisten dipakai backend mock & real.
  - **GFPGANer di-cache per `(outscale, upsampler)`** — `bg_upsampler` ter-bake saat konstruksi, jadi job denoise vs non-denoise pada outscale sama tidak boleh berbagi instance (alias cache).
  - **Pemilihan backend auto aware-tujuan**: `_resolve_backend(denoise)` memeriksa model sesuai kebutuhan job — auto mode dengan denoise jatuh ke mock bila model `general` tidak ada (bukan memaksa x4plus).
  - **Mock backend** menerapkan efek ringan (MedianFilter 3×3 + `_color_enhance`) agar toggle terasa di dev; log warning untuk face_enhance tetap dipertahankan.
  - **download_models.py** menambah `realesr-general-x4v3.pth` + `realesr-general-wdn-x4v3.pth` (rilis v0.2.5.0) → ter-bake di image worker.
  - **UI**: switch "Denoise" & "Pertegas warna" di dashboard (grid 5 opsi); riwayat menampilkan badge opsinya.
- **Konsekuensi:** worker GPU menambah 2 weight (~10 MB total, kecil); denoise memakai model SRVGGNetCompact (lebih ringan dari RRDBNet); kolom DB baru `denoise`/`color_enhance` (butuh ALTER/create_all baru — Alembic belum ada).

## ADR-008: Riwayat Proses (FR-10) — List Endpoint & Halaman Riwayat

- **Status:** `accepted` — Fase 2.
- **Konteks:** FR-10 wajib daftar hasil user + unduh ulang selama masa retensi (7 hari free).
- **Keputusan:**
  - **Endpoint `GET /api/v1/jobs`** (list) — pagination `limit` (1–100, default 20) + `offset`, urut `created_at DESC`, HANYA job milik user yang login (`user_id` filter — tidak membocorkan job orang lain). Response `JobListOut {items, total}`.
  - **`JobOut` mengekspos `result_deleted_at`** — UI riwayat menonaktifkan tombol unduh ulang saat hasil sudah dihapus retensi (FR-07), menghindari 410 di sisi klien.
  - **Halaman `/history`** di web: daftar riwayat + badge status + unduh ulang (memakai endpoint download FR-05 yang sudah ada, dengan ownership check); state loading/empty/error. Link "Riwayat" di nav dashboard.
- **Konsekuensi:** tanpa Alembic, kolom tidak bertambah (murni endpoint + UI baru — tidak ada migrasi); retensi tetap jalan (riwayat otomatis berkurang saat hasil dihapus 7 hari).
