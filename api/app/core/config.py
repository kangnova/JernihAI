"""Konfigurasi aplikasi via environment variables (pydantic-settings)."""

import logging

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# Secret dev yang DIKENALI — produksi WAJIB override (lihat validator
# `_validate_production` di bawah). Jangan dipakai di produksi.
_DEV_JWT_SECRET = (
    "dev-only-jangan-pakai-di-produksi-0123456789abcdef0123456789abcdef"
)


def _generate_secret_hint() -> str:
    return 'python -c "import secrets; print(secrets.token_urlsafe(48))"'


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

    # --- Admin (FR-13) ---
    # Email yang berhak mengakses endpoint admin (list JSON di .env).
    # Tanpa kolom DB: cukup env — sederhana & tanpa migrasi untuk MVP.
    admin_emails: list[str] = []

    # --- Pembayaran (FR-11 — Midtrans Snap) ---
    # Kosongkan untuk mode dev: checkout memakai token MOCK (tanpa SDK/
    # key), webhook tetap diverifikasi dengan server key kosong -> 403.
    midtrans_server_key: str = ""
    midtrans_client_key: str = ""
    midtrans_is_production: bool = False  # sandbox default
    # Nama item produk di halaman pembayaran Midtrans.
    midtrans_item_name: str = "Kredit JernihAI"
    # Biaya admin halaman /billing untuk menampilkan paket (PRD §11).
    billing_packages: dict[str, dict] = {
        "kredit-20": {"credits": 20, "price_idr": 10_000},
        "lite-100": {"credits": 100, "price_idr": 29_000},
        "pro-500": {"credits": 500, "price_idr": 79_000},
    }

    # --- Rate limiting (NFR-04) ---
    # False untuk test (suite tidak saling memicu 429). Ambang per menit
    # per IP (fixed-window, lihat app/core/ratelimit.py).
    rate_limit_enabled: bool = True
    rate_limit_auth_per_minute: int = 10  # brute-force login/register
    rate_limit_upload_per_minute: int = 30  # abuse upload/batch
    # FR-14: rate limit API publik B2B per menit per key, berdasarkan tier.
    api_rate_limit_free_per_minute: int = 20
    api_rate_limit_pro_per_minute: int = 120
    # Backend counter rate limit: "memory" (default, per proses — cukup
    # single-instance & test) atau "redis" (state dibagi antar instance —
    # produksi multi-node). Lihat app/core/ratelimit.py.
    rate_limit_backend: str = "memory"

    # --- Auth (JWT) ---
    # Secret dev (>= 32 byte, memenuhi rekomendasi RFC 7518). Produksi
    # WAJIB override via env JWT_SECRET — `environment=production` akan
    # memblokir start (fail-fast) bila secret masih dev/lemah.
    jwt_secret: str = _DEV_JWT_SECRET
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24 * 7  # 7 hari
    cookie_name: str = "jernihai_session"
    cookie_secure: bool = False  # True di produksi (HTTPS)

    # --- Google OAuth ---
    google_client_id: str = ""
    google_client_secret: str = ""
    # Redirect tujuan setelah Google login sukses/gagal di web.
    web_url: str = "http://localhost:3000"

    # --- Upload & storage (Fase 1 — FR-02/FR-07; Fase 3 — R2) ---
    # Folder relatif terhadap root repo; di Docker menjadi /app/storage/...
    upload_dir: str = "storage/uploads"
    result_dir: str = "storage/results"
    max_upload_bytes: int = 10 * 1024 * 1024  # 10 MB
    # FR-12: jumlah maksimal gambar per batch (prd.md: hingga 10 gambar).
    batch_max_files: int = 10
    # True = proses job inline tanpa Redis (dev lokal & test end-to-end).
    celery_task_always_eager: bool = False

    # --- Cloudflare R2 (Fase 3 — storage produksi & multi-node) ---
    # Backend penyimpanan: "local" (disk, dev/test) atau "r2" (produksi).
    # Panduan setup: docs/GUIDE_R2.md
    storage_backend: str = "local"
    # Account ID Cloudflare (32 karakter alfanumerik) — bagian dari endpoint
    # S3: https://<account_id>.r2.cloudflarestorage.com
    r2_account_id: str = ""
    # R2 API Token → Access Key ID + Secret Access Key (RAHASIA).
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    # Nama bucket (buat manual di dashboard R2 atau wrangler).
    r2_bucket: str = "jernihai"

    # --- Kuota gratis (FR-06) ---
    # Jumlah gambar gratis per user per hari (reset otomatis 00:00 WIB).
    free_daily_quota: int = 3

    # --- Retensi data (FR-07 / UU PDP) ---
    # Original dihapus otomatis setelah N jam; hasil proses setelah N hari.
    retention_original_hours: int = 24
    retention_result_days: int = 7
    # Interval sweep retensi (Celery Beat) dalam menit.
    retention_purge_interval_minutes: int = 60

    # --- Reliabilitas job (NFR-03) ---
    # Job yang tersangkut di status `processing` lebih dari batas ini (menit)
    # dianggap hang (worker mati/crash di tengah pipeline) -> di-stale-check
    # menjadi `failed` + kuota direfund (lihat app/tasks/stale.py).
    job_stale_minutes: int = 30
    # Interval sweep stale-check (Celery Beat) dalam menit.
    stale_check_interval_minutes: int = 15
    # Celery: jumlah percobaan ulang untuk job gagal (retry count = max_retries,
    # total eksekusi = max_retries + 1).
    job_max_retries: int = 2
    # NFR-03: timeout per job — prd.md §8 "timeout per job (mis. 120 detik)".
    # soft_time_limit: waktu maksimal eksekusi pipeline sebelum worker
    # menginterupsi (SoftTimeLimitExceeded -> job failed -> retry otomatis).
    job_soft_time_limit_seconds: int = 120
    # hard limit: batas mati (mematikan task bila soft limit tidak digubris).
    job_hard_time_limit_seconds: int = 180

    # --- Enhance pipeline (Fase 2 — ADR-002: PyTorch + Real-ESRGAN) ---
    # Pilihan backend: "auto" (real bila model tersedia, fallback mock),
    # "real" (wajib model — gagal keras bila tidak tersedia), "mock".
    enhance_backend: str = "auto"
    model_dir: str = "storage/models"
    realesrgan_model: str = "RealESRGAN_x4plus.pth"
    # FR-09: model general untuk denoise (SRVGGNetCompact + DNI interpolasi
    # dengan versi wdn = with-denoise; lihat realesrgan 0.3.0 inference script).
    realesrgan_general_model: str = "realesr-general-x4v3.pth"
    realesrgan_general_wdn_model: str = "realesr-general-wdn-x4v3.pth"
    # Kekuatan denoise (DNI): `dni_weight=[strength, 1-strength]` dibobotkan
    # ke [model NORMAL (x4v3), model wdn] — bobot wdn makin besar = denoise
    # makin kuat. CATATAN quirk upstream (Real-ESRGAN v0.3.0): dengan urutan
    # ini, strength 0 = murni wdn (denoise TERKUAT), 1 = murni normal
    # (terlemah) — kebalikan dari help text resmi `-dn` (kode kita meniru
    # inference_realesrgan.py persis). Default 0.5 = campuran seimbang.
    denoise_strength: float = 0.5
    # Kekuatan color enhance (saturasi, kontras, brightness), 1.0 = netral.
    color_enhance_strength: float = 1.2
    # FR-08: weight GFPGAN (face enhance) di model_dir.
    gfpgan_model: str = "GFPGANv1.4.pth"
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
    # ADR-004: PNG lossless dibatasi 4096 px sisi terpanjang (8K PNG = 20+ MB,
    # tidak didukung default). Di-enforce di request (jobs/b2b -> 400) dan di
    # pipeline (_effective_outscale memakai batas ini untuk format png).
    png_max_output_longest: int = 4096

    @model_validator(mode="after")
    def _validate_production(self) -> "Settings":
        """Fail-fast saat `environment=production`: tolak konfigurasi berbahaya.

        Mencegah deploy tanpa sengaja dengan JWT secret default/lemah atau
        cookie sesi tanpa Secure (cookie dikirim plaintext via HTTP). Dev/test
        (environment lain) tidak terpengaruh.
        """
        if self.environment.lower() != "production":
            return self
        problems: list[str] = []
        if self.jwt_secret == _DEV_JWT_SECRET or len(self.jwt_secret) < 32:
            problems.append(
                "JWT_SECRET masih dev/lemah — set secret acak minimal 32 byte, "
                f"misal: {_generate_secret_hint()}"
            )
        if not self.cookie_secure:
            problems.append(
                "COOKIE_SECURE wajib true di produksi (sesi hanya lewat HTTPS)"
            )
        if self.storage_backend == "r2" and not (
            self.r2_account_id and self.r2_access_key_id and self.r2_secret_access_key
        ):
            problems.append(
                "STORAGE_BACKEND=r2 tapi R2_ACCOUNT_ID/R2_ACCESS_KEY_ID/"
                "R2_SECRET_ACCESS_KEY belum lengkap — upload/download pasti gagal"
            )
        if self.storage_backend not in ("local", "r2"):
            problems.append(
                f"STORAGE_BACKEND tidak dikenal: {self.storage_backend!r} "
                "(hanya 'local' atau 'r2')"
            )
        if self.rate_limit_backend not in ("memory", "redis"):
            problems.append(
                f"RATE_LIMIT_BACKEND tidak dikenal: {self.rate_limit_backend!r} "
                "(hanya 'memory' atau 'redis')"
            )
        if problems:
            raise ValueError(
                "Konfigurasi produksi tidak aman — perbaiki sebelum start:\n - "
                + "\n - ".join(problems)
            )
        return self

    def log_production_warnings(self) -> None:
        """Warning non-fatal saat produksi (dipanggil di lifespan main.py).

        Tidak memblokir start — hanya mengingatkan gap yang disengaja (mis.
        Google OAuth belum di-set) atau yang perlu diperketat (CORS).
        """
        if self.environment.lower() != "production":
            return
        if not self.admin_emails:
            logger.warning(
                "ADMIN_EMAILS kosong — halaman/endpoint admin (FR-13) tidak bisa diakses"
            )
        if not self.google_client_id:
            logger.warning(
                "GOOGLE_CLIENT_ID kosong — login Google (FR-01) nonaktif (HTTP 503)"
            )
        if not self.midtrans_server_key:
            logger.warning(
                "MIDTRANS_SERVER_KEY kosong — checkout mode MOCK, tidak ada pembayaran nyata"
            )
        if self.storage_backend not in ("local", "r2"):
            logger.warning(
                f"STORAGE_BACKEND tidak dikenal: {self.storage_backend!r} — "
                "dianggap 'local' (cek .env)"
            )
        if self.rate_limit_backend != "redis":
            logger.warning(
                "RATE_LIMIT_BACKEND=memory — counter per proses; "
                "dengan multi-instance rate limit tidak akurat (set redis)"
            )
        if any(
            origin == "*" or "localhost" in origin or "127.0.0.1" in origin
            for origin in self.cors_origins
        ):
            logger.warning(
                "CORS_ORIGINS mengandung '*'/localhost — perketat ke origin domain produksi saja"
            )


settings = Settings()
