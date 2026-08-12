# Panduan: Aktifkan Login Google (FR-01)

Panduan langkah-demi-langkah untuk mengaktifkan **Google OAuth** di
JernihAI: buat *OAuth client ID* di Google Cloud Console, isi env, lalu
verifikasi alurnya.

> 🧭 **Konteks:** alur OAuth sudah diimplementasikan di
> `api/app/api/routes/auth.py` (redirect ke Google → callback → tukar code →
> set cookie JWT). Yang perlu kamu lakukan hanya **membuat credential** dan
> **mengisi env**. Selama `GOOGLE_CLIENT_ID` kosong, tombol "Lanjut dengan
> Google" otomatis nonaktif (HTTP 503) dan `api` menulis warning di log.

---

## 1. Prasyarat

- **Akun Google** (bebas `@gmail.com` atau Workspace).
- Akses ke **Google Cloud Console** — <https://console.cloud.google.com>.
- Project di Cloud Console (boleh yang sudah ada; untuk uji awal bisa buat
  project baru agar tidak tercampur).

---

## 2. Buat OAuth Client ID di Google Cloud Console

Google sudah merapikan UI-nya ke **Google Auth Platform** (menu ☰ →
**Google Auth Platform**), dengan tab *Branding / Audience / Clients /
Data Access*. Jalur lama **APIs & Services → OAuth consent screen /
Credentials** masih ada dan setara — ikuti salah satu.

### 2a. Konfigurasi consent screen (sekali per project)

1. Buka **Google Cloud Console** → pilih project.
2. Menu ☰ → **Google Auth Platform** (atau **APIs & Services →
   OAuth consent screen**).
3. **Branding** tab:
   - **App name:** nama yang tampil di layar consent, mis. `JernihAI`.
   - **Support email:** email yang bisa dihubungi user (pakai akun Google
     kamu atau Google Group).
   - **Authorized domains:** domain produksi (mis. `jernihai.example.com`)
     — opsional untuk dasar, tapi wajib bila nanti mau hilangkan warning
     "unverified".
4. **Audience** tab:
   - **User type:** pilih **External** (bisa dipakai `@gmail.com` umum).
     *Internal* hanya untuk user Workspace organisasi sendiri.
   - **Publishing status:** biarkan **Testing** selama pengembangan.
     Tambahkan **Test users** (email yang boleh login) di tab ini.
     > 💡 Karena aplikasi ini hanya memakai scope dasar `openid email
     > profile` (non-sensitive), user **di luar** daftar test users tetap
     > bisa login meski status Testing — mereka hanya melihat layar
     > peringatan "Google belum memverifikasi aplikasi ini". Aman untuk
     > go-live tahap awal; hilangkan warning dengan verifikasi brand bila
     > perlu (tidak wajib untuk scope dasar).
5. **Data Access** tab: scope `openid email profile` sudah default (tidak
   perlu menambah apa pun — scope sensitif/restricted tidak dipakai).

### 2b. Buat Client ID (Web application)

1. Menu ☰ → **Google Auth Platform → Clients** (atau **APIs & Services →
   Credentials → + Create credentials → OAuth client ID**).
2. **Application type:** **Web application**.
3. **Name:** mis. `JernihAI Web`.
4. **Authorized JavaScript origins** (boleh kosong untuk alur server-side
   kita, tapi isi agar aman):
   ```
   https://jernihai.example.com
   http://localhost:3000        # dev lokal
   ```
5. **Authorized redirect URIs** — **bagian paling penting**:
   ```
   https://jernihai.example.com/api/v1/auth/google/callback
   http://localhost:3000/api/v1/auth/google/callback     # dev lokal
   ```
   > ⚠️ **Harus persis** sama dengan nilai `WEB_URL` di `.env` + path
   > `/api/v1/auth/google/callback`. Kalau beda (mis. trailing slash,
   > `http` vs `https`), Google menolak dengan `redirect_uri_mismatch`.
6. Klik **Create**. Muncul **Client ID** dan **Client Secret** — salin
   keduanya (Client Secret hanya tampil sekali; kalau hilang, buat ulang
   atau reset via menu).

---

## 3. Isi env

Isi di file **`.env` root repo** (sudah di-`.gitignore`; contoh template di
`.env.example`):

```dotenv
# --- Google OAuth (FR-01) ---
GOOGLE_CLIENT_ID=xxxxxxxxxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-xxxxxxxxxxxxxxxxxxxx
# WAJIB: origin publik web — redirect_uri callback dibangun dari nilai ini:
#   <WEB_URL>/api/v1/auth/google/callback
WEB_URL=https://jernihai.example.com
```

> 🔑 **Keamanan:** `GOOGLE_CLIENT_SECRET` = **RAHASIA**. JANGAN commit ke
> git atau kirim ke frontend. `docker-compose.yml` sudah meneruskannya ke
> container `api` (`GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`).

### 3a. Dev lokal (laptop, tanpa Docker)

Isi di **`api/.env`** (bukan root `.env`):

```dotenv
GOOGLE_CLIENT_ID=xxxxxxxxxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-xxxxxxxxxxxxxxxxxxxx
WEB_URL=http://localhost:3000
```

Agar callback (`http://localhost:3000/api/v1/auth/google/callback`) sampai
ke backend tanpa gateway, aktifkan rewrite `/api/*` di Next.js:

```bash
# terminal web (Windows bash):
export API_REWRITE_TARGET=http://localhost:8000
npm run dev
```

Dan daftarkan redirect URI `http://localhost:3000/api/v1/auth/google/callback`
di Google Console (langkah 2b.5).

---

## 4. Restart stack & verifikasi

### 4a. Restart

```bash
# produksi (docker compose)
docker compose up -d --force-recreate api web

# cek tidak ada warning GOOGLE_CLIENT_ID kosong lagi
docker compose logs api | grep -iE "oauth|google" | tail -5
```

### 4b. Uji alur manual

1. Buka halaman **Login** web → klik **Lanjut dengan Google**.
2. Browser harus diarahkan ke **accounts.google.com** (bukan menampilkan
   JSON).
3. Pilih akun → kembali ke web **/dashboard** dengan status login.
4. Cek `/api/v1/auth/me` (atau buka `/dashboard`) — user baru ber-`provider:
   "google", `privacy_consent_at: null` sampai banner consent di dashboard
   dikonfirmasi (FR-07).

> 💡 **User Google = user baru?** Kalau emailnya belum ada di DB, dibuat
> otomatis (provider `google`). Kalau sudah ada (mis. daftar via email),
> akun yang sama dipakai dan login langsung sukses. Admin (`ADMIN_EMAILS`)
> yang login via Google tetap dapat akses admin.

### 4c. Uji teknis cepat (curl)

```bash
# 1) Tanpa config → 503 (sebelum isi env)
curl -i http://localhost:8000/api/v1/auth/google | head -1   # HTTP/1.1 503

# 2) Setelah config → 302 ke Google + cookie state CSRF
curl -i -c cookies.txt http://localhost:8000/api/v1/auth/google \
  | grep -iE "HTTP/|location"
# HTTP/1.1 302
# Location: https://accounts.google.com/o/oauth2/v2/auth?client_id=...&redirect_uri=...&state=...
# Set-Cookie: oauth_state=...
```

> 💡 Alur penuh tidak bisa diuji dengan curl saja (butuh browser untuk
> menyelesaikan consent Google). Test otomatis API menutup semua cabang
> penting (503 / 302 / state CSRF / callback sukses) di `api/tests/test_auth.py`.

---

## 5. Troubleshooting

| Gejala | Penyebab & solusi |
|---|---|
| Tombol Google mengarah ke halaman yang menampilkan `{"detail": "Google login belum dikonfigurasi"}` | `GOOGLE_CLIENT_ID` kosong di env yang terbaca container. Isi `.env`, restart `api`. |
| `redirect_uri_mismatch` dari Google | Redirect URI di Google Console ≠ `<WEB_URL>/api/v1/auth/google/callback` persis. Cek: trailing slash, `http` vs `https`, host beda. |
| Login sukses tapi kembali ke halaman API / JSON | `WEB_URL` salah (mis. menunjuk `api` atau port). Set `WEB_URL` = origin web yang benar, restart. |
| `error=access_denied` saat pilih akun | User memilih "Cancel" di layar Google — bukan bug. Coba lagi. |
| Warning "Google belum memverifikasi aplikasi ini" | Normal untuk scope dasar saat status Testing. Tidak memblokir login. Verifikasi brand bila ingin hilangkan. |
| Dev lokal: callback 404 di web:3000 | Rewrite `/api/*` belum aktif. Jalankan web dengan `API_REWRITE_TARGET=http://localhost:8000` (langkah 3a). |
| Error `400 Parameter state OAuth tidak valid` | Alur login dimulai ulang tanpa cookie state (kadaluarsa 10 menit atau cookie terhapus). Cukup klik tombol Google lagi dari awal. |
| User Google tidak bisa akses `/admin` | Email belum masuk `ADMIN_EMAILS` (list JSON) — sama seperti user biasa. |

---

## 6. Catatan keamanan (ringkas)

- `redirect_uri` callback **dibangun dari `WEB_URL`** (bukan header proxy) —
  konsisten dan tidak bergantung pada `X-Forwarded-*`, serta trailing slash
  pada `WEB_URL` otomatis dibersihkan agar tidak menghasilkan `//api/...`
  (lihat `api/app/api/routes/auth.py`).
- **Proteksi login CSRF (`state`):** saat mulai login, backend membuat nilai
  acak, mengirimnya ke Google, dan menyimpannya di cookie `oauth_state`
  (httpOnly, 10 menit, hanya untuk path callback). Callback menolak (400)
  bila `state` dari Google tidak persis sama dengan cookie (perbandingan
  constant-time) — mencegah login paksa via tautan palsu. Cookie dihapus
  setelah dipakai sekali.
- Scope minimal: `openid email profile` — tidak butuh verifikasi aplikasi.
- Cookie sesi JWT di-set dengan atribut Secure/httpOnly saat produksi
  (hardening `COOKIE_SECURE`).
- Akun yang di-*suspend* via admin tetap ditolak meski login Google
  (HTTP 403).
- **Troubleshooting tambahan:** kalau login Google gagal dengan `400
  Parameter state OAuth tidak valid`, itu berarti alur login dimulai dari
  awal (cookie state kadaluarsa 10 menit) — cukup ulangi klik tombol
  Google.
