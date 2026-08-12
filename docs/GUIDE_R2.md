# Panduan: Aktifkan Cloudflare R2 (Storage Produksi — Fase 3)

Panduan langkah-demi-langkah untuk memindahkan storage JernihAI dari disk
lokal ke **Cloudflare R2** — storage objek S3-compatible dengan **egress
gratis** dan akses dari mana saja (kunci untuk arsitektur **multi-node**:
api + worker GPU Vast.ai tidak perlu berbagi disk).

> 🧭 **Konteks:** abstraksi `api/app/core/storage.py` sudah mendukung dua
> backend — `local` (default, dev/test) dan `r2`. Saat `STORAGE_BACKEND=r2`:
> - Upload disimpan langsung ke bucket (bukan disk api).
> - Download menjawab **302 ke presigned URL** (egress R2 gratis, tanpa
>   lewat api).
> - Pipeline worker mengunduh original → proses → meng-upload hasil ke
>   bucket (bekerja tanpa volume bersama).
> - Retensi FR-07, hapus admin, & hapus akun menghapus object dari bucket.

---

## 1. Buat bucket & API token di Cloudflare

1. Login ke **dash.cloudflare.com** → menu ☰ → **R2 Object Storage**.
2. **Create bucket** → nama mis. `jernihai` (bebas; isi `R2_BUCKET` sama).
3. Buka bucket → **Settings**:
   - *(Opsional)* **Custom Domains** → tambahkan domain Anda (mis.
     `cdn.jernihai.example.com`) agar unduhan lewat CDN Cloudflare. Tanpa
     ini, unduhan tetap jalan via presigned URL (egress tetap gratis).
   - *(Opsional)* **Object Lifecycle Rules** — lapisan pengaman ekstra di
     samping retensi aplikasi (FR-07). Mis. hapus object `storage/uploads/*`
     setelah 24 jam & `storage/results/*` setelah 7 hari.
4. Buat **API Token** (R2 → kanan atas → **Manage R2 API Tokens** →
   **Create API Token**):
   - Scope: **Object Read & Write** (read/write object).
   - Bucket: pilih bucket yang dibuat (atau *all buckets*).
   - Catat **Access Key ID** dan **Secret Access Key** — secret hanya
     tampil **sekali**.

> 🔑 **Keamanan:** Access Key ID + Secret Access Key = **RAHASIA** — jangan
> commit ke git. `R2_SECRET_ACCESS_KEY` cukup kuat untuk menulis & membaca
> seluruh bucket.

---

## 2. Isi env

Isi di file **`.env` root repo** (sudah di-`.gitignore`; template di
`.env.example`):

```dotenv
# --- Cloudflare R2 ---
STORAGE_BACKEND=r2
# Account ID Cloudflare: dashboard → kanan atas (32 karakter alfanumerik).
R2_ACCOUNT_ID=1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d
R2_ACCESS_KEY_ID=<Access Key ID dari API token>
R2_SECRET_ACCESS_KEY=<Secret Access Key — RAHASIA>
R2_BUCKET=jernihai
```

`docker-compose.yml` sudah meneruskannya ke service `api`, `worker`, dan
`worker-gpu`.

---

## 3. Restart & verifikasi

```bash
# Pre-flight: interpolasi env benar
docker compose config --quiet && echo "OK"

# Restart service yang memakai storage
docker compose up -d --force-recreate api worker
```

### Verifikasi alur penuh

1. Upload gambar via web → job `completed`.
2. **Download** → respons harus **302** (bukan 200 langsung). Cek:
   ```bash
   curl -i -L -b <cookie> https://jernihai.example.com/api/v1/jobs/<id>/download \
     | grep -iE "HTTP/|location"
   # HTTP/1.1 302
   # Location: https://<account>.r2.cloudflarestorage.com/jernihai/storage/results/<id>.webp?X-Amz-...
   ```
   (Tanpa `-L`, lihat header `location` — browser mengikutinya otomatis.)
3. Cek object benar-benar di bucket: buka R2 dashboard → bucket →
   `storage/results/<id>.webp` ada.
4. **Retensi FR-07:** tunggu/uji `purge_expired` — object terhapus dari
   bucket & `result_deleted_at` terisi (test otomatis di
   `api/tests/test_storage.py` menutup perilaku ini).

> 🛡️ **Keamanan cadangan (crash window):** ada jendela sangat kecil antara
> hasil di-upload ke bucket dan baris job di-commit `completed` (worker
> crash tepat di sela). Dalam kasus itu stale-check menandai job `failed`
> & kredit direfund, tapi object hasil di bucket menjadi yatim dan retensi
> aplikasi tidak menyentuhnya (retensi hanya untuk job `completed`).
> **Lifecycle Rule R2** untuk prefiks `storage/results/*` (mis. hapus
> setelah 30 hari) adalah jaring pengaman yang disarankan untuk kasus ini.

> 💡 Saat produksi, log `api` menulis warning non-fatal bila
> `STORAGE_BACKEND=r2` tapi kredensial tidak lengkap (`R2_ACCOUNT_ID` dll.)
> — periksa `docker compose logs api | grep -i r2`.

---

## 4. Multi-node (api & worker di host berbeda)

Keuntungan utama R2: **tidak perlu volume bersama**. Konfigurasi tipikal:

- **VPS (api + worker CPU):** compose biasa — `api` upload ke bucket,
  `worker` membaca & menulis bucket.
- **GPU Vast.ai (worker-gpu):** image worker dengan env R2 yang sama
  (`STORAGE_BACKEND=r2` + kredensial) — tanpa bind mount storage bersama
  (lihat `docs/DEPLOY_VAST.md` §3). Original diunduh dari bucket ke disk
  worker sementara, hasil di-upload balik, lalu salinan lokal dibersihkan.
- **Opsional custom domain:** `cdn.jernihai.example.com` di R2 → download
  lewat CDN (cache + kecepatan Indonesia).

---

## 5. Troubleshooting

| Gejala | Penyebab & solusi |
|---|---|
| Upload 500 / log `NoSuchBucket` | Bucket belum dibuat atau `R2_BUCKET` salah. Buat di dashboard R2, sesuaikan env. |
| Download 302 tapi URL `403 SignatureDoesNotMatch` | Waktu server api melenceng (presigned URL signature sensitif jam) atau `R2_SECRET_ACCESS_KEY` salah. Cek `date` server & env. |
| Log warning `R2_ACCOUNT_ID/R2_ACCESS_KEY_ID... belum lengkap` | Kredensial tidak lengkap saat `STORAGE_BACKEND=r2`. Isi `.env`, restart `api`. |
| Pipeline gagal `Original tidak ditemukan` | Object original tidak ada di bucket (mis. lifecycle rules terlalu agresif menghapus upload sebelum proses). Cek bucket + aturan lifecycle. |
| Worker GPU lama gagal | Image worker belum di-update dengan dependensi `boto3` — rebuild image (`docker compose --profile gpu up -d --build worker-gpu`). |
| Ingin kembali ke disk lokal | Set `STORAGE_BACKEND=local` (dev) atau `STORAGE_BACKEND=r2` (produksi) — nilai lain ditolak pipeline (`enhance_backend` tidak terkait). |

---

## 6. Catatan desain (ringkas)

- Key R2 = **path relatif** yang sama dengan yang tersimpan di DB
  (`storage/uploads/<id>.png`) — tidak perlu kolom baru, tidak ada migrasi.
- `delete_if_inside` tetap berlaku untuk R2 (guard berbasis prefiks key) —
  path menyimpang tidak bisa menghapus object arbitrer.
- Presigned URL berlaku **1 jam** (download normal jauh lebih cepat dari
  itu); untuk CDN custom domain, unduhan permanen publik sesuai
  pengaturan bucket.
- Test otomatis `api/tests/test_storage.py` menutup kedua backend dengan
  boto3 di-mock (tidak butuh akun R2 asli untuk CI).
