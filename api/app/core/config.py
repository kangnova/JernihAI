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


settings = Settings()
