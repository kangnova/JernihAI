# Runbook: Sewa GPU Pertama di Vast.ai (rent → smoke test → destroy)

Checklist **langkah konkret** untuk smoke test Real-ESRGAN pertama di GPU
Vast.ai — memakai API key yang sudah tersimpan di `.env` (`VAST_API_KEY`).
Sesuai urutan: **siapkan alat → rent instance → SSH + smoke test → destroy**.

> ⏱️ Target sekali jalan **< 1 jam**, biaya **< $1** (T4 spot ~$0,12–0,15/jam).
> Vast billing **per detik** — instance nyala tanpa kerja tetap ditagih.
> Selesai → **Destroy seketika** atau biarkan pemantau biaya yang mengurusnya.

---

## 0. Status awal (dicek Agustus 2026)

| Item | Status | Perlu tindakan? |
|---|---|---|
| API key di `.env` (`VAST_API_KEY`) | ✅ Ada & terverifikasi (tes API real `[]`) | — |
| CLI `vastai` | ❌ Belum terinstall | Ya → **Langkah 1** |
| SSH key (`~/.ssh/id_ed25519.pub`) | ❌ Belum dibuat | Ya → **Langkah 2** |
| Tag rilis `v0.1.0` (image di Docker Hub) | ❌ Belum dibuat | Ya → **Langkah 3** |
| Secrets `DOCKERHUB_USERNAME`/`TOKEN` di GitHub | ❓ Cek di repo | Ya → **Langkah 3** |
| Saldo Vast.ai ≥ $5 | ❓ Cek di konsol | Ya → GUIDE §3 |

> 📄 Detail akun/saldo/SSH key: `docs/GUIDE_VAST_ACCOUNT.md`. Detail teknis
> deploy + troubleshooting: `docs/DEPLOY_VAST.md`.

---

## 1. Install & autentikasi CLI `vastai` (pakai key dari `.env`)

```bash
pip install vastai
vastai --help        # verifikasi terinstall
```

Autentikasi CLI dengan key yang sudah tersimpan — **tanpa menampilkan
key-nya di layar**:

```bash
vastai set api-key "$(grep '^VAST_API_KEY=' .env | cut -d= -f2 | tr -d '\r')"
```

Verifikasi:

```bash
vastai show user     # harusnya menampilkan user id, email, saldo, SSH keys
```

- ✅ Kunci CLI tersimpan di `~/.vastai/` (di luar repo) — aman.
- 🚫 Jangan pernah `vastai set api-key` dengan key diketik manual di
  command line yang terlihat orang (process list). Pakai perintah di atas.

---

## 2. Buat & upload SSH key (wajib untuk mode `ssh_direct`)

```bash
ssh-keygen -t ed25519 -C "laptop-jernihai" -f ~/.ssh/id_ed25519
```

Upload public key ke Vast.ai — salah satu cara:

- **Web UI:** `console.vast.ai/manage-keys` → **SSH Keys** → **`+ New`** →
  tempel isi file `~/.ssh/id_ed25519.pub` → simpan.
- **CLI:**
  ```bash
  vastai create ssh-key ~/.ssh/id_ed25519.pub
  ```

> `id_ed25519` (tanpa `.pub`) = kunci privat — **jangan pernah dibagikan**.

---

## 3. Pastikan image worker ada di Docker Hub (lewat CI)

Image `jernihai-worker:v0.1.0` belum pernah di-build karena tag rilis belum
dibuat. Lakukan:

```bash
# a) Pastikan secrets ter-set di GitHub: Settings → Secrets and variables
#    → Actions → DOCKERHUB_USERNAME + DOCKERHUB_TOKEN
#    (token dari hub.docker.com → Account Settings → Security → New Access Token)

# b) Tag & push → GitHub Actions build + push 3 image ke Docker Hub
git tag v0.1.0
git push origin v0.1.0
```

- Pantau di tab **Actions** → workflow *Release images (api, web, worker)*.
- Rilis pertama: build worker (base pytorch ~8 GB) butuh **~10–20 menit**.
- Hasil yang dicari: `<USER>/jernihai-worker:v0.1.0` di Docker Hub
  (`<USER>` = username Docker Hub kamu = secret `DOCKERHUB_USERNAME`).

> ⚠️ **Repo image harus PUBLIK** (atau isi Docker Username/Password di form
> Rent Web UI untuk repo privat). Kalau repo privat tanpa kredensial,
> instance akan **gagal pull image** dan stuck di status "unpacking" —
> kegagalan yang membingungkan untuk sewa pertama (DEPLOY_VAST.md §A1b).

> Alternatif tanpa CI (hanya bila terpaksa): build manual di laptop
> `docker build -f api/Dockerfile.worker -t <USER>/jernihai-worker:v0.1.0 api`
> lalu `docker push` — berat, tidak disarankan (lihat DEPLOY_VAST.md §A1).

---

## 4. Cari GPU murah (T4 spot — cukup untuk Real-ESRGAN x4)

```bash
vastai search offers 't4' --limit 10
```

- Pilih **spot** (interruptible) — jauh lebih murah untuk tes sekali jalan.
- Catat `OFFER_ID` dari baris yang cocok (baca output kolom
  `dph_total` ≈ $/jam, `disk` ≥ 60 GB, `gpu_name` berisi T4 16GB).
- Ingin lebih cepat (dan berani bayar lebih): ganti ke `'rtx 4090'`.

> Harga acuan (2026): T4 spot ~$0,12–0,15/jam · 4090 spot ~$0,13–0,20/jam.

---

## 5. Rent instance (label `smoke-test` — untuk filter auto-destroy)

```bash
vastai create instance <OFFER_ID> \
  --image <USER>/jernihai-worker:v0.1.0 \
  --disk 60 \
  --runtype ssh_direct \
  --label smoke-test \
  --onstart "env >> /etc/environment"
```

| Parameter | Nilai | Kenapa |
|---|---|---|
| `--image` | `<USER>/jernihai-worker:v0.1.0` | Image hasil CI (langkah 3) |
| `--disk 60` | 60 GB | ⚠️ Default 8–10 GB **gagal unpack** image ~8 GB |
| `--runtype ssh_direct` | `ssh_direct` | Supaya bisa SSH (butuh key langkah 2) |
| `--label smoke-test` | teks bebas | Filter pemantau biaya untuk auto-destroy |
| `--onstart` | `env >> /etc/environment` | Env var kebawa ke sesi SSH |

> 💡 Alternatif Web UI: **Rent** → filter T4/4090 spot → isi field yang sama
> (Image / Disk 60 / Launch mode `ssh_direct` / Label `smoke-test`).

Tunggu sampai instance **running** (pull image ~8 GB dari Docker Hub butuh
beberapa menit):

```bash
vastai show instances
```

---

## 6. SSH masuk & jalankan smoke test

```bash
vastai show instances --raw   # salin ssh_host + ssh_port
ssh -p <PORT> root@<IP>
```

Di dalam instance:

```bash
cd /app
python scripts/smoke_test_enhance.py --gen-1080p --scale 4 --iters 5
```

Yang harus tampil (kriteria sukses):

- `device : cuda` + nama GPU + VRAM bebas ✅ (GPU terdeteksi)
- `half (FP16) : True` ✅
- `inference warm: < 15.00 s` + **`KPI NFR-01 : OK`** ✅
- Encode WebP q90 + ukuran output (ADR-004)

Uji tambahan (opsional):

```bash
python scripts/smoke_test_enhance.py --gen-1080p --scale 2 --format jpeg
```

Foto uji sungguhan (bukan sintetis) — kirim `samples/noisy_256.png` dari
laptop, lalu jalankan:

```bash
# dari laptop (jangan di dalam SSH):
scp -P <PORT> samples/noisy_256.png root@<IP>:/app/
# lalu di dalam SSH:
python scripts/smoke_test_enhance.py /app/noisy_256.png --scale 4
```

> Script memakai **jalur produksi yang sama** dengan worker
> (`_get_upsampler` + `_encode_and_save`) — hasilnya representatif.

---

## 7. Pantau biaya selama instance hidup (opsional tapi disarankan)

Di terminal terpisah di laptop (skrip membaca `.env` otomatis):

```bash
# Pantau terus + alert popup desktop & notifikasi HP (ntfy)
python infra/vast/vast_cost_monitor.py --watch --notify-desktop \
  --ntfy-topic jernihai-gpu

# Atau biarkan auto-destroy saat lewat ambang 1 jam / $2
python infra/vast/vast_cost_monitor.py --auto-destroy \
  --label-contains smoke --max-hours 1 --max-cost 2 --yes
```

> Skrip hanya menyasar instance ber-label `smoke-test` — instance lain
> (apalagi label `prod`) tidak tersentuh. Detail: DEPLOY_VAST.md §5.

---

## 8. Destroy instance — seketika setelah hasil tercatat

```bash
vastai destroy instance <INSTANCE_ID>
```

Verifikasi sudah mati (billing berhenti):

```bash
vastai show instances   # instance tidak muncul lagi
```

> Tidak sempat destroy manual? Pemantau biaya di langkah 7 sudah otomatis
> menghancurkannya saat lewat ambang — cek `vast-monitor.log`.

---

## 9. Catat hasil & evaluasi

- [x] Inference warm 4x 1080p — **10,38 s** (RTX 3060, < 15 s = **OK** per NFR-01)
- [x] Total biaya sesi — **±$0,02** (credit 10,000 → 9,979; rent ~30 mnt, 5 iterasi)
- [x] Update `DECISIONS.md` / prd.md §12 — angka nyata tercatat, cost model
      ±Rp 2–6/gambar terkonfirmasi (NFR-08 ✅)

---

## Eksekusi cepat (copy-paste semua)

```bash
# 1) CLI + auth (pakai key .env, tanpa menampilkan key)
pip install vastai
vastai set api-key "$(grep '^VAST_API_KEY=' .env | cut -d= -f2 | tr -d '\r')"
vastai show user

# 2) SSH key (sekali saja)
ssh-keygen -t ed25519 -C "laptop-jernihai" -f ~/.ssh/id_ed25519
vastai create ssh-key ~/.ssh/id_ed25519.pub

# 3) Rilis image via CI (sekali saja; butuh secrets Docker Hub di GitHub)
git tag v0.1.0 && git push origin v0.1.0

# 4) Cari & sewa (ganti <OFFER_ID>, <USER>)
vastai search offers 't4' --limit 10
vastai create instance <OFFER_ID> --image <USER>/jernihai-worker:v0.1.0 \
  --disk 60 --runtype ssh_direct --label smoke-test \
  --onstart "env >> /etc/environment"

# 5) Tunggu running, lalu SSH
vastai show instances
ssh -p <PORT> root@<IP>

# 6) Di dalam instance — smoke test 4x 1080p (KPI NFR-01)
cd /app && python scripts/smoke_test_enhance.py --gen-1080p --scale 4 --iters 5

# 7) Dari laptop — pantau biaya (terminal lain) & destroy setelah selesai
python infra/vast/vast_cost_monitor.py --watch --notify-desktop
vastai destroy instance <INSTANCE_ID>
```
