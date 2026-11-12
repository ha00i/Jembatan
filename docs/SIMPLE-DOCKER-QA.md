# Jembatan — Docker & QA sederhana

## Panduan deployment terkini (menggantikan alur deploy lama di bawah)

**Alur nyata:** pilih repo → atur `deploy_branch=main` atau `staging` →
pilih QA container Git checkout yang sama → jalankan QA → periksa kesiapan →
konfirmasi repo → Git fetch/pull --ff-only → verifikasi SHA setelah pull →
Compose build/up → Compose healthcheck.

**Gate server-side wajib:**
1. GitHub branch target mengembalikan commit SHA valid.
2. QA job terakhir PASS untuk repo + container + profil yang sama, dengan
   `git rev-parse HEAD` dan tracked files clean pada container QA sebelum
   dan setelah tes, serta SHA sama dengan commit branch target saat QA.
3. SHA GitHub branch target saat deploy sama dengan SHA yang diuji.
4. Folder deployment punya Git origin yang cocok, tidak dirty, berada di
   branch target, dan HEAD setelah git pull persis sama dengan SHA QA.
5. Docker Compose hanya berjalan setelah semua gate lulus.

**Bila QA PASS tetapi tidak ada Git checkout di container:** readiness menolak
deploy. Ini sengaja, karena hasil tes saja tidak membuktikan kode yang diuji.
Gunakan QA environment yang menyimpan Git checkout untuk commit tersebut.
QA bisa dibangun dari branch target, tetapi pastikan tidak ada akses ke database
production atau volume production. QA/production masih satu daemon Docker; ini
tidak setara sandbox VM untuk kode tidak tepercaya.

**Healthcheck:** status `healthy` hanya jika semua service Compose yang
terdeteksi menyatakan healthy. Service yang tidak punya healthcheck dilaporkan
`unverified`; bukan kegagalan deployment, tetapi operator harus memastikan
kesehatan aplikasi manual. Status `unhealthy` berarti deploy harus diperiksa.
Tidak ada rollback otomatis.

**API terbaru:** `GET /api/deploy/readiness?repo=owner/name` adalah preflight
read-only; `POST /api/deploy` memverifikasi ulang secara server-side.
`POST /api/docker/app` kini menyimpan `deploy_branch` (`main`/`staging`).
Bukti QA job di backend in-memory: restart Jembatan menghapus gate PASS.

---

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



## QA verbose: log aktual saat Docker menjalankan tes

UI QA sekarang menampilkan progres yang **bisa dipertanggungjawabkan**:
waktu berjalan, tahap validasi container, profil tes, dan potongan stdout/stderr
dari proses `docker exec` yang sedang berlangsung. Backend memulai job melalui
`POST /api/docker/qa/start` dan dashboard polling
`GET /api/docker/qa/status?repo=...` tiap sekitar 1,2 detik.

- Status: `queued` → `running` → `passed` atau `failed`.
- Log update tersedia selama pengujian (bukan setelah semua selesai);
  ditampilkan pada terminal scrollable. Log dibatasi 64 KB terakhir.
- Tombol salin log bisa digunakan sebelum pekerjaan selesai.
- Satu job QA aktif maksimum pada instance backend ini (tidak hanya satu tab).
- Browser reload: status diambil lagi dari memory server ketika repo dipilih.
- Restart Jembatan: **job state hilang** dan monitoring job tidak bisa disambung.
- Pelindung: preset tes tetap, inspeksi container QA dan larangan privileged/
  host-network yang sudah ada; tidak menjalankan kode di container production.
- Log output disamarkan untuk token admin/GitHub Jembatan yang diketahui; teks
  aplikasi lain yang sensitif tetap harus dihindari pada output pengujian.

**Batasan:** Tidak ada persentase penyelesaian tes yang terukur, streaming saat
proses di container menahan flush juga tidak dapat dipaksakan, tidak ada cancel
job atau queue persisted, dan belum ada bukti bahwa container menguji commit
SHA GitHub terbaru. Live log tidak berarti QA environment sudah terisolasi dari
database/volume production—operator wajib memeriksanya dahulu.

Untuk meninjau fungsi yang baru ditambahkan jalankan:
```bash
python3 -m unittest discover -s tests -p "test_qa_verbose_jobs.py" -v
python3 -m unittest discover -s tests -p "test_docker_ui_activity_contract.py" -v
```
