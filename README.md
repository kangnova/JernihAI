# JernihAI — Peningkatan Kualitas Gambar Berbasis AI

Monorepo platform web untuk peningkatan kualitas foto/gambar (super-resolution, denoising, face restoration). Detail produk & persyaratan: **[prd.md](./prd.md)**.

## Struktur Repo

| Path | Deskripsi |
|---|---|
| `web/` | Frontend — Next.js 15 (App Router) + Tailwind CSS v4 |
| `api/` | Backend — FastAPI + SQLAlchemy async + Celery (stub) |
| `infra/nginx/` | Reverse proxy Nginx (opsional, profile `gateway`) |
| `scripts/` | Util dev — generator set uji gambar (stdlib-only) |
| `samples/` | Set uji gambar sintetis (hasil generate script) |
| `DECISIONS.md` | Log keputusan arsitektur Fase 0 (ADR ringan) |
| `docs/DEPLOY_VAST.md` | Runbook deploy & smoke test worker GPU di Vast.ai |
| `docs/DEPLOY_PRODUKSI.md` | Runbook deploy full-stack ke produksi + aktivasi pembayaran Midtrans (FR-11) & admin (FR-13) |
| `docs/GUIDE_VAST_ACCOUNT.md` | Panduan lengkap: buat akun Vast.ai, isi saldo, SSH key, CLI, integrasi |
| `docs/RUNBOOK_GPU_PERTAMA.md` | Checklist langkah konkret: sewa GPU pertama (rent → smoke test → destroy) |

## Retensi Data & Privasi (FR-07 / UU PDP)

- Gambar asli dihapus otomatis **setelah 24 jam**; hasil proses setelah **7 hari** (ADR-005).
- Sweep retensi dijalankan **Celery Beat** (service `beat`):
  `docker compose up -d beat worker` (interval default 60 menit, env `RETENTION_PURGE_INTERVAL_MINUTES`).
- Consent privasi wajib saat daftar (checkbox + halaman `/privacy`); user Google OAuth mengonfirmasi lewat banner di dashboard.

## Reliabilitas Job (NFR-03)

- **Retry otomatis**: job gagal dicoba ulang maksimal 2× (backoff eksponensial) di worker Celery; kuota hanya dipotong pada percobaan terakhir.
- **Stale-check**: job yang tersangkut di status `processing` > 30 menit (env `JOB_STALE_MINUTES`) otomatis ditandai `failed` + kuota direfund oleh beat (interval `STALE_CHECK_INTERVAL_MINUTES`, default 15) — ini juga membuka jalan retensi FR-07 untuk menghapus original-nya (anti bocor disk).

## Restorasi Wajah (FR-08)

- Opsi **GFPGAN** per upload (switch di dashboard) dikirim sebagai `face_enhance` ke `POST /api/v1/jobs`.
- Backend real memakai **`GFPGANer` terpisah** dengan `bg_upsampler` (RealESRGANer v0.3.0 **tidak** punya param `face_enhance` di `enhance()`); konversi RGB↔BGR + upscale kanal alpha ditangani pipeline.
- Mock backend mengabaikan flag dengan log warning (dev lokal).
- Weight `GFPGANv1.4.pth` + deteksi wajah retinaface diunduh otomatis oleh `python api/scripts/download_models.py` dan ter-bake di `Dockerfile.worker`.

## Denoise & Pertegas Warna (FR-09)

- Toggle **Denoise** & **Pertegas warna** per upload (dikirim sebagai `denoise` / `color_enhance` ke `POST /api/v1/jobs`).
- Denoise memakai model `realesr-general-x4v3` + `realesr-general-wdn-x4v3` (DNI interpolasi, flag `-dn` Real-ESRGAN) — kekuatan diatur `DENOISE_STRENGTH`.
- Pertegas warna = pra-pemrosesan Pillow (saturasi/kontras/brightness, `COLOR_ENHANCE_STRENGTH`); backend mock menerapkan efek ringan yang setara.
- Kedua weight `general` diunduh otomatis oleh `python api/scripts/download_models.py` dan ter-bake di `Dockerfile.worker`.

## Riwayat Proses (FR-10)

- Endpoint `GET /api/v1/jobs` (list riwayat user, pagination `limit`/`offset`, urut terbaru) — hanya job milik user.
- Halaman `/history` di web (link dari dashboard): badge status, info proses, **unduh ulang** selama hasil masih tersimpan (7 hari free). Tombol dinonaktifkan otomatis saat hasil sudah dihapus retensi.

## Kredit & Pembayaran (FR-11 — Midtrans Snap)

- **Model kredit**: 1 kredit = 1 gambar; kuota gratis (FR-06) dipakai lebih dulu, kredit otomatis menyusul saat habis. Job berbayar yang gagal **di-refund otomatis** ke saldo.
- **Paket** dikonfigurasi via env `BILLING_PACKAGES` (default: 20 kredit Rp10k / 100 kredit Rp29k / 500 kredit Rp79k).
- **Alur**: halaman `/billing` → pilih paket → modal **Midtrans Snap** (QRIS · e-wallet · Virtual Account) → webhook `POST /api/v1/billing/webhook` (signature SHA512 diverifikasi, **idempotent** per `order_id`) → kredit masuk ke saldo.
- **Mode MOCK (dev)**: tanpa `MIDTRANS_SERVER_KEY`/`MIDTRANS_CLIENT_KEY`, checkout menghasilkan token Snap mock (sandbox tetap bisa diisi key dari dashboard.sandbox.midtrans.com).

## API Publik B2B (FR-14)

- Developer membuat **API key** di halaman `/api-keys` (link **API** di dashboard); key asli ditampilkan **sekali** — yang disimpan hanya hash SHA-256 + prefix.
- Panggil endpoint dengan header `X-API-Key`; **1 job = 1 kredit** (pay-per-call, dari saldo pemilik key; habis → `402`).
- Endpoint: `POST /api/v1/b2b/jobs` (upload + mulai proses) · `GET /api/v1/b2b/jobs/{id}` (status) · `GET /api/v1/b2b/jobs/{id}/result` (unduh hasil) · `GET /api/v1/b2b/quota` (sisa kredit + tier).
- **Rate limit per menit per key** berdasarkan tier (env `API_RATE_LIMIT_FREE_PER_MINUTE` default 20 / `API_RATE_LIMIT_PRO_PER_MINUTE` default 120).
- Job B2B yang gagal **di-refund otomatis 1 kredit** (sama dengan alur kredit FR-11); job tercatat di riwayat pemilik.
- **Smoke test E2E mode produksi** (broker nyata, bukan eager):
  `cd api && .venv/Scripts/python scripts/smoke_test_b2b.py [--base-url https://api.example.id]`
  — alur lengkap register/login → buat key → upload → polling → unduh → potongan kredit → cabut key (403).

## Migrasi Database (ADR-011)

- Skema dikelola **Alembic** (`api/migrations/`). Saat menambah kolom/tabel,
  buat migrasi baru (bukan `create_all`):
  ```bash
  cd api
  # Autogenerate (hanya draft — review & rapikan dulu, terutama server_default)
  .venv/Scripts/python -m alembic revision --autogenerate -m "deskripsi"
  .venv/Scripts/python -m alembic upgrade head
  ```
- **Docker**: service `api` otomatis menjalankan `alembic upgrade head`
  sebelum start — DB lama di-upgrade **tanpa `docker compose down -v`**
  (migrasi awal punya guard tabel + backfill kolom untuk DB era `create_all`).
- **Manual di VPS**: `docker compose run --rm api alembic upgrade head`.

## Quickstart (Docker)

```bash
docker compose up --build
```

- Web: http://localhost:3000
- API docs (Swagger): http://localhost:8000/docs
- Migrasi DB dijalankan otomatis saat `api` start (`alembic upgrade head`).
- Gateway (opsional, sesuai arsitektur PRD §9): `docker compose --profile gateway up`

## Dev Lokal (tanpa Docker)

### API (Python 3.12)

```bash
cd api
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"     # Windows (git-bash)
# source .venv/bin/activate && pip install -e ".[dev]"  # Linux/macOS
cp .env.example .env
# Skema DB dikelola Alembic (bukan create_all runtime) — jalankan dulu:
.venv/Scripts/python -m alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

### Web (Node 20+)

```bash
cd web
npm install
npm run dev
```

## Test & Lint

```bash
# API
cd api && .venv/Scripts/python -m pytest && .venv/Scripts/python -m ruff check .

# Web
cd web && npm run lint && npx tsc --noEmit
```

## Catatan Penting (Constraint Perangkat Dev — PRD §12)

- Laptop dev (AMD A8-7410, **tanpa AVX2**) **tidak bisa menjalankan ONNX Runtime**. Semua inference ML dilarang berjalan lokal — gunakan **Google Colab** untuk uji model dan **GPU cloud** untuk produksi.
- Worker GPU (Celery + model) hanya dijalankan di mesin ber-GPU; container `worker` memakai pool `solo` (aman untuk CUDA context).
- Set uji gambar sintetis: `python scripts/make_test_images.py` (tanpa dependensi ML, aman di laptop).
- **Deploy Real-ESRGAN ke GPU:** mulai dari [docs/GUIDE_VAST_ACCOUNT.md](./docs/GUIDE_VAST_ACCOUNT.md) (buat akun Vast.ai dari nol), lalu ikuti [docs/RUNBOOK_GPU_PERTAMA.md](./docs/RUNBOOK_GPU_PERTAMA.md) (checklist sewa GPU pertama), dengan detail teknis di [docs/DEPLOY_VAST.md](./docs/DEPLOY_VAST.md) (build/push image worker, smoke test 4x 1080p, ukur KPI NFR-01).
