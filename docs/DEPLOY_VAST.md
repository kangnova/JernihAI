# Deploy & Smoke Test Worker GPU di Vast.ai

Runbook Fase 2 — membuktikan pipeline Real-ESRGAN asli (ADR-002) jalan di
GPU dan mengukur KPI **NFR-01** (inference 4x 1080p < 15 detik, warm).

**Kenapa Vast.ai?** Keputusan ADR-001 (Fase 0): long-running worker + Celery,
harga termurah untuk tes sekali jalan (T4 spot ~$0,12–0,15/jam; 4090
~$0,13–0,20/jam spot, ~$0,36 on-demand). Laptop dev tidak bisa inference
(CPU tanpa AVX2 — prd.md §12).

> ⏱️ Smoke test sekali jalan < 1 jam. Setelah selesai **Destroy instance
> seketika** — billing berhenti saat itu juga (Vast billing per detik).

---

## 1. Prasyarat

- Akun [Vast.ai](https://vast.ai) + SSH key dipasang:
  `Settings → Keys → tambahkan isi `id_ed25519.pub`.
- Akun [Docker Hub](https://hub.docker.com) — namespace image + token CI
  (secrets `DOCKERHUB_USERNAME`/`DOCKERHUB_TOKEN`, lihat A1). Login di
  instance hanya wajib bila repo image **privat**; repo publik cukup
  `docker pull` tanpa login.
- Repo sudah dipush (di `origin/main`).

---

## 2. Opsi A — Smoke test mandiri (disarankan, termurah)

### A1. Bangun & push image worker

> 🤖 **Otomatis via CI (disarankan):** GitHub Actions
> (`.github/workflows/release-images.yml`) sudah build + push image worker
> (juga api & web) ke Docker Hub saat tag rilis dibuat — cukup:
>
> ```bash
> git tag v0.1.0 && git push origin v0.1.0
> ```
>
> Hasil: `<USER>/jernihai-worker:v0.1.0` di Docker Hub. Wajib set secrets
> `DOCKERHUB_USERNAME` + `DOCKERHUB_TOKEN` di repo (Settings → Secrets).
> Jalur manual di bawah hanya untuk kasus khusus (image pribadi, branch
> eksperimen, laptop ingin build sendiri).

```bash
docker build -f api/Dockerfile.worker -t <USER>/jernihai-worker:v0.1.0 api
docker login
docker push <USER>/jernihai-worker:v0.1.0
```

> ⚠️ Base image `pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime` ~8 GB — di
> laptop A8 pull-nya lama (sekali saja). Kalau terlalu berat (atau ingin
> tanpa build sama sekali), lihat **A1b**.

### A1b. Tanpa build di mana pun: tarik image hasil CI dari Docker Hub

Kalau CI sudah menghasilkan image (lihat kotak **Otomatis via CI** di A1),
instance Vast **tidak perlu clone repo, build, maupun push** — cukup tarik
image yang sudah ada di Docker Hub:

```bash
docker pull <USER>/jernihai-worker:v0.1.0
```

- Pull di instance Vast sangat cepat (bandwidth datacenter), jauh lebih
  ringan daripada build (base pytorch ~8 GB + kompilasi `basicsr` dari
  source) dan tidak menyentuh laptop sama sekali.
- Repo image **privat**: jalankan `docker login` dulu dengan akun pemilik
  namespace `<USER>`. Repo **publik**: pull tanpa login.
- Saat rent (A2), field **Image** `<USER>/jernihai-worker:v0.1.0` memakai
  image yang sama — Vast menariknya langsung saat instance dibuat.
- Ingin menguji **branch yang belum di-tag** (belum dirilis)? Baru pakai
  build manual di §A1.

### A2. Rent instance

Web UI: **Rent** → filter GPU (T4 16GB atau RTX 4090, spot) → isi:

| Field | Nilai | Keterangan |
|---|---|---|
| **Image** | `<USER>/jernihai-worker:v0.1.0` | Selalu tag eksplisit, jangan `latest` |
| **Disk** | **60 GB** | ⚠️ Default 8–10 GB **gagal unpack** image ~8 GB |
| **Launch mode** | `ssh_direct` | Supaya bisa SSH. Entrypoint celery tidak auto-jalan (tidak masalah utk smoke test) |
| **On-start (opsional)** | `env >> /etc/environment` | Agar env var kebawa ke sesi SSH |

CLI (alternatif) — beri label `smoke-test` agar pemantau biaya bisa
auto-destroy dengan filter label (§5):

```bash
vastai create instance <OFFER_ID> \
  --image <USER>/jernihai-worker:v0.1.0 \
  --disk 60 --runtype ssh_direct --label smoke-test
```

### A3. SSH & jalankan smoke test

Dari panel instance, salin perintah SSH (`ssh -p <PORT> root@<IP>`) lalu:

```bash
cd /app
python scripts/smoke_test_enhance.py --gen-1080p --scale 4 --iters 5
```

Yang diukur (`api/scripts/smoke_test_enhance.py`, memakai jalur produksi
`_get_upsampler` + `_encode_and_save`):

- Device CUDA + nama GPU + VRAM bebas (validasi GPU terdeteksi)
- Waktu load model (sekali per proses, seperti worker)
- Inference **cold** vs **warm** 4x untuk gambar uji 1080p
- KPI NFR-01: `<15 s` warm → script mencetak `OK` / `BELUM`
- Encode WebP q90 (ADR-004) + ukuran output

Uji juga skala 2x dan format lain:

```bash
python scripts/smoke_test_enhance.py --gen-1080p --scale 2 --format jpeg
```

### A4. (Opsional) Verifikasi worker Celery sungguhan

Bila Redis/Postgres sudah tersedia (mis. VPS produksi), jalankan worker dan
hubungkan ke queue yang sama dengan API:

```bash
cd /app
ENHANCE_BACKEND=real \
DATABASE_URL=postgresql+asyncpg://<user>:<pass>@<host>:5432/jernihai \
CELERY_BROKER_URL=redis://<host>:6379/1 \
CELERY_RESULT_BACKEND=redis://<host>:6379/2 \
celery -A app.tasks.worker.celery_app worker --loglevel=info --pool=solo
```

Log harus memuat: `Backend real siap: device=cuda, half=True, tile=512`.
Lalu upload lewat API → polling → download (E2E penuh, hasil asli ML).

### A5. Selesai — matikan instance

Tombol **Destroy** di panel, atau:

```bash
vastai destroy instance <INSTANCE_ID>
```

> 🛡️ Agar tidak terlupakan: jalankan pemantau biaya `vast_cost_monitor.py`
> selama instance hidup — bisa auto-destroy saat melewati ambang (§5).

---

## 3. Opsi B — Full-stack E2E dalam satu instance (opsional)

Semua service (`db`, `redis`, `api`, `worker-gpu`) dalam satu instance agar
bisa diuji end-to-end dari browser tanpa VPS terpisah. Biaya lebih tinggi
(instance lebih besar) — hanya bila perlu bukti lengkap.

1. **Rent instance DENGAN port publik `-p 8000:8000`** (docker_options saat
   rent — tidak bisa ditambahkan setelah instance dibuat), `--disk 60`,
   lalu SSH masuk.
2. Clone repo & start semua service (build worker-gpu di instance):
   ```bash
   git clone https://github.com/kangnova/JernihAI.git && cd JernihAI
   docker compose -f docker-compose.yml -f infra/vast/compose.vast.yml \
     --profile gpu up -d --build
   ```
   Override `infra/vast/compose.vast.yml` memberi `worker-gpu` bind mount
   `./api:/app` supaya berbagi storage (uploads/results) dengan `api` —
   override yang sama juga menambahkan service `beat` (Celery Beat)
   sehingga sweep retensi FR-07 ikut berjalan.
3. Populasi weights (bind mount men-shadow weights yang di-bake):
   ```bash
   docker compose -f docker-compose.yml -f infra/vast/compose.vast.yml \
     --profile gpu run --rm worker-gpu python scripts/download_models.py
   docker compose -f docker-compose.yml -f infra/vast/compose.vast.yml \
     --profile gpu restart worker-gpu
   ```
4. Buka API di browser: instance perlu port publik `-p 8000:8000`
   (via docker_options saat rent). Port eksternal RANDOM di IP publik —
   lihat panel (atau `echo $VAST_TCP_PORT_8000` di dalam container), buka
   `http://<IP>:<PORT>`.
5. E2E: register → upload `samples/noisy_256.png` → polling → download →
   verifikasi hasil (bandingkan dengan versi mock: detail model vs halus).
6. Destroy instance.

> ⚠️ **FR-07 & kolom DB baru:** fitur retensi & consent menambah kolom
> (`original_deleted_at`, `result_deleted_at` di tabel `jobs`;
> `privacy_consent_at` di tabel `users`). Proyek belum memakai Alembic —
> `create_all` hanya membuat tabel BARU. Bila volume Postgres `pgdata`
> sudah ada dari versi lama, jalankan sekali:
> `docker compose down -v` (hapus data dev) atau ALTER TABLE manual, agar
> service yang memakai kolom baru tidak error di runtime.

> **Gap produksi yang diketahui:** `worker-gpu` dan `api` membutuhkan
> storage bersama (bind mount di Opsi B; di produksi multi-node pakai
> Cloudflare R2 — abstraksi `core/storage.py` sudah siap, lihat prd.md §9).

---

## 4. Troubleshooting

| Gejala | Solusi |
|---|---|
| Instance stuck "unpacking" / build gagal disk penuh | `--disk 60` atau lebih (default 8–10 GB tidak cukup untuk image ~8 GB) |
| OOM / VRAM habis saat input besar | Turunkan `TILE_SIZE=400` (atau 256), naikkan `TILE_PAD=20` (prd.md §10) |
| `RuntimeError: different loop` | Sudah di-fix (NullPool, commit `798ec07`) — jangan downgrade |
| `torch.cuda.is_available()=False` | Cek `nvidia-smi`; host Vast biasanya sudah benar, jarang terjadi |
| Env var tidak terlihat di SSH | On-start: `env >> /etc/environment` |
| `basicsr`/`numpy` error saat build | Versi sudah di-pin di extra `gpu` (pyproject). Pastikan `numpy<2` |
| Hasil download 404 di Opsi B | Pastikan `worker-gpu` memakai bind mount dari `compose.vast.yml` (storage bersama) |
| GPU kedetect tapi lambat | Cek `half=True` di log (`Backend real siap`) — FP16 wajib di CUDA |

---

## 5. Checklist biaya (NFR-08: alert biaya cloud)

**Pemantau otomatis** — `infra/vast/vast_cost_monitor.py` (mandiri, stdlib
saja) membaca instance dari CLI `vastai` (atau API langsung dengan
`--api-key`), menghitung umur & perkiraan biaya tiap instance
(`dph_total × jam`), lalu **alert bila ada yang melewati ambang**:

> 🔑 **API key:** skrip otomatis membaca `VAST_API_KEY` dari file `.env`
> di root repo (sudah di-`.gitignore`) atau env. Jangan pernah commit key
> — detail di GUIDE_VAST_ACCOUNT.md §5e.

```bash
# Cek sekali (exit code: 0 = aman, 2 = ada yang lewat ambang, 3 = destroy)
python infra/vast/vast_cost_monitor.py --max-hours 1 --max-cost 2

# Pantau terus di terminal — alert popup desktop + notifikasi HP via ntfy
python infra/vast/vast_cost_monitor.py --watch --notify-desktop \
  --ntfy-topic jernihai-gpu

# Alert ke HP (ntfy) bila kredit total (credit + balance) turun di bawah $2
python infra/vast/vast_cost_monitor.py --min-credit 2 --ntfy-topic jernihai-gpu

# Auto-destroy (dry-run dulu tanpa --yes; filter label 'smoke')
python infra/vast/vast_cost_monitor.py --auto-destroy --label-contains smoke
python infra/vast/vast_cost_monitor.py --auto-destroy --label-contains smoke --yes

# Output JSON untuk skrip lain
python infra/vast/vast_cost_monitor.py --json
```

- **Ambang default:** umur > 2 jam, biaya > $5, atau **kredit total
  (credit+balance) < $2** (ubah dengan `--max-hours` / `--max-cost` /
  `--min-credit`; `0` menonaktifkan; env `VAST_MONITOR_MIN_CREDIT`).
  Alert kredit berguna supaya tidak kehabisan kredit di tengah sewa
  (instance bisa mati mendadak saat kredit habis).
- **Channel alert:** popup desktop (`--notify-desktop`), ntfy.sh
  (`--ntfy-topic`, bisa ke HP), webhook (`--webhook-url`, format
  `{"text": ...}` cocok untuk Slack/Discord). Semua via env
  `VAST_MONITOR_*` juga bisa.
- **Keamanan auto-destroy:** wajib `--yes` (tanpa itu hanya dry-run), hanya
  menyasar instance **aktif** yang lewat ambang, **tidak pernah** label
  `prod` (kecuali `--allow-prod`), dan bisa dipersempit dengan
  `--label-contains`. Beri label saat rent — field **Label** di Web UI
  atau `--label smoke-test` di CLI (A2) — supaya filter ini efektif;
  tanpa filter, auto-destroy menyasar semua instance yang lewat ambang.

Integrasikan ke cron / Task Scheduler (cek tiap 15 menit):

```bash
# cron (Linux) — output hanya saat ada masalah; destroy otomatis instance
# 'smoke' + alert ntfy ke HP bila kredit < $2
*/15 * * * * cd /path/to/JernihAI && python infra/vast/vast_cost_monitor.py \
  --quiet --auto-destroy --yes --label-contains smoke --min-credit 2 \
  --ntfy-topic jernihai-gpu >> vast-monitor.log 2>&1
```

Checklist:

- [ ] GPU spot (T4/4090), bukan on-demand — untuk tes sekali jalan
- [ ] Durasi target < 1 jam (build di instance ≠ billing tinggi; GPU idle tetap
      ditagih → jangan biarkan instance nyala tanpa kerja)
- [ ] **Destroy seketika** setelah hasil tercatat — atau biarkan
      `vast_cost_monitor.py --auto-destroy` yang menanganinya
- [ ] Jalankan pemantau (`vast_cost_monitor.py`) selama instance hidup
- [ ] Catat hasil (inference time 4x 1080p, VRAM) ke DECISIONS.md / prd.md §12
      untuk memvalidasi cost model ±Rp 2–6/gambar
