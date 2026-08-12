# Guide: Multi-Instance & Autoscale (NFR-02)

Runbook menaikkan JernihAI ke **lebih dari satu instance** — replica API
dan/atau beberapa worker GPU — tanpa double-process job, double-refund,
over-spend slot, atau migrasi DB yang bertabrakan.

> 🧭 **Konteks:** prd.md Fase 3 ("Scale"), NFR-02 horizontal scale (target
> 100 job simultan). Storage bersama sudah siap via [GUIDE_R2.md](./GUIDE_R2.md);
> rate limit bersama sudah siap via `RATE_LIMIT_BACKEND=redis` (NFR-04).

---

## 1. Prasyarat (wajib sebelum scale)

| Komponen | Kebutuhan | Status |
|---|---|---|
| **Storage** | `STORAGE_BACKEND=r2` + kredensial R2 terisi (GUIDE_R2.md) — worker GPU di instance lain membaca/menulis bucket, **tanpa volume bersama** | ✅ sudah didukung |
| **Rate limit** | `RATE_LIMIT_BACKEND=redis` — counter dibagi antar instance (dengan `memory`, limit efektif = limit × jumlah instance) | ✅ sudah didukung |
| **Broker/backend Celery** | Redis (sudah default) — queue job + result backend dibagi | ✅ |
| **Migrasi DB** | Advisory lock PostgreSQL — dua replica `api` yang start bersamaan tidak tabrakan saat `alembic upgrade head` | ✅ sejak fase ini |
| **Pemrosesan job** | Klaim ATOMIK + guard selesai/gagal (SQL `WHERE status=...`) — redelivery tidak memproses ulang, refund tidak dobel | ✅ sejak fase ini |
| **Konsumsi slot** | `SELECT ... FOR UPDATE` pada baris user — dua request konkuren tidak over-spend kuota/kredit | ✅ sejak fase ini |

Semua prasyarat keamanan lain (JWT, COOKIE_SECURE, CORS) tetap wajib —
lihat docs/DEPLOY_PRODUKSI.md §3.

---

## 2. Skala API (replica FastAPI)

```bash
cd /path/to/JernihAI
# 2 replica API (load balancing internal; expose lewat gateway §DEPLOY_PRODUKSI §8)
docker compose up -d --scale api=2
```

Yang aman secara otomatis:

- **Migrasi**: setiap replica menjalankan `alembic upgrade head` saat start;
  advisory lock PostgreSQL (migrations/env.py) membuat replica kedua menunggu
  yang pertama selesai, lalu no-op.
- **Rate limit**: `RATE_LIMIT_BACKEND=redis` → counter per-IP/per-key dibagi
  (set di `.env`; lihat checklist §5).
- **Job**: pembuatan job + konsumsi slot dikunci per user (`FOR UPDATE`);
  klaim job atomik mencegah duplikasi.
- **Webhook Midtrans**: idempotent per `order_id` (transisi `pending→paid`
  sekali) — aman diterima replica mana pun.

> ⚠️ **Uvicorn `--proxy-headers`:** `api` memercayai `X-Forwarded-For` dari
> gateway. Di produksi jangan ekspos port 8000 langsung (bisa spoof header) —
> semua trafik lewat gateway (DEPLOY_PRODUKSI.md §8).

## 3. Skala Worker (CPU & GPU)

### Worker CPU (mock/auto, `worker` service)

```bash
docker compose up -d --scale worker=2
```

- `task_acks_late` + `worker_prefetch_multiplier=1` (worker.py): ack setelah
  selesai, prefetch 1 — saat worker restart, job yang belum di-ack di-redeliver
  ke worker lain; **klaim atomik memastikan hanya satu yang memproses**.
- `broker_connection_retry_on_startup=True`: worker tidak crash-loop bila
  Redis sempat restart bersamaan.

### Worker GPU (Vast.ai / pool GPU, `worker-gpu`)

Setiap worker GPU di instance terpisah menjalankan pipeline Real-ESRGAN
(CUDA, pool `solo` — jangan ganti ke prefork, lihat prd.md §9 / ADR-001):

```bash
# Di instance GPU: aktifkan profile gpu (DEPLOY_VAST.md)
docker compose --profile gpu up -d --build worker-gpu
# Tambah kapasitas dengan menaikkan --scale worker-gpu=N di pool yang sama
```

- Dengan `STORAGE_BACKEND=r2`, worker GPU **tidak perlu volume bersama**:
  original diunduh dari bucket (`ensure_local`), hasil di-upload balik
  (`publish_result`), salinan lokal dibersihkan (`cleanup_local`).
- Job yang sama tidak diproses 2× oleh dua GPU worker (klaim atomik).

## 4. Autoscale (spike / B2B)

Pola yang disarankan untuk beban spike (mis. kampanye, API B2B):

1. **API replica**: naikkan `--scale api` saat traffic web naik; turunkan saat
   reda (semua state dibagi — stateless aman).
2. **Worker GPU**: tambah instance dari pool on-demand (Vast.ai) saat antrean
   menumpuk; turunkan saat idle (idle GPU = biaya terbuang, NFR-08).
3. **Pemantauan antrean** untuk memutuskan scale — lihat §4 (NFR-08) —
   contoh satu baris:
   ```bash
   python infra/monitor/queue_monitor.py --json   # panjang antrean & failure rate
   ```
   Panjang antrean ~0 = kapasitas cukup; menumpuk terus di atas ambang =
   tambah worker.
4. **Batas yang TIDAK boleh diskalakan:**
   - `beat` (service Celery Beat): **harus tetap 1** — dua beat = jadwal
     retensi/stale-check dobel. Jangan `--scale beat`.
   - Worker GPU tetap `--pool=solo` (CUDA context, ADR-001).
   - Redis/PostgreSQL: scale vertikal dulu; horizontal (replikasi/sharding)
     di luar cakupan MVP.

## 4. Pemantauan untuk keputusan autoscale (NFR-08)

API menyediakan endpoint metrik operasional **`GET /api/v1/health/metrics`**
(JSON, tanpa auth — batasi di gateway untuk produksi):

| Field | Arti | Sinyal autoscale |
|---|---|---|
| `queue.length` | Antrean Celery (Redis `LLEN`) | **Naik**: menumpuk > ambang = tambah worker; **turun**: nyaris 0 = kapasitas berlebih |
| `throughput.completed_1h/_24h` | Job selesai per jendela | Throughput tinggi + antrean naik = GPU bekerja penuh, tambah worker |
| `throughput.failure_rate_24h` | Proporsi gagal 24 jam | Meningkat = cek log worker/model (bukan masalah kapasitas) |
| `latency.avg_processing_seconds_24h` | Rata-rata durasi proses | Bandingkan KPI NFR-01 end-to-end (< 60 s) |
| `jobs.processing` | Job sedang diproses | Perkiraan beban kerja aktif |

Contoh:

```bash
curl -s http://localhost:8000/api/v1/health/metrics | python -m json.tool
```

**Skrip pemantau `infra/monitor/queue_monitor.py`** (stdlib saja, tanpa
dependency — pola sama dengan `vast_cost_monitor.py`) membaca endpoint ini
untuk alert & keputusan scale:

```bash
# Cek sekali (exit code: 0 = sehat, 1 = error, 2 = lewat ambang)
python infra/monitor/queue_monitor.py --api-url http://localhost:8000

# Pantau terus + alert ke HP via ntfy bila antrean > 5 ATAU failure-rate > 20%
python infra/monitor/queue_monitor.py --watch --interval 60 \
  --max-queue 5 --max-failure-rate 0.2 --ntfy-topic jernihai-ops

# Output JSON untuk skrip lain
python infra/monitor/queue_monitor.py --json

# Cron (Linux) / Task Scheduler (Windows) tiap 5 menit:
# */5 * * * * cd /path/to/JernihAI && python infra/monitor/queue_monitor.py \
#   --max-queue 5 --ntfy-topic jernihai-ops >> queue-monitor.log 2>&1
```

> 💡 **Antrean > ambang = tambah worker GPU (autoscale up); failure rate
> tinggi = masalah model/GPU, bukan kapasitas.** Antrean di bawah ambang
> lama = turunkan worker agar GPU tidak idle membuang biaya (NFR-08).

## 5. Checklist aktivasi multi-instance

- [ ] `STORAGE_BACKEND=r2` + `R2_*` terisi (GUIDE_R2.md) — download 302 presigned
- [ ] `RATE_LIMIT_BACKEND=redis` di `.env` (bukan `memory`)
- [ ] `docker compose up -d --scale api=2` → kedua replica `Up` tanpa crash
      (advisory lock migrasi bekerja)
- [ ] `docker compose up -d --scale worker=2` (dan `worker-gpu` di pool GPU)
- [ ] `beat` tetap 1 instance (jangan di-scale)
- [ ] Verifikasi E2E: upload → proses → download normal; dua upload konkuren
      user yang sama tidak over-spend slot (periksa `/quota`)
- [ ] Verifikasi 429 dibagi: pukul rate limit dari satu IP di replica A, request
      berikutnya (lewat replica B) juga 429
- [ ] Port 8000 tidak diekspos publik (firewall / hapus mapping) — gateway saja
- [ ] Log kedua replica bebas `Konfigurasi produksi tidak aman`

## 6. Troubleshooting

| Gejala | Penyebab & solusi |
|---|---|
| Dua API container saling crash saat start | Tanpa advisory lock, `alembic upgrade head` paralel bisa tabrakan. Pastikan memakai versi terbaru (migrations/env.py sudah lock); kalau sudah terlanjur, `docker compose run --rm api alembic upgrade head` sekali lalu start ulang. |
| Job diproses 2× (terlihat di R2) | Biasanya redelivery setelah worker crash — aman: hanya SATU worker yang menang klaim, yang kalah "skipped" tanpa menulis ulang hasil. Cek `job.status` — tidak mungkin dobel `completed` pada job yang sama. |
| Kredit user bertambah 2× setelah 1 job gagal | Bila terjadi, kemungkinan memakai kode sebelum fase ini (tanpa guard `_fail_job`). Update kode + `docker compose up -d --build`. |
| Upload user bisa melebihi slot saat request ganda | Hanya terjadi tanpa `FOR UPDATE` (kode lama). Update + rebuild; perbaikan otomatis untuk request baru. |
| Worker crash-loop "consumer: Cannot connect to redis" | Redis sempat down saat worker start — sudah ditangani `broker_connection_retry_on_startup=True`; pastikan `depends_on` healthcheck redis di compose. |
