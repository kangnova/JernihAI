"""Konfigurasi aplikasi via environment variables (pydantic-settings)."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "JernihAI API"
    app_version: str = "0.1.0"
    environment: str = "development"
    api_v1_prefix: str = "/api/v1"

    # List origin CORS — di .env ditulis sebagai string JSON:
    #   CORS_ORIGINS=["http://localhost:3000"]
    cors_origins: list[str] = ["http://localhost:3000"]

    # Default koneksi lokal; di Docker Compose di-override via environment.
    database_url: str = "postgresql+asyncpg://jernihai:jernihai@localhost:5432/jernihai"
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # --- Auth (JWT) ---
    # Secret untuk dev lokal; WAJIB diganti env kuat di produksi.
    jwt_secret: str = "dev-secret-ganti-di-produksi-0123456789abcdef"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24 * 7  # 7 hari
    cookie_name: str = "jernihai_session"
    cookie_secure: bool = False  # True di produksi (HTTPS)

    # --- Google OAuth ---
    google_client_id: str = ""
    google_client_secret: str = ""
    # Redirect tujuan setelah Google login sukses/gagal di web.
    web_url: str = "http://localhost:3000"

    # --- Upload & storage (Fase 1 — FR-02/FR-07) ---
    # Folder relatif terhadap root repo; di Docker menjadi /app/storage/...
    upload_dir: str = "storage/uploads"
    result_dir: str = "storage/results"
    max_upload_bytes: int = 10 * 1024 * 1024  # 10 MB
    # True = proses job inline tanpa Redis (dev lokal & test end-to-end).
    celery_task_always_eager: bool = False

    # --- Enhance pipeline (Fase 2 — ADR-002: PyTorch + Real-ESRGAN) ---
    # Pilihan backend: "auto" (real bila model tersedia, fallback mock),
    # "real" (wajib model — gagal keras bila tidak tersedia), "mock".
    enhance_backend: str = "auto"
    model_dir: str = "storage/models"
    realesrgan_model: str = "RealESRGAN_x4plus.pth"
    # "auto" -> cuda bila tersedia, selain itu cpu.
    model_device: str = "auto"
    # Tiling mencegah OOM di GPU (prd.md §10: tile 400-512, tile_pad 10-32).
    tile_size: int = 512
    tile_pad: int = 10
    pre_pad: int = 0
    # FP16 hanya aktif di CUDA (tidak didukung CPU oleh RealESRGANer).
    half_precision: bool = True
    # Batas output sisi terpanjang (ADR-004: maks 7680×4320).
    max_output_longest: int = 7680


settings = Settings()
