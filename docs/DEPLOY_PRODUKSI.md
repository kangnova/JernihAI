# Deploy Produksi: Full-stack + Pembayaran Midtrans (FR-11) + Admin (FR-13)

Runbook untuk menaikkan JernihAI (db, redis, api, worker, beat, web) ke
server produksi — termasuk **aktivasi pembayaran kredit** via Midtrans Snap
(FR-11) dan **admin dashboard** (FR-13), lalu verifikasi end-to-end di
sandbox sebelum cutover.

> 🧭 **Baca dulu:** runbook ini memakai `docker compose` biasa di satu VPS
> (arsitektur dev/prod tunggal, ADR-001). Deploy worker GPU terpisah ada di
> [DEPLOY_VAST.md](./DEPLOY_VAST.md). Alur akun Vast.ai ada di
> [GUIDE_VAST_ACCOUNT.md](./GUIDE_VAST_ACCOUNT.md).

---

## 1. Prasyarat

- **VPS** (Ubuntu 22.04+, min. 2 GB RAM, disk 20 GB) dengan Docker +
  Docker Compose terinstal.
- **Domain** (mis. `jernihai.example.com`) yang mengarah ke VPS, dengan
  **HTTPS**. Midtrans **hanya mengirim notifikasi webhook ke URL HTTPS** —
  tanpa HTTPS, pembayaran tidak akan masuk. (Belum punya domain/HTTPS?
  Lihat §7 tentang gateway Nginx + Let's Encrypt, atau coba dulu dengan
  tunnel seperti `cloudflared`.)
- Repo sudah di-`clone` dan di-push ke `origin/main`.
- Akun [dashboard.sandbox.midtrans.com](https://dashboard.sandbox.midtrans.com)
  (sandbox gratis, tanpa dokumen) — untuk uji coba.

> 🕐 **Waktu:** ~30–45 menit sekali jalan untuk sandbox; +5 menit untuk
> cutover produksi.

---

## 2. Ambil Server Key & Client Key Midtrans

Lakukan di **dashboard sandbox** dulu (produksi menyusul di §7):

1. Login ke **https://dashboard.sandbox.midtrans.com**
   (daftar dulu bila belum — gratis, tanpa verifikasi dokumen).
2. Buka menu **Settings → Access Keys**.
3. Salin **Server Key** dan **Client Key**. Toggle `Production` / `Sandbox`
   di halaman yang sama menentukan set key mana yang tampil:
   - **Sandbox keys** — untuk uji coba (default implementasi kita).
   - **Production keys** — baru muncul/dipakai setelah aktivasi akun
     produksi (Midtrans akan meminta dokumen usaha saat aktivasi).

> 🔑 **Keamanan:** **Server Key = RAHASIA** (dipakai webhook + membuat
> token Snap). **JANGAN pernah** commit ke git atau kirim ke frontend.
> Client Key bersifat publik (dipakai frontend Snap), tapi tetap jaga.

---

## 3. Isi env: MIDTRANS_* & ADMIN_EMAILS

Semua variabel di bawah diisi di file **`.env` root repo** (sudah
di-`.gitignore`, contoh template di `.env.example`). `docker-compose.yml`
meneruskannya ke service `api` (dan `web` untuk `NEXT_PUBLIC_*`).

```bash
cd /path/to/JernihAI
cp .env.example .env
```

Lalu edit `.env`:

```dotenv
# --- Pembayaran (FR-11 — Midtrans Snap) ---
# Dari §2 (dashboard sandbox).
MIDTRANS_SERVER_KEY=SB-Mid-server-xxxxxxxxxxxx
MIDTRANS_CLIENT_KEY=SB-Mid-client-xxxxxxxxxxxx
# false = sandbox. Ganti true HANYA saat cutover produksi (§7).
MIDTRANS_IS_PRODUCTION=false

# Dipakai web memilih URL script Snap (sandbox vs produksi).
NEXT_PUBLIC_MIDTRANS_PRODUCTION=false

# --- Admin (FR-13) ---
# Email yang berhak mengakses /admin & endpoint admin (list JSON).
ADMIN_EMAILS=["admin@example.com"]

# --- Wajib di produksi (bukan dev) ---
JWT_SECRET=<ganti-dengan-secret-kuat-acak>
COOKIE_SECURE=true
ENVIRONMENT=production
```

> ℹ️ **Format `ADMIN_EMAILS`:** list JSON string, mis.
> `["admin@example.com","bos@example.com"]`. User dianggap admin bila
> emailnya ada di list ini (tanpa kolom DB — FR-13). Login dulu dengan
> email tersebut, lalu link **Admin** muncul di nav dashboard.
>
> ⚠️ **Server key kosong = mode MOCK:** tanpa `MIDTRANS_SERVER_KEY`,
> checkout mengembalikan token Snap palsu dan webhook menolak semua
> notifikasi (403). Pastikan terisi sebelum menguji pembayaran.

---

## 4. Daftarkan URL webhook di dashboard Midtrans

Agar notifikasi pembayaran sampai ke backend, daftarkan endpoint webhook
kita di dashboard:

1. Login ke **https://dashboard.sandbox.midtrans.com**.
2. Buka menu **Settings → Configuration**.
3. Isi kolom **Payment Notification URL** dengan:
   ```
   https://jernihai.example.com/api/v1/billing/webhook
   ```
   (ganti domain dengan milik Anda).
4. **Save**.

> 🧾 **Detail teknis webhook (FR-11):**
> - Endpoint: `POST /api/v1/billing/webhook` — **publik** (tanpa auth user);
>   keamanannya verifikasi **`signature_key`** = `sha512(order_id +
>   status_code + gross_amount + server_key)` (cocok dengan algoritma resmi
>   Midtrans; lihat `app/core/billing.py`).
> - Idempotent: hanya transisi `pending → paid` sekali (atomic update) —
>   webhook duplikat/retry tidak menggandakan saldo.
> - Status `settlement`/`capture`/`accept` → kredit cair; `expire`/`cancel`/
>   `deny`/`failure` → transaksi ditandai gagal/kedaluwarsa.
> - Server key masih kosong → semua notifikasi ditolak 403 (mode MOCK).
> - Alternatif per-transaksi: param `callbacks.notification_url` saat
>   membuat token Snap (belum dipakai — cukup global URL di dashboard).

---

## 5. Deploy stack

```bash
cd /path/to/JernihAI

# Build & start semua service + beat (retensi FR-07 & stale-check NFR-03).
# Service `api` otomatis menjalankan `alembic upgrade head` (ADR-011)
# SEBELUM start — skema DB (users/jobs/transactions) dibuat/di-upgrade
# tanpa kehilangan data. TIDAK perlu `docker compose down -v`.
docker compose up -d --build worker beat web api

docker compose ps   # semua service: Up (healthy untuk db/redis)
```

> 🔄 **Upgrade VPS yang SUDAH berjalan** (menarik commit terbaru): jalankan
> migrasi SEBELUM worker di-restart agar tidak ada worker yang memulai
> polling saat skema belum selesai di-upgrade:
> ```bash
> git pull && docker compose run --rm api alembic upgrade head \
>   && docker compose up -d --build api web worker beat
> ```

- **Web:** http://jernihai.example.com (lewat gateway/HTTPS, §7)
- **API docs:** https://jernihai.example.com/docs
- **Worker GPU (pipeline asli):** jalankan terpisah dengan `--profile gpu`
  (lihat [DEPLOY_VAST.md](./DEPLOY_VAST.md) §3 — butuh storage bersama:
  bind mount atau R2). Tanpa worker GPU, backend `auto` akan jatuh ke mock.

> ✅ **Migrasi skema otomatis (ADR-011):** `api` memanggil
> `alembic upgrade head` saat start. Migrasi awal punya **guard tabel +
> backfill kolom** — DB lama (era `create_all`) otomatis dilengkapi
> kolom FR-06/07/08/09/11 dan tabel `transactions` tanpa reset data.
> Upgrade manual bila perlu: `docker compose run --rm api alembic upgrade head`.

---

## 6. Verifikasi E2E di sandbox

Alur lengkap: register admin → beli kredit (test card) → webhook → saldo
masuk → proses gambar pakai kredit.

### 6a. Register admin & cek halaman admin

1. Buka web → **Daftar** dengan email yang ada di `ADMIN_EMAILS`
   (centang consent privasi).
2. Login → nav dashboard menampilkan link **Admin** (FR-13).
3. Buka `/admin`: lihat statistik user/job dan riwayat job.

### 6b. Beli kredit & pastikan webhook diterima

1. Buka **Kredit** (nav) → halaman `/billing` menampilkan saldo 0 + 3 paket.
2. Klik **Beli** salah satu paket → modal **Snap** terbuka
   (domain `app.sandbox.midtrans.com`).
3. Pilih metode **Credit Card** → isi kartu uji sandbox:
   - Sukses: `4811 1111 1111 1114` (CVV apa saja 3 digit, expiry masa depan,
     mis. `12/28`)
   - 3DS: `4911 1111 1111 1113`
   - Ditolak: `4111 1111 1111 1112`
   - (Daftar lengkap di docs Midtrans; QRIS/e-wallet sandbox punya tombol
     **Simulate Success/Failure** di popup Snap)
4. Setelah bayar, halaman `/billing` harus menampilkan:
   - Riwayat transaksi berstatus **Lunas**
   - Saldo kredit bertambah sesuai paket
5. **Cek log API** untuk konfirmasi webhook masuk:
   ```bash
   docker compose logs api | grep -iE "webhook|kredit|signature"
   # Harus ada: "Kredit <N> cair untuk user ... (order ...)"
   ```
   > 💡 Bila saldo tidak masuk, lihat §8 Troubleshooting (paling sering:
   > URL webhook belum HTTPS, signature 403 karena server key salah,
   > atau webhook belum sampai karena domain/port tidak terbuka).

### 6c. Proses gambar pakai kredit

1. Habiskan kuota gratis 3 gambar (atau tunggu reset), lalu upload lagi —
   tombol harusnya memakai **kredit** (badge \"memakai N kredit berbayar\").
2. Halaman `/billing` tidak berubah saldonya saat kuota gratis masih ada;
   job berbayar yang **gagal** harus mengembalikan kredit (cek `/quota`
   → `credit_balance` kembali naik).

---

## 7. Cutover ke produksi (Midtrans Production)

Setelah uji sandbox sukses:

1. **Aktivasi akun produksi Midtrans** (dashboard.midtrans.com → Settings →
   Account; Midtrans meminta dokumen usaha — proses review 1–3 hari kerja).
2. Ambil **Production keys** di `Settings → Access Keys` (toggle
   `Production`) — berbeda dari sandbox keys.
3. Perbarui `.env`:
   ```dotenv
   MIDTRANS_SERVER_KEY=Mid-server-xxxxxxxxxxxx        # key PRODUKSI
   MIDTRANS_CLIENT_KEY=Mid-client-xxxxxxxxxxxx
   MIDTRANS_IS_PRODUCTION=true
   NEXT_PUBLIC_MIDTRANS_PRODUCTION=true
   ```
4. **Daftarkan ulang webhook di dashboard PRODUKSI**:
   `Settings → Configuration → Payment Notification URL` =
   `https://jernihai.example.com/api/v1/billing/webhook` (URL sama, tapi
   dilakukan di dashboard produksi).
5. Restart stack:
   ```bash
   docker compose up -d --force-recreate api web
   ```
6. Uji pembayaran **kecil sekali** (paket termurah) dengan kartu asli /
   QRIS asli, verifikasi webhook + saldo di dashboard produksi, lalu ulangi
   §6c.

> 🔒 **Wajib saat produksi:** `JWT_SECRET` kuat + `COOKIE_SECURE=true` +
> HTTPS (gateway §8). Jangan pernah pakai key sandbox di produksi dan
> sebaliknya.

---

## 8. HTTPS & gateway (opsional tapi disarankan)

Arsitektur PRD §9 memakai Nginx gateway di depan `web` + `api`:

```bash
# Aktifkan profile gateway (nginx.conf sudah disiapkan di infra/nginx/)
docker compose --profile gateway up -d gateway
```

- Jalankan di port 80/443 dengan **Let's Encrypt** (certbot) untuk domain
  Anda, atau pasang di balik reverse proxy VPS (Caddy/Nginx) yang sudah
  menangani TLS.
- Pastikan path `/api/*` diteruskan ke `api:8000` dan sisanya ke `web:3000`
  (lihat `infra/nginx/nginx.conf`; sesuaikan nama service/domain bila perlu).

---

## 9. Troubleshooting

| Gejala | Penyebab & solusi |
|---|---|
| Modal Snap error / tidak terbuka | `NEXT_PUBLIC_MIDTRANS_PRODUCTION` tidak sesuai mode akun (sandbox vs produksi) → cek URL script Snap di Network tab (`app.sandbox.midtrans.com` vs `app.midtrans.com`). Pastikan `NEXT_PUBLIC_API_URL` benar. |
| Webhook 403 `Signature tidak valid` | `MIDTRANS_SERVER_KEY` salah/kosong atau tidak sama dengan yang dipakai membuat token Snap. Cek log: `docker compose logs api \| grep webhook`. |
| Webhook 404 di dashboard Midtrans | URL belum HTTPS, atau endpoint salah. Harus `https://<domain>/api/v1/billing/webhook`. Uji manual: `curl -X POST https://<domain>/api/v1/billing/webhook -H "Content-Type: application/json" -d '{"order_id":"x","status_code":"200","gross_amount":"0","signature_key":"0"}'` → harus `403` (bukan 404). |
| Kredit tidak cair padahal status Lunas | Webhook tidak sampai (cek §4 + log). Saldo dicairkan di jalur `paid`; transaksi yang sudah `paid` tidak diproses ulang (idempotent) — cek riwayat `/billing`. |
| `docker compose` error kolom tidak ada | Skema DB tertinggal versi kode. Jalankan migrasi: `docker compose run --rm api alembic upgrade head` (biasanya sudah otomatis di start api). |
| Halaman `/admin` tidak muncul untuk email tertentu | Email belum masuk `ADMIN_EMAILS` (list JSON) atau login dengan email beda. Setelah ubah env, restart: `docker compose up -d --force-recreate api`. |
| Kartu uji ditolak di sandbox | Pakai kartu uji yang benar (4811...1114 sukses; 4111...1112 sengaja ditolak) atau metode lain (QRIS/e-wallet punya tombol simulate di popup Snap). |
| Token Snap `mock-...` muncul | `MIDTRANS_SERVER_KEY` kosong (mode MOCK). Isi key sandbox lalu restart api. |

---

## 10. Checklist deploy

- [ ] `.env` terisi: `MIDTRANS_SERVER_KEY` / `MIDTRANS_CLIENT_KEY` (sandbox
      dulu), `MIDTRANS_IS_PRODUCTION=false`, `NEXT_PUBLIC_MIDTRANS_PRODUCTION=false`
- [ ] `ADMIN_EMAILS=["admin@example.com"]` terisi & akun admin ter-register
- [ ] Webhook `https://<domain>/api/v1/billing/webhook` terdaftar di
      `Settings → Configuration` (dashboard sandbox)
- [ ] `docker compose up -d --build worker beat web api` sukses, semua `Up`
- [ ] E2E sandbox: beli paket → kartu uji sukses → riwayat **Lunas** →
      saldo bertambah → log api memuat `Kredit N cair`
- [ ] Job berbayar gagal → kredit kembali (refund sesuai sumber)
- [ ] Saat produksi: key produksi + `MIDTRANS_IS_PRODUCTION=true` +
      webhook didaftarkan di dashboard produksi + `JWT_SECRET` kuat +
      `COOKIE_SECURE=true` + HTTPS
- [ ] Worker GPU terhubung (pipeline asli, bukan mock) — DEPLOY_VAST.md
