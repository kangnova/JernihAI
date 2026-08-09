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
> (`.github/workflows/release-worker.yml`) sudah build + push image saat tag
> rilis dibuat — cukup:
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

CLI (alternatif):

```bash
vastai create instance <OFFER_ID> \
  --image <USER>/jernihai-worker:v0.1.0 \
  --disk 60 --runtype ssh_direct
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
   `./api:/app` supaya berbagi storage (uploads/results) dengan `api`.
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

- [ ] GPU spot (T4/4090), bukan on-demand — untuk tes sekali jalan
- [ ] Durasi target < 1 jam (build di instance ≠ billing tinggi; GPU idle tetap
      ditagih → jangan biarkan instance nyala tanpa kerja)
- [ ] **Destroy seketika** setelah hasil tercatat
- [ ] Catat hasil (inference time 4x 1080p, VRAM) ke DECISIONS.md / prd.md §12
      untuk memvalidasi cost model ±Rp 2–6/gambar
