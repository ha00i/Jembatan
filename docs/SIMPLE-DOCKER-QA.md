# Jembatan — Docker & QA sederhana

**Status:** implementasi dashboard / endpoint tersedia melalui PR ini; pengujian pada server pengguna belum dijalankan. Untuk server production dengan banyak container, mulai dari **Local Docker**. Jangan melakukan restart atau build production hanya untuk mengetes koneksi.

## Alur harian dalam satu tempat

1. Sinkron repository GitHub; hanya issue **terbuka** ditampilkan secara default. Riwayat issue selesai bisa dibuka melalui tombol.
2. Pilih issue → salin prompt → tempel patch ChatGPT → **Terapkan patch**. Branch bernama tetap \`bridge/issue-N\`, bukan branch acak setiap percobaan.
3. Buka **Docker & QA** → pilih koneksi local → **Tes koneksi** → **Lihat container**.
4. Isi nama **container QA yang memang terpisah** dari production. Misalnya container \`vortex-qa-qa-app-1\` dari daftar server pengguna. Jangan menganggap nama cukup sebagai bukti isolasi: periksa konfigurasi volume dan database terlebih dahulu.
5. Pilih profil tes yang sesuai dan klik **Run QA**. Tes hanya berjalan di container QA yang dipetakan, bukan pada production. Hasil PASS/FAIL adalah **hasil command container**, belum bukti bahwa commit branch tertentu diuji jika kode container belum disinkronkan ke SHA tersebut.
6. Push ke GitHub → Jembatan memakai kembali PR terbuka untuk branch yang sama.
7. Untuk deploy: isi folder **Docker Compose existing** yang berisi repo Git dan file compose. Di UI klik **Deploy** lalu ketik nama repository persis; hanya stack pada path tersebut yang di-update.
8. Setelah PR merged, buka Manajemen branch → **Hapus semua branch aman** → konfirmasi nama repo. Jembatan menghitung ulang bukti merged dan menggunakan lease SHA sebelum menghapus. Branch default / protected / aktif tidak dihapus.

### Koneksi Docker local

Di Jembatan:
- Menu **Docker & QA** → Metode **Docker Lokal** → Simpan → Tes koneksi → Lihat container.
- Jembatan saat ini **sudah memiliki** \`/var/run/docker.sock\` pada Compose. Hal ini memberi proses Jembatan kewenangan besar atas host Docker; hanya gunakan di mesin pribadi/lokal dengan akses UI terbatas dan token \`ADMIN_TOKEN\` terpisah.
- Akun Linux \`dev\` mungkin perlu \`sudo docker ps\` di terminal. Itu **berbeda** dari akses container Jembatan ke socket yang sudah di-mount. Jangan mengubah izin socket hanya demi UI.
- Untuk deploy, folder aplikasi harus dapat diakses container Jembatan melalui bind mount **pada path yang sama di host dan container**. Contoh untuk satu aplikasi tertentu, di \`docker-compose.yml\` Jembatan di bawah \`volumes:\` tambahkan secara manual:
  \`- /mnt/app/nama-app:/mnt/app/nama-app\`
  Gunakan path aktual dan jangan memetakan seluruh \`/\` atau seluruh root data Docker.

### Koneksi Docker remote (SSH)

- Isi **Host/IP**, **Username**, **Port** pada menu, lalu Tes koneksi. Username tidak boleh root.
- Gunakan SSH key + verified \`known_hosts\` di sisi Jembatan, **bukan password yang disimpan browser**. Docker CLI remote menggunakan \`ssh://user@host:port\`.
- Untuk container Jembatan, pasang **direktori SSH khusus** dengan Compose override jika diperlukan. Contoh file \`docker-compose.ssh.yml\` tersedia; sebelum dipakai, siapkan direktori berisi \`id_ed25519\` khusus dengan permission terbatas dan \`known_hosts\` yang diverifikasi.
- Runner remote memerlukan hak Docker milik user SSH tersebut. Ini akses tinggi terhadap server remote; gunakan akun khusus dan batasi jaringan/host.
- Deploy remote menjalankan \`git pull --ff-only && docker compose up -d --build && docker compose ps\` **hanya** pada folder yang didaftarkan, lewat SSH. Folder dan repo harus sudah ada di host remote dan kredensial Git remote harus berfungsi.
- Jangan deploy remote saat key/known_hosts belum dikonfigurasi atau akses server belum teruji.

### Profil QA bawaan

| Pilihan | Perintah tetap (hanya di container QA) |
| --- | --- |
| Laravel | \`php artisan test --no-ansi\` |
| Python | \`python -m unittest discover\` |
| Node | \`npm test\` |
| Rust | \`cargo test --locked\` |

Tidak ada textarea command shell arbitrer; perintah hanya dari preset yang disetujui. Proses QA synchronous, maksimum 5 menit dan menampilkan ringkasan stdout/stderr setelah selesai. **Belum** ada streaming log nyata atau bukti per SHA. Batas penggunaan resource bergantung pada container QA existing; Jembatan tidak memodifikasi kapasitas production atau menjalankan load test.

**Keamanan minimum QA**: container yang dipetakan harus running, namanya mengandung token \`qa\`, tidak privileged dan tidak memakai host network. Itu pemeriksaan minimum, **tidak membuktikan database/volume terpisah**. Jangan pilih container QA yang terhubung ke database production atau shared volume production.

### Deploy

Deploy hanya berjalan jika **path aplikasi spesifik** sudah disimpan, bukan folder clone acak atau semua repository sekaligus. Backend tetap menolak request tanpa \`confirm\` sama persis dengan nama repo. Tidak ada auto-deploy saat PR merged. Operasi deploy saat ini synchronous; log muncul setelah perintah selesai, dan rollback otomatis belum tersedia.

**PENTING:** \`docker compose up -d --build\` tetap operasi production yang dapat memodifikasi/memulai ulang container pada stack tujuan. Backup, pilih aplikasi yang benar, dan jadwalkan deploy secara sadar.

### API (semua memerlukan X-Admin-Token)

| Method | Path | Fungsi |
| --- | --- | --- |
| GET | \`/api/docker/settings?repo=owner/name\` | Koneksi dan mapping per repo tanpa rahasia SSH |
| POST | \`/api/docker/settings\` | Simpan mode local/ssh, username/host/port |
| POST | \`/api/docker/app\` | Simpan repo → container QA + profil + path compose |
| GET | \`/api/docker/connection\` | Docker info read-only |
| GET | \`/api/docker/containers\` | Docker ps read-only |
| POST | \`/api/docker/qa\` | Jalankan preset hanya pada QA container terpilih |
| POST | \`/api/deploy\` | Deploy mapping dengan konfirmasi nama repo |
| POST | \`/api/branches/cleanup\` | Hapus hanya branch yang sudah verifiably merged |

Konfigurasi non-rahasia disimpan pada \`/srv/jembatan/repos/.docker-control.json\` mode file 0600. Token GitHub / SSH key tidak ditulis dalam file konfigurasi ini.

## Yang belum ada

Tidak ada QA generatif yang secara mandiri membuat tes baru dari bahasa alami tanpa API model. Untuk menulis tes khusus, minta ChatGPT menghasilkan kode/prompt tes terlebih dahulu, lalu gunakan salah satu profil yang disetujui. Belum ada streaming log, branch-SHA testing attestation, automated merge gates, rollback otomatis, atau isolasi kuat seperti VM. Jangan memperlakukan hasil QA pada container existing sebagai sertifikat release.

## Verifikasi lokal

\`\`\`bash
python3 -m unittest discover -s tests -v
python3 -m py_compile bridge.py branch_manager.py docker_control.py
\`\`\`

Sebelum mengaktifkan deploy: tes koneksi → cek daftar container QA → verifikasi pemisahan database → uji QA → simpan folder deploy app spesifik → konfirmasi manual.



## UI activity feedback (November 2026)

Dashboard sekarang menampilkan **activity card** saat QA/deploy sedang menunggu
respons API: spinner, animasi bar tanpa persentase palsu, waktu berjalan, dan
status proses. Setelah respons diterima, hasil perintah disimpan dalam state
halaman dan bisa disalin. Notifikasi animasi memiliki kondisi success/error/
warning/info serta tombol tutup. Layout dua kolom merespons menjadi satu kolom
di layar lebih kecil; gerakan dinonaktifkan pada reduced-motion.

**Batasan yang tetap berlaku:** QA Docker saat ini masih *synchronous* sampai
maksimum 5 menit. Frontend **tidak** menampilkan log streaming, tidak tahu tahap
tes individual, dan tidak punya kemampuan membatalkan proses di server.
Animasi bar hanya menunjukkan permintaan masih berjalan. Jika halaman ditutup
atau dimuat ulang, operasi backend tidak menjadi job yang dapat diikuti kembali.
Untuk true progress diperlukan job ID, event/log streaming dan cancellation
di backend dalam pengembangan berikutnya.
