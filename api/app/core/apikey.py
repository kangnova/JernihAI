"""API key (FR-14 B2B) — generate & hash.

Key asli hanya ditampilkan SEKALI saat dibuat; yang disimpan di DB
hanyalah hash SHA-256 + awalan pendek (untuk tampilan UI).
"""

import hashlib
import secrets

KEY_PREFIX = "jn_"


def generate_api_key() -> tuple[str, str, str]:
    """Buat key baru.

    Returns:
        (full_key, key_hash, key_prefix)
        - full_key: `jn_<token>` — TAMPILKAN SEKALI ke developer.
        - key_hash: SHA-256 hex — satu-satunya yang disimpan di DB.
        - key_prefix: awalan pendek utk ditampilkan, mis. `jn_AbC123xYz`.
    """
    token = secrets.token_urlsafe(32)
    full = f"{KEY_PREFIX}{token}"
    return full, hash_api_key(full), full[:12]


def hash_api_key(full_key: str) -> str:
    """SHA-256 hex dari key asli — dipakai lookup & verifikasi header."""
    return hashlib.sha256(full_key.encode()).hexdigest()
