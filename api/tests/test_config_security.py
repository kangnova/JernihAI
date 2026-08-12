"""Tests hardening keamanan konfigurasi produksi (fail-fast, ADR-003).

`Settings._validate_production` (app/core/config.py) harus menolak start
saat `environment=production` dengan JWT secret dev/lemah atau cookie tanpa
`Secure` — mencegah deploy tidak sengaja dengan konfigurasi berbahaya.
"""

import logging

import pytest
from pydantic import ValidationError

from app.core.config import Settings

# Secret dev yang dikenali validator (sama dengan _DEV_JWT_SECRET).
_DEV_SECRET = "dev-only-jangan-pakai-di-produksi-0123456789abcdef0123456789abcdef"
_STRONG_SECRET = "k" * 64


def test_dev_default_secret_is_long_enough():
    """Default dev pun harus >= 32 byte agar tidak memicu InsecureKeyLengthWarning
    (RFC 7518) saat JWT encode/decode."""
    s = Settings(_env_file=None)
    assert len(s.jwt_secret.encode("utf-8")) >= 32


def test_production_rejects_dev_default_secret():
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            environment="production",
            jwt_secret=_DEV_SECRET,
            cookie_secure=True,
        )


def test_production_rejects_short_secret():
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            environment="production",
            jwt_secret="rahasia-pendek",
            cookie_secure=True,
        )


def test_production_rejects_cookie_not_secure():
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            environment="production",
            jwt_secret=_STRONG_SECRET,
            cookie_secure=False,
        )


def test_production_accepts_hardened_config():
    s = Settings(
        _env_file=None,
        environment="production",
        jwt_secret=_STRONG_SECRET,
        cookie_secure=True,
    )
    assert s.environment == "production"


def test_non_production_is_not_blocked():
    """Dev/test boleh pakai secret pendek — suite test tidak terpengaruh."""
    s = Settings(
        _env_file=None,
        environment="development",
        jwt_secret="dev-singkat",
        cookie_secure=False,
    )
    assert s.environment == "development"


def test_production_reports_all_problems_at_once():
    """Semua masalah dilaporkan sekaligus, bukan hanya yang pertama ditemukan."""
    with pytest.raises(ValidationError) as exc:
        Settings(
            _env_file=None,
            environment="production",
            jwt_secret="pendek",
            cookie_secure=False,
        )
    detail = str(exc.value)
    assert "JWT_SECRET" in detail
    assert "COOKIE_SECURE" in detail


def test_production_accepts_minimum_32_char_secret():
    """Batas bawah: secret tepat 32 byte diterima (RFC 7518)."""
    s = Settings(
        _env_file=None,
        environment="production",
        jwt_secret="a" * 32,
        cookie_secure=True,
    )
    assert len(s.jwt_secret) == 32


def test_production_rejects_r2_without_credentials():
    """Fase 3: STORAGE_BACKEND=r2 tanpa kredensial = core storage pasti gagal
    → fail-fast (bukan sekadar warning seperti fitur opsional)."""
    with pytest.raises(ValidationError) as exc:
        Settings(
            _env_file=None,
            environment="production",
            jwt_secret=_STRONG_SECRET,
            cookie_secure=True,
            storage_backend="r2",
        )
    assert "R2_ACCOUNT_ID" in str(exc.value)


def test_production_accepts_r2_with_full_credentials():
    s = Settings(
        _env_file=None,
        environment="production",
        jwt_secret=_STRONG_SECRET,
        cookie_secure=True,
        storage_backend="r2",
        r2_account_id="a" * 32,
        r2_access_key_id="key",
        r2_secret_access_key="secret",
    )
    assert s.storage_backend == "r2"


def test_production_rejects_unknown_storage_backend():
    """Typo STORAGE_BACKEND tidak boleh diam-diam jatuh ke local."""
    with pytest.raises(ValidationError) as exc:
        Settings(
            _env_file=None,
            environment="production",
            jwt_secret=_STRONG_SECRET,
            cookie_secure=True,
            storage_backend="r22",
        )
    assert "STORAGE_BACKEND" in str(exc.value)


def test_log_production_warnings_reports_gaps(caplog):
    s = Settings(
        _env_file=None,
        environment="production",
        jwt_secret=_STRONG_SECRET,
        cookie_secure=True,
        admin_emails=[],
        google_client_id="",
        midtrans_server_key="",
        cors_origins=["http://localhost:3000"],
    )
    with caplog.at_level(logging.WARNING):
        s.log_production_warnings()
    messages = [r.message for r in caplog.records]
    assert any("ADMIN_EMAILS" in m for m in messages)
    assert any("GOOGLE_CLIENT_ID" in m for m in messages)
    assert any("MIDTRANS_SERVER_KEY" in m for m in messages)
    assert any("CORS_ORIGINS" in m for m in messages)


def test_log_production_warnings_silent_in_dev(caplog):
    s = Settings(
        _env_file=None,
        environment="development",
        jwt_secret=_DEV_SECRET,
        admin_emails=[],
        cors_origins=["http://localhost:3000"],
    )
    with caplog.at_level(logging.WARNING):
        s.log_production_warnings()
    assert caplog.records == []
