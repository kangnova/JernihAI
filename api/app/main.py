"""JernihAI API — entry point FastAPI.

Arsitektur: lihat prd.md §9 dan DECISIONS.md di root repo.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import models  # noqa: F401  (registrasi model ke Base.metadata)
from app.api.routes.account import router as account_router
from app.api.routes.admin import router as admin_router
from app.api.routes.auth import router as auth_router
from app.api.routes.b2b import router as b2b_router
from app.api.routes.billing import router as billing_router
from app.api.routes.health import router as health_router
from app.api.routes.jobs import router as jobs_router
from app.api.routes.quota import router as quota_router
from app.core.config import settings
from app.db.session import engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Skema DB dikelola Alembic (ADR-011) — `alembic upgrade head` dijalankan
    # otomatis oleh entrypoint api (docker-compose) atau manual saat deploy.
    # `Base.metadata.create_all` TIDAK dipakai di runtime: tidak bisa
    # menghapus kolom & tidak ada versioning. Test memanggil create_all
    # eksplisit per-fixture (SQLite in-memory), jadi aman dihapus.
    # Hardening (fail-fast) sudah dilakukan oleh validator Settings saat
    # import; di sini hanya warning non-fatal untuk gap produksi.
    settings.log_production_warnings()
    yield
    await engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=(
            "API backend JernihAI — platform peningkatan kualitas gambar.\n\n"
            "Dokumentasi API Publik B2B (FR-14) untuk developer: `docs/API_B2B.md`. "
            "Spesifikasi OpenAPI lengkap: `docs/api/openapi.yaml` (regenerate via "
            "`python api/scripts/export_openapi.py`)."
        ),
        openapi_tags=[
            {
                "name": "b2b",
                "description": (
                    "**API Publik B2B (FR-14)** untuk developer. Autentikasi via header "
                    "`X-API-Key` (buat di halaman web **/api-keys**); 1 job = 1 kredit "
                    "dari saldo pemilik key (pay-per-call, tanpa saldo -> 402). Rate limit "
                    "per menit per tier: **free 20 / pro 120** (NFR-04)."
                ),
            },
        ],
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health_router, prefix=settings.api_v1_prefix)
    app.include_router(auth_router, prefix=settings.api_v1_prefix)
    app.include_router(billing_router, prefix=settings.api_v1_prefix)
    app.include_router(jobs_router, prefix=settings.api_v1_prefix)
    app.include_router(quota_router, prefix=settings.api_v1_prefix)
    app.include_router(account_router, prefix=settings.api_v1_prefix)
    app.include_router(admin_router, prefix=settings.api_v1_prefix)
    app.include_router(b2b_router, prefix=settings.api_v1_prefix)

    return app


app = create_app()
