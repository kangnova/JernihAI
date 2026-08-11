"""Tes integrasi Alembic (ADR-011).

Menjamin `alembic upgrade head` menghasilkan skema yang LENGKAP untuk
versi kode saat ini — tujuan utamanya: deploy ke VPS (DB lama yang dibuat
`create_all` era pra-Alembic) TIDAK perlu `docker compose down -v` saat
menambah kolom/tabel.

Cakupan:
- DB fresh: semua tabel + kolom lintas FR dibuat.
- Idempoten: upgrade ulang tidak error (alembic_version terisi).
- DB lama parsial (hanya sebagian tabel/kolom): tabel & kolom yang hilang
  ditambahkan, DATA lama tidak hilang.

Catatan: migrasi memakai engine async (env.py) — URL test memakai
`sqlite+aiosqlite`; inspeksi memakai engine sync pada file DB yang sama.
"""

from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

API_DIR = Path(__file__).resolve().parents[1]

# Kolom yang hanya ada berkat FR-07/08/09/11 — bukti migrasi menutup gap
# yang dulu memaksa `docker compose down -v`.
USER_COLS = {"credit_balance", "privacy_consent_at", "free_daily_quota_used"}
JOB_COLS = {
    "face_enhance",
    "denoise",
    "color_enhance",
    "uses_credit",
    "original_deleted_at",
    "result_deleted_at",
    # Migrasi 0002 (contoh autogenerate): kolom baru harus ikut terdeteksi
    # di rantai upgrade 0001 -> 0002 (bukti alur autogenerate jalan).
    "admin_notes",
}
TXN_COLS = {"order_id", "credits", "amount_idr", "status", "paid_at"}
# Migrasi 0003 (FR-14): tabel API publik B2B — hanya hash + prefix key.
API_KEY_COLS = {"user_id", "key_hash", "key_prefix", "tier", "is_active"}


def _alembic_config(sqlite_url: str) -> Config:
    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", sqlite_url)
    return cfg


def _sync_engine(db_path: Path):
    return sa.create_engine(f"sqlite:///{db_path}")


def _table_names(db_path: Path) -> set[str]:
    engine = _sync_engine(db_path)
    try:
        return set(sa.inspect(engine).get_table_names())
    finally:
        engine.dispose()


def _columns(db_path: Path, table: str) -> set[str]:
    engine = _sync_engine(db_path)
    try:
        return {c["name"] for c in sa.inspect(engine).get_columns(table)}
    finally:
        engine.dispose()


def test_upgrade_head_creates_full_schema(tmp_path):
    """DB fresh: upgrade head membuat semua tabel + kolom lintas FR."""
    db = tmp_path / "fresh.db"
    command.upgrade(_alembic_config(f"sqlite+aiosqlite:///{db}"), "head")

    tables = _table_names(db)
    assert {"users", "jobs", "transactions", "api_keys"} <= tables

    assert _columns(db, "users") >= USER_COLS
    assert _columns(db, "jobs") >= JOB_COLS
    assert _columns(db, "transactions") >= TXN_COLS
    assert _columns(db, "api_keys") >= API_KEY_COLS


def test_upgrade_is_idempotent(tmp_path):
    """Upgrade ulang tidak error (versi sudah tercatat di alembic_version)."""
    db = tmp_path / "idem.db"
    cfg = _alembic_config(f"sqlite+aiosqlite:///{db}")

    command.upgrade(cfg, "head")
    command.upgrade(cfg, "head")  # ulang — harus mulus

    assert {"users", "jobs", "transactions"} <= _table_names(db)


def test_upgrade_converges_partial_legacy_db_without_losing_data(tmp_path):
    """DB lama (era create_all) dgn sebagian tabel/kolom -> konvergen penuh.

    Tabel `users`/`jobs` versi minimal (FR-01/FR-02) + 1 baris data lama:
    upgrade harus menambah tabel `transactions`, men-backfill kolom
    FR-06/07/08/09/11 yang hilang, dan MEMPERTAHANKAN data lama.
    """
    db = tmp_path / "legacy.db"
    engine = _sync_engine(db)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "CREATE TABLE users ("
                "id VARCHAR(36) PRIMARY KEY, email VARCHAR(255), "
                "created_at TIMESTAMP)"
            )
        )
        conn.execute(
            sa.text(
                "CREATE TABLE jobs ("
                "id VARCHAR(36) PRIMARY KEY, status VARCHAR(20))"
            )
        )
        conn.execute(
            sa.text("INSERT INTO users (id, email) VALUES ('u1', 'lama@example.com')")
        )
    engine.dispose()

    command.upgrade(_alembic_config(f"sqlite+aiosqlite:///{db}"), "head")

    tables = _table_names(db)
    assert {"users", "jobs", "transactions"} <= tables
    # Kolom yang hilang di DB lama kini lengkap.
    assert _columns(db, "users") >= USER_COLS
    assert _columns(db, "jobs") >= JOB_COLS
    # Data lama tidak hilang.
    engine = _sync_engine(db)
    try:
        with engine.connect() as conn:
            count = conn.execute(sa.text("SELECT COUNT(*) FROM users")).scalar()
    finally:
        engine.dispose()
    assert count == 1
