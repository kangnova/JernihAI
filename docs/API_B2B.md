# API Publik B2B — Dokumentasi Developer (FR-14)

Integrasikan peningkatan kualitas gambar (super-resolution, denoising,
face restoration) ke aplikasi Anda lewat REST API. **1 job = 1 kredit**
(pay-per-call) dari saldo pemilik API key.

- **Autentikasi**: header `X-API-Key`
- **Base URL**: `https://api.<domain-anda>.id` (lokal dev: `http://localhost:8000`)
- **Versi**: prefix `/api/v1`
- **Spesifikasi OpenAPI**: `docs/api/openapi.yaml` (interaktif: `/docs` di server)
- **Smoke test E2E**: `python api/scripts/smoke_test_b2b.py --base-url <base-url>`

---

## 1. Memulai dalam 3 langkah

1. **Buat API key** — login ke web, buka **API** (halaman `/api-keys`),
   klik **Buat key**. Pilih tier `free` (20 req/menit) atau `pro`
   (120 req/menit). ⚠️ **Key asli hanya ditampilkan SEKALI** — salin
   dan simpan di tempat aman (env var server Anda). Yang disimpan di
   server hanya hash SHA-256.
2. **Isi saldo kredit** — kunjungi halaman **Billing** di web dan beli
   paket kredit (Midtrans: QRIS · e-wallet · Virtual Account). Tanpa
   saldo, panggilan job ditolak `402`.
3. **Panggil API** — kirim header `X-API-Key` pada setiap request
   (contoh: §6).

```bash
# Cek saldo & tier key Anda:
curl -s https://api.jernihai.id/api/v1/b2b/quota \
  -H "X-API-Key: jn_xxxx"
# → {"credit_balance":20,"tier":"free","rate_limit_per_minute":20}
```

---

## 2. Autentikasi & keamanan key

| Aspek | Keterangan |
|---|---|
| Header | `X-API-Key: jn_<token>` — wajib di semua endpoint B2B (job, result, quota) |
| Format key | `jn_` + token acak 32-byte (`secrets.token_urlsafe`) |
| Penyimpanan server | Hanya **hash SHA-256** + prefix pendek (`jn_AbC123xYz`) untuk tampilan |
| Key dicabut | Langsung ditolak `403`; cabut kapan saja di halaman `/api-keys` |
| Akun pemilik di-suspend | Semua key pemilik ditolak `403` |
| Privasi | Key tidak pernah muncul ulang di API `GET /b2b/keys` |

**Praktik baik:**
- Simpan key di env var server, bukan di kode/klien.
- Buat key terpisah per lingkungan (`Produksi`, `Staging`) agar bisa
  dicabut independen.
- Rotasi berkala: buat key baru → deploy → cabut key lama.

---

## 3. Endpoint (ringkasan)

| Metode & Path | Deskripsi | Auth |
|---|---|---|
| `POST /api/v1/b2b/jobs` | Upload gambar & mulai proses (1 kredit) | `X-API-Key` |
| `GET /api/v1/b2b/jobs/{id}` | Status job (polling) | `X-API-Key` |
| `GET /api/v1/b2b/jobs/{id}/result` | Unduh hasil (binary) | `X-API-Key` |
| `GET /api/v1/b2b/quota` | Sisa kredit + tier + rate limit | `X-API-Key` |
| `GET /api/v1/b2b/keys` | Daftar key saya | Cookie web |
| `POST /api/v1/b2b/keys` | Buat key baru | Cookie web |
| `DELETE /api/v1/b2b/keys/{id}` | Cabut key | Cookie web |

> Manajemen key memakai sesi login web (cookie), bukan API key — alur
> alami: developer mengelola key di dashboard, lalu memakainya dari server.

---

## 4. Endpoint detail

### 4.1 `POST /api/v1/b2b/jobs` — upload & mulai proses

Multipart form. **Biaya: 1 kredit** (dipotong atomik saat request sukses).

| Field | Tipe | Default | Keterangan |
|---|---|---|---|
| `file` | binary | — | JPG/PNG/WebP, maks **10 MB** (validasi konten, bukan ekstensi) |
| `scale` | int | `2` | Faktor pembesaran: `2` atau `4` |
| `output_format` | str | `webp` | `webp` (default, terbaik) · `jpeg` · `png` (lossless — **dibatasi ≤ 4096 px** sisi terpanjang, ADR-004) |
| `face_enhance` | bool | `false` | Restorasi wajah (GFPGAN) |
| `denoise` | bool | `false` | Kurangi noise |
| `color_enhance` | bool | `false` | Pertegas warna |

**Respons `201 Created`** — objek job (status awal `queued`):
```json
{
  "id": "339d3342-c9cc-49c5-8b1f-64c7eb2fb6f9",
  "status": "queued",
  "scale": 2,
  "output_format": "webp",
  "face_enhance": false,
  "denoise": false,
  "color_enhance": false,
  "original_name": "foto.png",
  "error": null,
  "created_at": "2026-08-11T13:44:18.400Z",
  "finished_at": null,
  "result_deleted_at": null
}
```

```bash
curl -X POST https://api.jernihai.id/api/v1/b2b/jobs \
  -H "X-API-Key: jn_xxxx" \
  -F "file=@foto.jpg" -F "scale=2" -F "output_format=webp"
```

### 4.2 `GET /api/v1/b2b/jobs/{id}` — status job

Polling hingga `completed`. Siklus: `queued → processing → completed`
(atau `failed` bila ada error — lihat field `error`).

```bash
curl https://api.jernihai.id/api/v1/b2b/jobs/339d3342-c9cc-49c5-8b1f-64c7eb2fb6f9 \
  -H "X-API-Key: jn_xxxx"
```

### 4.3 `GET /api/v1/b2b/jobs/{id}/result` — unduh hasil

Hanya tersedia saat job `completed` dan sebelum retensi menghapus hasil
(7 hari free). Media type sesuai `output_format`.

```bash
curl -o hasil.webp https://api.jernihai.id/api/v1/b2b/jobs/339d3342-c9cc-49c5-8b1f-64c7eb2fb6f9/result \
  -H "X-API-Key: jn_xxxx"
```

### 4.4 `GET /api/v1/b2b/quota` — sisa kredit & tier

```json
{"credit_balance": 19, "tier": "free", "rate_limit_per_minute": 20}
```

---

## 5. Kode status & error

Semua error memakai tubuh `{"detail": "..."}`.

| Kode | Arti | Kapan |
|---|---|---|
| `200` / `201` | Sukses | — |
| `204` | Sukses tanpa tubuh (cabut key) | — |
| `400` | Parameter salah | `scale`/`output_format`/`tier` tidak dikenal |
| `401` | Tidak terautentikasi | Header `X-API-Key` tidak ada / key tidak dikenal |
| `402` | **Saldo kredit kosong** | Pay-per-call: 1 gambar = 1 kredit; isi saldo di Billing |
| `403` | Ditolak | Key dicabut / akun pemilik di-suspend |
| `404` | Tidak ditemukan | Job/key tidak ada **atau milik key lain** (tanpa bocor info) |
| `409` | Konflik | Hasil belum siap (job belum `completed`) |
| `410` | Gone | Hasil sudah dihapus retensi otomatis (7 hari) |
| `413` | Terlalu besar | File > 10 MB |
| `415` | Tipe tidak didukung | Bukan JPG/PNG/WebP (validasi konten) |
| `422` | Validasi gagal | Form/body tidak valid, nama kosong |
| `429` | **Rate limit terlampaui** | Melebihi batas tier per menit |

> **Isolasi job:** key Anda hanya bisa mengakses job milik akun Anda.
> Job akun lain → `404` (bukan `403`), agar tidak ada kebocoran informasi.

---

## 6. Contoh integrasi

### Python (`httpx`)

```python
import httpx, time

BASE = "https://api.jernihai.id/api/v1"
KEY = "jn_xxxx"          # dari env var, bukan hardcode!

# 1) Upload & mulai proses
r = httpx.post(f"{BASE}/b2b/jobs",
    headers={"X-API-Key": KEY},
    files={"file": ("foto.jpg", open("foto.jpg", "rb"), "image/jpeg")},
    data={"scale": "2", "output_format": "webp"})
r.raise_for_status()
job_id = r.json()["id"]

# 2) Polling status (interval 2-3 dtk — jangan lebih cepat)
while True:
    job = httpx.get(f"{BASE}/b2b/jobs/{job_id}",
                    headers={"X-API-Key": KEY}).json()
    if job["status"] in ("completed", "failed"):
        break
    time.sleep(2)

if job["status"] == "failed":
    raise SystemExit(f"Job gagal: {job['error']}")

# 3) Unduh hasil
res = httpx.get(f"{BASE}/b2b/jobs/{job_id}/result", headers={"X-API-Key": KEY})
open("hasil.webp", "wb").write(res.content)
```

### Node.js (fetch, Node 18+)

```js
const KEY = process.env.JERNIHAI_API_KEY; // wajib dari env

const form = new FormData();
form.append("file", new Blob([buffer], { type: "image/jpeg" }), "foto.jpg");
form.append("scale", "2");
form.append("output_format", "webp");

const created = await fetch(`${BASE}/api/v1/b2b/jobs`, {
  method: "POST", headers: { "X-API-Key": KEY }, body: form,
});
const job = await created.json();

let current;
do {
  await new Promise((r) => setTimeout(r, 2000));
  current = await (await fetch(`${BASE}/api/v1/b2b/jobs/${job.id}`,
    { headers: { "X-API-Key": KEY } })).json();
} while (!["completed", "failed"].includes(current.status));
```

---

## 7. Rate limit per tier (NFR-04)

Fixed-window **per menit per key** (in-memory; satu instance API):

| Tier | Batas | Keterangan |
|---|---|---|
| `free` | **20 req/menit** | Default saat membuat key |
| `pro` | **120 req/menit** | Key dengan tier `pro` |

- Berlaku untuk **semua** endpoint B2B (termasuk polling status).
- Melebihi batas → `429`; tunggu jendela menit berikutnya.
- Konfigurasi server: env `API_RATE_LIMIT_FREE_PER_MINUTE` /
  `API_RATE_LIMIT_PRO_PER_MINUTE`.

**Tips:** polling status dengan interval 2–3 detik (bukan per detik) —
aman di tier `free` sekalipun.

---

## 8. Kredit & billing (pay-per-call)

- **1 job = 1 kredit**, dipotong dari saldo pemilik key saat request
  `POST /b2b/jobs` sukses (potongan atomik — aman untuk request paralel).
- **Job gagal → refund otomatis 1 kredit** ke saldo (termasuk job yang
  hang dan ditandai gagal oleh stale-check).
- Isi saldo di halaman **Billing** web (paket kredit, Midtrans Snap:
  QRIS · e-wallet · Virtual Account). Belum ada top-up API publik.

---

## 9. Spesifikasi OpenAPI

- **Interaktif**: buka `/docs` (Swagger UI) atau `/redoc` di server —
  seluruh endpoint, skema, dan contoh siap dicoba dari browser.
- **File**: `docs/api/openapi.yaml` — unduh untuk generator SDK/klien
  (OpenAPI Generator, openapi-typescript, dsb). Regenerate setelah
  perubahan API:
  ```bash
  cd api && .venv/Scripts/python scripts/export_openapi.py
  ```
- **Smoke test E2E** terhadap server mana pun (termasuk VPS produksi):
  ```bash
  cd api && .venv/Scripts/python scripts/smoke_test_b2b.py \
    --base-url https://api.jernihai.id
  ```
  (user test baru dibuat otomatis; untuk alur sukses, isi saldo user tsb.)

---

## 10. FAQ

**Kenapa `402`?** Saldo kredit habis — beli di halaman Billing web, lalu
ulangi request. Tidak ada job yang dibuat tanpa saldo.

**Job `failed` — apakah saya dirugikan?** Tidak. Kredit di-refund otomatis
ke saldo; cek field `error` untuk penyebab (format, file korup, dll.).

**Bisa proses 4x gambar besar?** Ya — worker menangani tiling + FP16 dan
membatasi output maks 7680×4320 (ADR-004). Hasil default WebP q90 (kecil &
tajam). **PNG lossless dibatasi ≤ 4096 px** sisi terpanjang: input lebih
besar ditolak `400` sebelum kredit dipakai — pilih `webp`/`jpeg` untuk
ukuran lebih besar.

**Berapa lama proses?** End-to-end biasanya < 60 detik tergantung ukuran
input & antrean (KPI NFR-01: 4x 1080p < 15 dtk saat worker warm).
