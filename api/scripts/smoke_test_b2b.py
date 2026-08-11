"""Smoke test E2E API Publik B2B (FR-14) — mode PRODUKSI (broker nyata).

Menjalankan alur yang sama persis dengan yang dilakukan developer B2B
terhadap server:

    health -> register/login -> buat API key -> upload via X-API-Key
    -> polling status (job diproses worker Celery nyata, BUKAN eager)
    -> unduh hasil -> verifikasi potongan 1 kredit -> key dicabut ditolak
    -> tanpa saldo -> 402

Pemakaian:
  # Uji lokal mode produksi (docker compose, non-eager):
  python scripts/smoke_test_b2b.py

  # Uji terhadap VPS produksi:
  python scripts/smoke_test_b2b.py --base-url https://api.jernihai.id

Catatan kredit (pay-per-call, 1 job = 1 kredit): bila saldo user test 0,
script menguji jalur 402 dan berhenti dengan panduan. Untuk alur sukses,
beri saldo dulu (opsi operator, bukan endpoint publik):
  docker compose exec -T db psql -U jernihai -d jernihai \
    -c "UPDATE users SET credit_balance = 10 WHERE email = 'b2b-smoke@example.com';"

Exit code 0 = semua langkah lulus; 1 = ada yang gagal.
"""

import argparse
import io
import random
import string
import sys
import time

import httpx
from PIL import Image

API_V1 = "/api/v1"
POLL_INTERVAL_SECONDS = 2
POLL_TIMEOUT_SECONDS = 120


class SmokeError(RuntimeError):
    """Langkah smoke test gagal."""


def _image_bytes() -> bytes:
    """Gambar uji kecil (PNG) — murni in-memory, tanpa file di disk."""
    buf = io.BytesIO()
    Image.new("RGB", (96, 96), (140, 60, 200)).save(buf, format="PNG")
    return buf.getvalue()


def _random_email() -> str:
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    return f"b2b-smoke-{suffix}@example.com"


def _require(resp: httpx.Response, expected: int, step: str, what: str) -> None:
    ok = resp.status_code == expected
    marker = "PASS" if ok else "FAIL"
    print(f"  [{marker}] {step}: {what} -> HTTP {resp.status_code} (harap {expected})")
    if not ok:
        raise SmokeError(f"{step}: HTTP {resp.status_code}, harap {expected} — {resp.text[:300]}")


def _poll_job(client: httpx.Client, base: str, key: str, job_id: str) -> dict:
    """Polling status job sampai selesai/failed (worker nyata memproses)."""
    deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        resp = client.get(f"{base}{API_V1}/b2b/jobs/{job_id}", headers={"X-API-Key": key})
        resp.raise_for_status()
        job = resp.json()
        print(f"  [....] status: {job['status']}")
        if job["status"] in ("completed", "failed"):
            return job
        time.sleep(POLL_INTERVAL_SECONDS)
    raise SmokeError(f"Job {job_id} tidak selesai dalam {POLL_TIMEOUT_SECONDS}s")


def run(base: str, email: str, password: str) -> None:
    print(f"== Smoke test E2E B2B (FR-14) — {base} ==")

    with httpx.Client(base_url=base, timeout=30) as client:
        # --- 1. Health & mode produksi ---
        resp = client.get(f"{API_V1}/health/ready")
        _require(resp, 200, "1. Health ready", "readiness")
        checks = {c["name"]: c["status"] for c in resp.json().get("checks", [])}
        print(f"      checks: {checks}")
        if resp.json()["status"] != "ok":
            print("  [WARN] readiness 'degraded' — server belum siap penuh; "
                  "lanjut tetap dijalankan")

        # --- 2. Register (atau login bila user sudah ada) ---
        resp = client.post(
            f"{API_V1}/auth/register",
            json={"email": email, "password": password, "name": "Smoke B2B",
                  "privacy_consent": True},
        )
        if resp.status_code == 409:  # user sudah ada -> login
            resp = client.post(f"{API_V1}/auth/login", json={"email": email, "password": password})
            _require(resp, 200, "2. Login", "user sudah terdaftar")
            print("  [PASS] 2. Login user test (sudah terdaftar sebelumnya)")
        else:
            _require(resp, 201, "2. Register", "user baru")

        # --- 3. Buat API key ---
        resp = client.post(f"{API_V1}/b2b/keys", json={"name": "Smoke Test", "tier": "free"})
        _require(resp, 201, "3. Buat API key", "POST /b2b/keys")
        body = resp.json()
        full_key = body["full_key"]
        key_id = body["key"]["id"]
        print(f"      key_prefix={body['key']['key_prefix']} "
              f"full_key sekali-tampil={full_key[:12]}…")
        assert full_key.startswith("jn_"), "format key salah"
        listed = str(client.get(f"{API_V1}/b2b/keys").json())
        assert full_key not in listed, "key asli bocor di list"

        # --- 4. Cek kuota & saldo ---
        resp = client.get(f"{API_V1}/b2b/quota", headers={"X-API-Key": full_key})
        _require(resp, 200, "4. Cek kuota", "GET /b2b/quota")
        quota = resp.json()
        balance = quota["credit_balance"]
        print(f"      saldo kredit={balance} tier={quota['tier']} "
              f"rate_limit={quota['rate_limit_per_minute']}")

        # --- 5a. Tanpa saldo -> 402 (pay-per-call gate) ---
        if balance < 1:
            resp = client.post(
                f"{API_V1}/b2b/jobs",
                headers={"X-API-Key": full_key},
                files={"file": ("foto.png", _image_bytes(), "image/png")},
                data={"scale": "2", "output_format": "webp"},
            )
            _require(resp, 402, "5. Tanpa saldo", "upload -> 402 Payment Required")
            raise SmokeError(
                "Saldo kredit 0. Alur 402 PASS. Untuk menguji alur sukses, "
                "beri saldo user ini lalu jalankan ulang:\n"
                "  docker compose exec -T db psql -U jernihai -d jernihai "
                f"-c \"UPDATE users SET credit_balance = 10 WHERE email = '{email}';\""
            )

        # --- 5b. Upload job via X-API-Key (1 kredit) ---
        resp = client.post(
            f"{API_V1}/b2b/jobs",
            headers={"X-API-Key": full_key},
            files={"file": ("foto.png", _image_bytes(), "image/png")},
            data={"scale": "2", "output_format": "webp"},
        )
        _require(resp, 201, "5. Upload job", "POST /b2b/jobs")
        job = resp.json()
        job_id = job["id"]
        if job["status"] == "completed":
            print("  [WARN] job langsung 'completed' saat dibuat — waspadai mode EAGER "
                  "(harusnya 'queued' lalu diproses worker)")
        else:
            print("  [PASS] job 'queued' di respons awal — terkonfirmasi diproses via broker")

        # --- 6. Polling status (worker Celery nyata) ---
        job = _poll_job(client, base, full_key, job_id)
        _require_done = job["status"] == "completed"
        print(f"  [{'PASS' if _require_done else 'FAIL'}] 6. Status job: {job['status']}")
        if not _require_done:
            raise SmokeError(f"Job gagal: {job.get('error')}")

        # --- 7. Unduh hasil ---
        resp = client.get(f"{API_V1}/b2b/jobs/{job_id}/result", headers={"X-API-Key": full_key})
        _require(resp, 200, "7. Unduh hasil", "GET /b2b/jobs/{id}/result")
        assert resp.headers["content-type"].startswith("image/"), "bukan image"
        assert len(resp.content) > 0, "file hasil kosong"
        print(f"      {len(resp.content)} bytes, {resp.headers['content-type']}")

        # --- 8. Potongan kredit tepat 1 ---
        resp = client.get(f"{API_V1}/b2b/quota", headers={"X-API-Key": full_key})
        new_balance = resp.json()["credit_balance"]
        exact = new_balance == balance - 1
        print(f"  [{'PASS' if exact else 'FAIL'}] 8. Potongan kredit: "
              f"{balance} -> {new_balance} (harus -1)")
        if not exact:
            raise SmokeError(f"Potongan kredit salah: {balance} -> {new_balance}")

        # --- 9. Key dicabut -> ditolak 403 ---
        resp = client.delete(f"{API_V1}/b2b/keys/{key_id}")
        _require(resp, 204, "9a. Cabut key", "DELETE /b2b/keys/{id}")
        resp = client.get(f"{API_V1}/b2b/quota", headers={"X-API-Key": full_key})
        _require(resp, 403, "9b. Key dicabut", "permintaan ditolak 403")

        print("\n== SEMUA LANGKAH LULUS — FR-14 berfungsi di mode produksi (broker nyata) ==")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--base-url", default="http://localhost:8000",
        help="Base URL API (default: lokal compose)",
    )
    parser.add_argument("--email", default=None, help="Email user test (default: acak unik)")
    parser.add_argument("--password", default="smoke-test-pass-123", help="Password user test")
    args = parser.parse_args()

    email = args.email or _random_email()
    try:
        run(args.base_url, email, args.password)
        return 0
    except SmokeError as exc:
        print(f"\n❌ SMOKE TEST GAGAL: {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001 — laporkan & exit kode 1
        print(f"\n❌ ERROR TAK TERDUGA: {exc!r}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
