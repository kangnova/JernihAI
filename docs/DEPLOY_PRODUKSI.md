# Deploy Produksi: Full-stack + Pembayaran Midtrans (FR-11) + Admin (FR-13)

Runbook untuk menaikkan JernihAI (db, redis, api, worker, beat, web) ke
server produksi — termasuk **aktivasi pembayaran kredit** via Midtrans Snap
(FR-11) dan **admin dashboard** (FR-13), lalu verifikasi end-to-end di
sandbox sebelum cutover.

> 🧭 **Baca dulu:** runbook ini memakai `docker compose` biasa di satu VPS
> (arsitektur dev/prod tunggal, ADR-001). Deploy worker GPU terpisah ada di
> [DEPLOY_VAST.md](./DEPLOY_VAST.md). Alur akun Vast.ai ada di
> [GUIDE_VAST_ACCOUNT.md](./GUIDE_VAST_ACCOUNT.md). Aktifkan login Google
> (FR-01) di [GUIDE_GOOGLE_OAUTH.md](./GUIDE_GOOGLE_OAUTH.md). Storage
> produksi & multi-node (Cloudflare R2) di [GUIDE_R2.md](./GUIDE_R2.md).
> Skala ke **multi-instance / autoscale** (replica API + beberapa worker
> GPU) di [GUIDE_SCALE.md](./GUIDE_SCALE.md).

---

## 1. Prasyarat

- **VPS** (Ubuntu 22.04+, min. 2 GB RAM, disk 20 GB) dengan Docker +
  Docker Compose terinstal.
- **Domain** (mis. `jernihai.example.com`) yang mengarah ke VPS, dengan
  **HTTPS**. Midtrans **hanya mengirim notifikasi webhook ke URL HTTPS** —
  tanpa HTTPS, pembayaran tidak akan masuk. (Belum punya domain/HTTPS?
  Lihat §8 tentang gateway Nginx + Let's Encrypt, atau coba dulu dengan
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

## 3. Isi env: KEAMANAN + MIDTRANS_* + ADMIN_EMAILS

Semua variabel di bawah diisi di file **`.env` root repo** (sudah
di-`.gitignore`, contoh template di `.env.example`). `docker-compose.yml`
meneruskannya ke service `api` (dan `web` untuk `NEXT_PUBLIC_*`).

```bash
cd /path/to/JernihAI
cp .env.example .env
```

Lalu edit `.env` — **seksi keamanan dulu (wajib)**. Sejak hardening
(`app/core/config.py`), `api` **menolak start (fail-fast)** bila
`ENVIRONMENT=production` dengan `JWT_SECRET` dev/lemah (< 32 byte) atau
`COOKIE_SECURE=false`:

```dotenv
# --- Keamanan (WAJIB — fail-fast memblokir start bila salah) ---
ENVIRONMENT=production
# Secret acak >= 32 byte. Generate:
#   python3 -c "import secrets; print(secrets.token_urlsafe(48))"
JWT_SECRET=<tempel-hasil-generate>
# Sesi hanya lewat HTTPS (cookie Secure).
COOKIE_SECURE=true
# Origin web yang sah (list JSON) — ganti ke domain produksi, bukan localhost.
CORS_ORIGINS=["https://jernihai.example.com"]
# URL publik web (redirect Google OAuth / link callback).
WEB_URL=https://jernihai.example.com
# URL API yang DILIHAT BROWSER — WAJIB domain publik (default localhost
# membuat browser pengunjung memanggil localhost miliknya sendiri).
NEXT_PUBLIC_API_URL=https://jernihai.example.com

# --- Google OAuth (FR-01 — opsional) ---
# Panduan lengkap: docs/GUIDE_GOOGLE_OAUTH.md (Google Cloud Console →
# OAuth client ID → Authorized redirect URI = <WEB_URL>/api/v1/auth/google/callback)
# Kosong = tombol Google nonaktif (HTTP 503).
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
```

> ℹ️ **`NEXT_PUBLIC_API_URL` mengikuti cara akses situs:** dengan gateway
> (§8) nilainya = origin domain (`https://jernihai.example.com`, tanpa
> `/api` — endpoint tetap `/api/v1/...`). Tanpa gateway (akses langsung
> `http://VPS_IP:3000`), nilainya jadi `http://VPS_IP:8000` dan
> `CORS_ORIGINS` ikut menunjuk `http://VPS_IP:3000`. Sesuaikan dengan
> topologi yang dipakai.

Lalu bagian pembayaran & admin:

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
```

> ℹ️ **Format `ADMIN_EMAILS`:** list JSON string, mis.
> `["admin@example.com","bos@example.com"]`. User dianggap admin bila
> emailnya ada di list ini (tanpa kolom DB — FR-13). Login dulu dengan
> email tersebut, lalu link **Admin** muncul di nav dashboard.
>
> ⚠️ **Server key kosong = mode MOCK:** tanpa `MIDTRANS_SERVER_KEY`,
> checkout mengembalikan token Snap palsu dan webhook menolak semua
> notifikasi (403). Pastikan terisi sebelum menguji pembayaran.
>
> 🛡️ **Perilaku fail-fast (hardening):** bila `ENVIRONMENT=production` dan
> ada masalah, container `api` langsung **crash saat start** dengan pesan
> jelas `Konfigurasi produksi tidak aman` di log — ini proteksi, bukan bug.
> Perbaiki `.env`, lalu `docker compose up -d --force-recreate api`.
> Setelah start sukses, `api` juga menulis **warning non-fatal** di log bila
> `ADMIN_EMAILS` kosong, Google OAuth mati, Midtrans masih MOCK, atau CORS
> masih localhost (`log_production_warnings`) — cek sekali lalu lanjut.
>
> 🌐 **HTTPS wajib sebelum `ENVIRONMENT=production`:** cookie `Secure`
> (`COOKIE_SECURE=true`) TIDAK dikirim via HTTP → login rusak total tanpa
> HTTPS, dan `COOKIE_SECURE=false` di produksi justru diblokir fail-fast.
> Jadi urutan yang benar: siapkan domain + TLS (gateway §8) DULU, baru set
> `ENVIRONMENT=production`. Selama belum ada HTTPS, biarkan
> `ENVIRONMENT=development` (fail-fast tidak aktif — aman untuk uji awal
> sandbox via tunnel/IP, sebelum go-live).
>
> 🖥️ **Catatan arsitektur (ADR-001):** compose ini menjalankan `web` dalam
> mode dev (`npm run dev`) — desain dev/prod tunggal yang disengaja.
> Untuk jalur yang lebih hardened, workflow `release-images.yml` membangun
> image Next.js standalone (`NEXT_PUBLIC_API_URL` di-bake saat build dari
> variabel repo) — cukup set `vars.NEXT_PUBLIC_API_URL` di GitHub.

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

# (0) Pre-flight: validasi interpolasi env .env di compose (tanpa side effect)
docker compose config --quiet && echo "OK: config valid"

# (1) Build & start semua service + beat (retensi FR-07 & stale-check NFR-03).
# Service `api` otomatis menjalankan `alembic upgrade head` (ADR-011)
# SEBELUM start — skema DB (users/jobs/transactions) dibuat/di-upgrade
# tanpa kehilangan data. TIDAK perlu `docker compose down -v`.
docker compose up -d --build worker beat web api

docker compose ps   # semua service: Up (healthy untuk db/redis)
```

> 🛡️ **Kalau `api` crash-loop (restart terus):** itu biasanya **fail-fast
> hardening** yang menolak konfigurasi tidak aman. Cek alasan pastinya:
> ```bash
> docker compose logs api | tail -30
> # "Konfigurasi produksi tidak aman — perbaiki sebelum start:" + daftar masalah
> ```
> Perbaiki `.env` (umumnya: `JWT_SECRET` belum diganti, `COOKIE_SECURE`
> masih false, atau `ENVIRONMENT` malah tidak production), lalu
> `docker compose up -d --force-recreate api`. Jangan menonaktifkan
> validator — itu lapis keamanan terakhir sebelum data user masuk.

> 🔄 **Upgrade VPS yang SUDAH berjalan** (menarik commit terbaru): jalankan
> migrasi SEBELUM worker di-restart agar tidak ada worker yang memulai
> polling saat skema belum selesai di-upgrade:
> ```bash
> git pull && docker compose run --rm api alembic upgrade head \
>   && docker compose up -d --build api web worker beat
> ```

- **Web:** http://jernihai.example.com (lewat gateway/HTTPS, §8)
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
   > 💡 Bila saldo tidak masuk, lihat §9 Troubleshooting (paling sering:
   > URL webhook belum HTTPS, signature 403 karena server key salah,
   > atau webhook belum sampai karena domain/port tidak terbuka).

### 6c. Proses gambar pakai kredit

1. Habiskan kuota gratis 3 gambar (atau tunggu reset), lalu upload lagi —
   tombol harusnya memakai **kredit** (badge \"memakai N kredit berbayar\").
2. Halaman `/billing` tidak berubah saldonya saat kuota gratis masih ada;
   job berbayar yang **gagal** harus mengembalikan kredit (cek `/quota`
   → `credit_balance` kembali naik).

### 6d. Cek warning produksi di log (sekali saja)

```bash
docker compose logs api 2>&1 | grep -iE "warn|oauth|mock|cors" | tail -10
```

- **Wajar muncul:** `GOOGLE_CLIENT_ID kosong` HANYA bila login Google
  sengaja tidak dipakai (opsional — lihat GUIDE_GOOGLE_OAUTH.md);
  `RATE_LIMIT_BACKEND=memory` bila belum multi-instance (opsional).
- **TIDAK boleh muncul:** `MIDTRANS_SERVER_KEY kosong` (key sudah diisi §3)
  dan `CORS_ORIGINS ... localhost`.
- **Tidak boleh ada `Konfigurasi produksi tidak aman`** — kalau ada, `api`
  crash saat start (lihat §5). Absennya pesan ini bagus, tapi pastikan juga
  `ENVIRONMENT=production` benar-benar ter-set — kalau tidak, fail-fast
  tidak pernah aktif dan proteksi ini diam-diam mati.

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
> sebaliknya. Keempat variabel keamanan §3 (`ENVIRONMENT`, `JWT_SECRET`,
> `COOKIE_SECURE`, `CORS_ORIGINS`) **tidak boleh diubah** saat cutover —
> hanya bagian Midtrans yang berganti. Kalau tidak sengaja ter-edit,
> fail-fast `api` akan mencegah start dengan konfigurasi tidak aman.

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

> 🔒 **Port `8000` jangan diekspos publik di produksi.** `api` kini
> menjalankan uvicorn dengan `--proxy-headers` (memercayai `X-Forwarded-For`
> dari gateway) agar **rate limit per-IP akurat** di balik nginx
> (NFR-04) — konsekuensinya, client yang bisa mengakses `api:8000`
> **langsung** dapat spoof header `X-Forwarded-For` dan melewati rate limit.
> Di produksi: blokir port 8000 di firewall (ufw/security group) atau hapus
> mapping `8000:8000` dari `docker-compose.yml` — semua trafik masuk lewat
> gateway §8. Mapping port tetap berguna di dev (`http://localhost:8000`).

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
| `api` crash-loop terus dengan log `Konfigurasi produksi tidak aman` | Fail-fast hardening menolak konfigurasi: `JWT_SECRET` masih dev/lemah (< 32 byte), `COOKIE_SECURE=false`, atau `ENVIRONMENT` bukan `production`. Lihat daftar masalah di log, perbaiki `.env`, lalu `docker compose up -d --force-recreate api`. Jangan mematikan validator. |
| Web tidak bisa upload/login di produksi (error API tak sampai) | `NEXT_PUBLIC_API_URL` masih `http://localhost:8000` (browser pengunjung memanggil localhost miliknya). Set ke domain publik di `.env`, restart web: `docker compose up -d --force-recreate web`. |
| `api` start tapi log penuh warning `CORS ... localhost` / `ADMIN_EMAILS kosong` | Non-fatal (log_production_warnings). Tetap perbaiki untuk produksi yang benar: `CORS_ORIGINS` + `WEB_URL` ke domain asli, isi `ADMIN_EMAILS`. |
| `GET /health/metrics` menampilkan `queue.status=error` | Redis broker tidak terjangkau (atau mode eager di dev). Cek `redis` container (`docker compose ps`); metrik lain tetap keluar — endpoint tidak down walau Redis mati (by design). |
| Antrean menumpuk terus (metrik `queue.length` naik) | Tambah worker: `docker compose up -d --scale worker=2` (dan `worker-gpu` di pool GPU) — panduan autoscale di GUIDE_SCALE.md; cek juga failure rate (`queue_monitor.py --json`) untuk membedakan masalah kapasitas vs model. |

---

## 10. Checklist deploy

### Keamanan (hardening — wajib sebelum go-live)

- [ ] `ENVIRONMENT=production` di `.env` (bukan default `development`)
- [ ] `JWT_SECRET` = hasil `secrets.token_urlsafe(48)` (≥ 32 byte, bukan
      default dev) — generate sekali, simpan aman
- [ ] `COOKIE_SECURE=true` (HTTPS aktif — §8)
- [ ] `CORS_ORIGINS=["https://<domain>"]` + `WEB_URL=https://<domain>`
      (bukan localhost)
- [ ] `NEXT_PUBLIC_API_URL=https://<domain>` (URL yang dilihat browser)
- [ ] (Opsional) `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` terisi &
      redirect URI `https://<domain>/api/v1/auth/google/callback` terdaftar
      di Google Console (docs/GUIDE_GOOGLE_OAUTH.md) — tombol Google aktif
- [ ] `api` start tanpa crash-loop (fail-fast tidak memblokir) & log tidak
      memuat `Konfigurasi produksi tidak aman`
- [ ] Storage: `STORAGE_BACKEND=r2` + `R2_ACCOUNT_ID`/`R2_ACCESS_KEY_ID`/
      `R2_SECRET_ACCESS_KEY`/`R2_BUCKET` terisi (GUIDE_R2.md) — download
      menjawab 302 presigned, object ada di bucket
- [ ] Multi-instance: `RATE_LIMIT_BACKEND=redis` (counter dibagi antar
      instance; `memory` hanya untuk single-instance/dev)
- [ ] (Bila perlu) Skala multi-instance: `STORAGE_BACKEND=r2` + `--scale
      api=2 --scale worker=2` — klaim job atomik, konsumsi slot terkunci,
      migrasi aman (GUIDE_SCALE.md); `beat` TETAP 1 instance
- [ ] NFR-08 observability: `GET /api/v1/health/metrics` menjawab 200
      (antrean, throughput, failure rate, latensi) & `queue_monitor.py`
      berjalan via cron (GUIDE_SCALE.md §4) — sinyal autoscale + alert
      ntfy/webhook; batasi akses metrik di gateway (tanpa auth)
- [ ] `docker compose config --quiet` lolos (interpolasi env benar)
- [ ] `POSTGRES_PASSWORD` diganti dari default `jernihai` — port 5432
      **di-publish ke host** (`${POSTGRES_PORT:-5432}:5432`): blokir di
      firewall VPS (ufw/security group) atau hapus mapping port di produksi
      (akses antar-container tetap jalan via jaringan internal compose)

### Pembayaran & fitur (sandbox → produksi)

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
