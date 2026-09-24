# Build APK **Arka** (agen), Linux cuma alat

Ini **bukan** Termux + tab WebView.

- Aplikasi yang terpasang: **Arka** (`id.arka.app`)
- Layar utama: chat agen (Plan / Agent / Build)
- Linux/shell dipakai **Arka** waktu `bash` — kamu tidak tinggal di terminal
- Tidak perlu `start-arka.sh`, `git clone`, atau buka Termux

Hapus APK Termux/Arka lama (`com.termux`) supaya tidak campur.

---

## Upload ke GitHub (ganti isi repo lama)

Repo: https://github.com/Dien45/arka-android

1. Di GitHub, hapus folder lama yang tidak dipakai lagi kalau masih ada: `overlay`, `payload`, `setup-termux.sh` (titik tiga file → Delete).
2. **Add file → Upload files**
3. Seret isi baru folder `arka-android` dari PC:
   - `app/`
   - `build.gradle.kts`
   - `settings.gradle.kts`
   - `gradle.properties`
   - `LANGKAH.md`
   - `.github/workflows/build-arka.yml` (kalau `.github` tidak terunggah: **Add file → Create new file**, nama `.github/workflows/build-arka.yml`, tempel isi file itu)
4. Commit ke `main`

File Python agen ada di `app/src/main/python/` (ikut ter-build).

## Actions

1. **Settings → Actions → General** → Allow all actions
2. **Actions → Build Arka → Run workflow**
3. Tunggu hijau (bisa 10–25 menit, pertama kali unduh Android SDK + Python)
4. Artifact **Arka-apk** → `app-debug.apk` → pasang di HP

## Di HP

1. Buka **Arka**
2. Tunggu “Menyalakan agen…”
3. Chat muncul. Isi API di Pengaturan (URL yang HP bisa tembus)
4. Folder proyek default: di dalam app (`…/files/proyek`)

Tidak ada tab Termux. Tidak ketik perintah.

Kalau build merah: buka log **Build APK**, screenshot, kirim ke sini.
