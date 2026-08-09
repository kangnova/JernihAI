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
| `docs/GUIDE_VAST_ACCOUNT.md` | Panduan lengkap: buat akun Vast.ai, isi saldo, SSH key, CLI, integrasi |

## Quickstart (Docker)

```bash
docker compose up --build
```

- Web: http://localhost:3000
- API docs (Swagger): http://localhost:8000/docs
- Gateway (opsional, sesuai arsitektur PRD §9): `docker compose --profile gateway up`

## Dev Lokal (tanpa Docker)

### API (Python 3.12)

```bash
cd api
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"     # Windows (git-bash)
# source .venv/bin/activate && pip install -e ".[dev]"  # Linux/macOS
cp .env.example .env
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
- **Deploy Real-ESRGAN ke GPU:** mulai dari [docs/GUIDE_VAST_ACCOUNT.md](./docs/GUIDE_VAST_ACCOUNT.md) (buat akun Vast.ai dari nol), lalu ikuti [docs/DEPLOY_VAST.md](./docs/DEPLOY_VAST.md) (build/push image worker, smoke test 4x 1080p, ukur KPI NFR-01).
