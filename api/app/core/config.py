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
    # per IP (fixed-window in-memory, lihat app/core/ratelimit.py).
    rate_limit_enabled: bool = True
    rate_limit_auth_per_minute: int = 10  # brute-force login/register
    rate_limit_upload_per_minute: int = 30  # abuse upload/batch

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
    # FR-12: jumlah maksimal gambar per batch (prd.md: hingga 10 gambar).
    batch_max_files: int = 10
    # True = proses job inline tanpa Redis (dev lokal & test end-to-end).
    celery_task_always_eager: bool = False

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


settings = Settings()
