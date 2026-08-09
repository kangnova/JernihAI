# Panduan Lengkap: Akun Vast.ai + Integrasi ke JernihAI

Panduan dari **nol** — membuat akun, mengisi saldo, menyiapkan akses SSH,
memasang CLI, lalu menautkannya ke pipeline deploy proyek ini.

> 📄 Setelah akun siap, eksekusi deploy-nya mengikuti runbook
> [DEPLOY_VAST.md](./DEPLOY_VAST.md) (smoke test GPU, opsi full-stack, dan
> troubleshooting). Dokumen ini fokus ke **persiapan akun** yang jadi
> prasyaratnya.

---

## 1. Mengapa Vast.ai?

Keputusan ADR-001 (Fase 0): worker Real-ESRGAN berjalan sebagai **Celery
long-running worker** di GPU cloud. Vast.ai adalah pasar GPU sewa per-jam
(termurah untuk beban tes sekali jalan):

| GPU | Spot (interruptible) | On-demand | Kecocokan |
|---|---|---|---|
| NVIDIA **T4** 16 GB | ~$0,12–0,15/jam | ~$0,15–0,25/jam | Cukup untuk Real-ESRGAN x4 (VRAM aman) |
| NVIDIA **RTX 4090** 24 GB | ~$0,13–0,20/jam | ~$0,36–0,45/jam | Lebih cepat, biaya per jam lebih tinggi |

Smoke test 1 gambar sekali jalan biasanya **< 1 jam** → biaya di bawah
$0,50. Billing **per detik**, jadi **Destroy seketika = biaya berhenti**.

---

## 2. Membuat Akun

1. Buka **https://vast.ai** → klik **Sign Up** (atau **Console**).
2. Daftar dengan **email + password**, atau sekali klik via **Google /
   GitHub OAuth**.
3. **Verifikasi email wajib** sebelum bisa menyewa GPU:
   - Cek inbox (dan folder **Spam**) → klik tautan verifikasi.
   - Kalau terlewat: `Settings → Resend Verification Email`.
4. Setelah terverifikasi, kamu masuk ke konsol (`console.vast.ai`).

**Alur pertama kali yang disarankan (urut):** isi saldo (§3) → pasang SSH
key (§4) → (opsional) pasang CLI (§5) → rent instance (§6).

---

## 3. Isi Saldo (Billing)

### 3a. Cek saldo & kredit dulu (mungkin TIDAK perlu deposit)

Sebelum deposit, cek apakah akunmu sudah punya **kredit promo** yang bisa
langsung dipakai untuk sewa (banyak akun baru menerima **$10 credit**):

```bash
vastai show user
# Perhatikan dua kolom:
#   Balance  -> saldo tunai/deposit (0 = belum ada deposit)
#   Credit   -> kredit promo/bonus (mis. 10.00 = $10 siap pakai)
```

> ✅ Status akun proyek ini (Agustus 2026): `Balance = 0`,
> `Credit = 10.00`, `Billing Creditonly = 1`. Artinya kredit $10 tersedia
> dan akun dibatasi hanya memakai kredit (tanpa auto-charge kartu) —
> **langsung lanjut ke §6 (sewa) tanpa deposit**. Bila `create instance`
> gagal dengan pesan saldo tidak cukup, baru lanjut deposit di §3b.
>
> ℹ️ Kredit promo **tidak bisa ditarik kembali** (hanya untuk dipakai
> sewa/storage); begitu habis dan saldo $0, instance otomatis berhenti.

### 3b. Deposit manual (hanya bila perlu)

Vast.ai **tidak punya free tier** — untuk top-up, minimal deposit **$5**
(beberapa metode menetapkan $10; UI akan menampilkan minimum saat
checkout).

1. Di sidebar konsol → **Billing** (atau buka `console.vast.ai/billing`).
2. Klik **Add Credit** (atau *Add Credits*).
3. Pilih metode:
   - **Kartu kredit** — diproses Stripe (Vast tidak menyimpan nomor kartu).
   - **Kripto** — via BitPay / Crypto.com (⚠️ deposit kripto **tidak dapat
     dikembalikan**/non-refundable).
4. Masukkan nominal (min. $5–$10) → selesaikan transaksi.

> 💡 **Aktifkan autobilling** (simpan kartu → top-up otomatis saat saldo di
> bawah ambang). Tanpa backup pembayaran, instance **otomatis di-terminate
> begitu saldo menyentuh $0** — termasuk storage-nya.

**Biaya minimum untuk uji coba JernihAI:** deposit $5 cukup untuk berjam-jam
smoke test (T4 spot < $0,15/jam) dan bahkan sisa untuk tes kedua.

---

## 4. Pasang SSH Key (wajib untuk `ssh_direct`)

Mode `ssh_direct` (dipakai runbook deploy) butuh **SSH public key** di akun
agar kamu bisa masuk ke instance. Windows 10/11 sudah punya OpenSSH
bawaan.

**a. Buat key di laptop (sekali saja, bila belum punya):**

```bash
# Git Bash / PowerShell
ssh-keygen -t ed25519 -C "laptop-jernihai" -f ~/.ssh/id_ed25519
```

- Tanpa passphrase lebih mudah untuk otomatisasi; dengan passphrase lebih
  aman (kamu diminta passphrase saat SSH).
- Hasil: `~/.ssh/id_ed25519` (privat — **jangan pernah dibagikan**) dan
  `~/.ssh/id_ed25519.pub` (publik — boleh di-upload).

**b. Upload public key ke Vast.ai:**

1. Konsol → sidebar **Keys** (atau buka `console.vast.ai/manage-keys`).
2. Cari bagian **SSH Keys** → **`+ New`**.
3. Tempel **isi file** `id_ed25519.pub` → simpan.

> Key ini otomatis disuntikkan ke setiap instance yang kamu sewa nanti.

**c. (Alternatif CLI)** setelah CLI terpasang (§5):

```bash
vastai create ssh-key ~/.ssh/id_ed25519.pub
```

**d. Uji sebelum lanjut** (wajib sudah ada instance — lihat §6):

```bash
ssh -p <PORT> root@<IP>
```

---

## 5. Pasang & Autentikasi CLI `vastai` (disarankan)

CLI memudahkan: cari offer, sewa instance, cek status, destroy — tanpa
klik-klik UI.

**a. Install (Windows, butuh Python 3):**

```bash
pip install vastai
vastai --help   # verifikasi
```

**b. Ambil API key di dashboard:**

1. Konsol → sidebar **Keys** (atau `console.vast.ai/manage-keys`).
2. Bagian **API Keys** → **`+ New`** → beri nama (mis. `laptop`) → generate.
3. ⚠️ **Salin sekarang juga** — hanya ditampilkan sekali.

**c. Autentikasi CLI:**

```bash
vastai set api-key <API_KEY>
vastai show user   # harusnya menampilkan user id, email, saldo, SSH keys
```

**d. Perintah yang sering dipakai:**

```bash
vastai search offers 'rtx 4090'  --limit 5     # cari GPU + harga
vastai create instance <OFFER_ID> --image <USER>/jernihai-worker:v0.1.0 \
  --disk 60 --runtype ssh_direct                # sewa (lihat §6)
vastai show instances                            # daftar & status
vastai ssh --instance-id <ID>                    # masuk SSH via CLI
vastai destroy instance <ID>                     # ⚠️ matikan + stop billing
```

**e. Keamanan API key (penting!) — di mana menyimpannya:**

API key memberi akses penuh ke saldo & instance — perlakukan seperti
password. **Jangan pernah commit/push.** Lokasi aman yang dipakai proyek
ini (urut preferensi):

1. **CLI** — `vastai set api-key <KEY>` → tersimpan di `~/.vastai/`
   (**di luar repo**), otomatis dipakai semua perintah `vastai`.
2. **`.env` root repo** (sudah di-`.gitignore`) — `VAST_API_KEY=<KEY>`;
   skrip `infra/vast/vast_cost_monitor.py` auto-membacanya tanpa CLI.

❌ **Jangan** simpan key di file teks di dalam folder repo (mis.
`api-JernihAI.txt`) — berisiko ikut ter-commit saat `git add -A`.
Hindari juga flag `--api-key <KEY>` di baris perintah (terlihat di Task
Manager / process list) — pakai `.env` atau konfig CLI di atas.

🔄 **Rotasi segera** bila key pernah ter-expose (tertulis polos di folder
repo, terlihat pihak lain, dsb): `console.vast.ai/manage-keys` → API Keys →
hapus yang lama → buat baru → update `.env`/CLI. Murah dan menghapus semua
risiko sisa.

✅ Sebelum push: `git status` — tidak boleh ada file berisi key
(`.env`, `api-JernihAI*.txt`, dll).

---

## 6. Integrasi Penuh ke Proyek JernihAI (alur end-to-end)

Akun + saldo + SSH key siap. Berikut keseluruhan alur dari repositori ini
sampai hasil inference di GPU:

```
┌──────────────┐   tag v0.1.0    ┌──────────────────┐   docker push   ┌─────────────┐
│ GitHub repo  │ ──────────────► │ CI release-images│ ──────────────► │ Docker Hub  │
│ (local push) │                 │ (3 image: api,   │                 │ jernihai-*  │
└──────────────┘                 │  web, worker)    │                 └──────┬──────┘
                                                                             │ pull
┌──────────────┐   SSH + smoke   ┌──────────────────┐   Image field   ┌──────▼──────┐
│ Laptop (dev) │ ◄────────────── │ Instance Vast.ai │ ◄────────────── │ Vast.ai GPU │
│ (mock lokal) │  hasil NFR-01   │ (worker-gpu)     │                 └─────────────┘
└──────────────┘                 └──────────────────┘
```

**Langkah 1 — Siapkan repo GitHub (sekali):**

1. Push repo ke GitHub (sudah dilakukan).
2. **Secrets** di `Settings → Secrets and variables → Actions`:
   - `DOCKERHUB_USERNAME` — username Docker Hub kamu
   - `DOCKERHUB_TOKEN` — access token dari hub.docker.com
     (`Account Settings → Security → New Access Token`), **bukan** password
3. **Variable** (penting untuk image web): `NEXT_PUBLIC_API_URL` = URL API
   produksi, mis. `https://api.jernihai.id` (untuk tes smoke GPU, image
   worker tidak butuh ini).

**Langkah 2 — Rilis image via CI:**

```bash
git tag v0.1.0 && git push origin v0.1.0
```

Workflow `.github/workflows/release-images.yml` otomatis: gate test (API +
Web) → build & push **3 image** ke Docker Hub:
`jernihai-api`, `jernihai-web`, `jernihai-worker` (tag `v0.1.0`, tanpa
`latest`). Pantau di tab **Actions**.

> Rilis pertama: build worker (base pytorch ~8 GB) bisa ~10–20 menit.
> Build berikutnya cepat karena registry cache (`:buildcache`).

**Langkah 3 — Sewa instance GPU (detail di DEPLOY_VAST.md §A2):**

Web UI **Rent** → filter GPU (T4/4090) → isi minimal:

| Field | Nilai |
|---|---|
| **Image** | `<USER>/jernihai-worker:v0.1.0` (hasil CI) |
| **Disk** | **60 GB** (default 8–10 GB gagal unpack image ~8 GB) |
| **Launch mode** | `ssh_direct` |
| **On-start** | `env >> /etc/environment` (opsional, biar env kebawa SSH) |

Atau via CLI (beri label — berguna untuk filter auto-destroy pemantau biaya,
DEPLOY_VAST.md §5):

```bash
vastai search offers 't4' --limit 10          # catat OFFER_ID yang spot
vastai create instance <OFFER_ID> --image <USER>/jernihai-worker:v0.1.0 \
  --disk 60 --runtype ssh_direct --label smoke-test \
  --onstart "env >> /etc/environment"
```

**Langkah 4 — Jalankan smoke test (DEPLOY_VAST.md §A3):**

```bash
ssh -p <PORT> root@<IP>        # dari panel instance
cd /app
python scripts/smoke_test_enhance.py --gen-1080p --scale 4 --iters 5
```

Script memakai **jalur produksi yang sama** (`_get_upsampler` +
`_encode_and_save`): memvalidasi GPU/FP16, mengukur load model, inference
cold vs warm, dan mencetak verdict **KPI NFR-01** (`<15 s` warm untuk 1080p
4x).

**Langkah 5 — Matikan instance (penting!):**

```bash
vastai destroy instance <ID>     # atau tombol Destroy di panel
```

Billing per detik — instance yang nyala tanpa kerja tetap ditagih.

> 🔗 Semua detail eksekusi, troubleshooting (OOM, env SSH, storage 404), dan
> opsi full-stack E2E: **docs/DEPLOY_VAST.md**.

---

## 7. Praktik Hemat Biaya & Keamanan

- ✅ Gunakan **spot (interruptible)** untuk tes sekali jalan — jauh lebih
  murah; tugas kita toleran di-interupsi (smoke test bisa diulang).
- ✅ Set target durasi < 1 jam, lalu **Destroy seketika** setelah hasil
  tercatat.
- ✅ Catat hasil (inference time 4x 1080p, VRAM) ke DECISIONS.md/prd.md §12
  untuk memvalidasi cost model ±Rp 2–6/gambar (NFR-08).
- ⚠️ Jangan biarkan instance idle nyala.
- ⚠️ `id_ed25519` (privat) jangan pernah di-upload/dibagikan; API key Vast.ai
  disimpan CLI di folder home (`~/.vastai/`), **di luar repo** — jangan pernah
  dibagikan atau dimasukkan ke env repo.
- 💡 Aktifkan autobilling atau pantau saldo agar storage tak hilang saat
  saldo $0.
- 🛡️ **Pasang pemantau biaya (NFR-08)** — skrip mandiri
  `infra/vast/vast_cost_monitor.py` (stdlib saja, tanpa dependency)
  menampilkan umur & perkiraan biaya tiap instance, memberi **alert**
  (popup desktop, notifikasi HP via ntfy.sh, atau webhook) bila melewati
  ambang, dan bisa **auto-destroy** agar instance yang selesai dipakai tidak
  terlupakan. Cara pakai + contoh cron: **DEPLOY_VAST.md §5**.

---

## 8. FAQ

**Apakah ada free tier / trial?** Tidak ada free tier tetap, tapi banyak akun
baru menerima **kredit promo $10** yang langsung bisa dipakai untuk sewa
(cek `vastai show user` → kolom `Credit`). Minimal deposit $5–$10 bila
perlu top-up.

**Kartu kredit wajib?** Hampir selalu untuk membuka akses penuh; alternatif
via kripto (BitPay/Crypto.com, non-refundable).

**T4 atau 4090 untuk JernihAI?** T4 sudah cukup (VRAM aman untuk
Real-ESRGAN x4, tiling 512). 4090 hanya bila mau bukti waktu tercepat.

**Kalau saldo habis saat instance nyala?** Instance + storage langsung
di-terminate otomatis. Simpan hasil sebelum saldo kritis. Kredit promo yang
habis juga tidak bisa di-top-up secara manual — deposit baru (§3b) bila
perlu lanjut.

**Repo image privat di Docker Hub, gimana?** Jalankan `docker login` di
instance dengan akun pemilik namespace. Publik cukup pull tanpa login.

**Bisa pakai GPU lain selain Vast.ai?** Bisa — image worker ini standar
Docker + CUDA: bisa jalan di RunPod, Lambda, Paperspace, atau Colab dengan
adjustment kecil. Keputusan ADR-001 menetapkan Vast.ai untuk harga terendah.
