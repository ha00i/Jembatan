# Jembatan

## Branding Jembatan

Identitas visual baru menggunakan siluet jembatan dan node alur kerja
dengan warna **indigo, biru, cyan, dan teal**.

- `assets/jembatan-mark.svg`: ikon untuk sidebar, topbar, dan favicon.
- `assets/jembatan-lockup.svg`: logo horizontal pada halaman awal.
- `index.html`: palet, typografi, spacing, card, dan tampilan mobile yang
  selaras dengan identitas baru.
- `bridge.py`: dua rute aset publik yang eksplisit, bukan direktori statis
  terbuka. `Dockerfile` membundel folder `assets/`.

Tidak ada perubahan pada mekanisme GitHub, pemeriksaan QA, gate deploy, maupun
perintah Compose. Kedua SVG dapat digunakan tanpa memasang font eksternal.
Untuk memeriksa integrasi branding:

```bash
python3 -m unittest discover -s tests -p "test_brand_assets.py" -v
sudo docker compose up -d --build jembatan
```


Alat bantu ChatGPT dan GitHub di servermu. Tanpa API berbayar: kamu yang menempel prompt ke
chat ChatGPT dan menempel balasannya kembali. Sisanya otomatis.

## Menjalankan
1. Buat token GitHub (fine-grained, repo yang dipilih): Contents (baca/tulis), Issues (baca),
   Pull requests (baca/tulis), Metadata (baca). Token classic: centang `repo`.
2. `cp .env.example .env`, isi `GITHUB_TOKEN`. Disarankan mengisi `ADMIN_TOKEN` terpisah untuk login web UI. Jika kosong, secara kompatibilitas lama UI memakai `GITHUB_TOKEN`.
3. Tanpa Docker: `export $(cat .env | xargs) && python3 bridge.py` lalu buka http://192.168.1.2:5111 (atau http://localhost:5111 dari server itu sendiri)
   Dengan Docker: `mkdir -p /srv/jembatan/repos && docker compose up -d --build`

## Alur
1. Centang repo (atau Pilih semua) lalu Sinkron. Bot meng-clone atau fetch semua branch.
2. Pilih repo. Tiap issue dibandingkan dengan branch dan commit, lalu diberi tahap:
   belum ada pekerjaan, ada commit di branch, sudah masuk tapi issue terbuka, selesai.
   Ini tebakan dari nama branch, pesan commit (`#12`), dan status issue. Bukan pengganti review.
3. Buka issue, Buat prompt untuk ChatGPT, tempel ke chat. Balasan berformat
   STATUS / KEKURANGAN / PATCH.
4. Tempel balasan, Terapkan patch. Patch masuk ke branch baru `bridge/issue-N-...`.
   Jalankan QA secara terpisah melalui menu Docker & QA; tes tidak otomatis pada production.
   Jika patch tidak cocok dengan kode terbaru (misalnya `git apply gagal: patch failed: ...php:165`),
   Jembatan akan menampilkan **notifikasi peringatan di pojok kanan bawah**. Notifikasi memuat
   lokasi konflik, panel detail error, tombol **Salin detail** untuk dikirim ke ChatGPT,
   serta tombol tutup. Notifikasi tetap tampil sampai pengguna menutupnya.
5. Push dan buat PR. Setelah ditinjau, pakai perintah merge yang ditampilkan.
6. Deploy hanya dari **folder Compose yang dipetakan untuk repository**, setelah konfirmasi nama repo; jangan jalankan deploy ke semua container.

## Docker & QA — alur sederhana (fitur baru)

Menu utama **Repositories & Issues** dan **Docker & QA**.
Pada Docker & QA kamu bisa:
- Memilih **Docker lokal** (socket sudah di-mount) atau **Remote SSH** (host, username, port, SSH key + known_hosts).
- Klik **Simpan koneksi**, **Tes koneksi**, **Lihat container**.
- Pilih repository, mapping **container QA** + **profil tes tetap** + **path Compose** aplikasi; klik **Run QA** atau **Deploy** setelah konfirmasi.
- QA tidak boleh memakai container production; backend hanya menerima container yang namanya menunjukkan QA, running, non-privileged, dan bukan host-network. Pastikan database/volume juga memang terpisah.
- Deployment hanya pada path mapping dan branch target (main/staging) yang telah disetujui.
- **Satu issue → satu branch tetap** `bridge/issue-N`, PR terbuka dipakai kembali, issue closed tersembunyi kecuali dibuka.
- Untuk branch lama yang sudah merged: klik **Hapus** pada branch atau **Hapus semua branch aman**. Dialog konfirmasi sekarang cukup **Hapus / Batal**, tanpa mengetik; SHA, proteksi branch, dan bukti merge tetap diperiksa ulang oleh backend.

Baca [panduan Docker & QA](docs/SIMPLE-DOCKER-QA.md). Tidak ada API AI berbayar,
auto-merge, auto-deploy, atau klaim bahwa QA pada container existing telah
menguji commit terbaru secara otomatis. Legacy `TEST_CMD` tidak lagi dijalankan
saat apply patch demi mencegah tes bebas memakai Docker socket production.

### QA verbose realtime dan notifikasi

- **Run QA** membuat job di backend dan langsung mengembalikan ID. Jembatan membaca
  status job setiap sekitar 1,2 detik lalu menampilkan tahap, durasi dan
  **stdout/stderr Docker yang benar-benar sudah keluar**. Tidak ada persentase
  palsu; spinner dan bar indeterminate tetap digunakan saat tes berjalan.
- **Log terminal:** scroll otomatis saat berada di posisi bawah, tetapi tidak
  memaksa scroll kalau operator sedang membaca baris sebelumnya. Tombol
  **Salin hasil** dapat dipakai selama QA berjalan atau setelah selesai.
- **PASS / FAIL:** berasal dari exit status perintah QA pada container yang
  dikonfigurasi; bukan bukti bahwa SHA branch terakhir diuji.
- **Satu QA aktif maksimum pada satu instance Jembatan.** Job tetap dapat
  dipantau setelah halaman Docker dimuat ulang, selama backend belum restart.
  Riwayat maksimal 16 repository tersimpan **di memori**, bukan database.
- **Keamanan:** profil dan nama container QA tetap di-allowlist; log maksimal
  sekitar 64 KB terakhir dan token kontrol Jembatan yang diketahui disamarkan.
  Jangan menulis rahasia lain pada output tes. Tidak ada akses shell bebas.
- **Notifikasi:** toast untuk info/sukses/gagal/peringatan dengan animasi,
  tombol tutup, reduced motion, serta kartu Docker berjarak konsisten.

Backend baru: `POST /api/docker/qa/start`, `GET /api/docker/qa/status?repo=owner/name`.
Endpoint QA sinkron lama tetap tersedia untuk kompatibilitas, tetapi
dashboard menggunakan job async yang menampilkan output bertahap.
Polling bukan SSE/WebSocket; output bisa tampak berkelompok jika proses tes
melakukan buffering. Refresh atau restart **backend** saat QA berjalan akan
memutus catatan pekerjaan, karena status job belum persisten.

Pengujian:
```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile bridge.py docker_control.py qa_jobs.py
```

### Branch deploy mengikuti yang tersedia di GitHub

- Pilihan **main/staging** dibaca langsung dari repository GitHub yang dipilih.
  Branch yang tidak ada tidak dapat disimpan sebagai target baru.
- Jika sebelumnya tersimpan `staging` tetapi branch itu telah dihapus,
  Jembatan menampilkan `staging (tidak ditemukan)`, alasan deploy terblokir,
  dan opsi nyata yang ada di GitHub. Pilih target yang benar secara eksplisit,
  lalu **Simpan aplikasi**. Tidak ada pengalihan otomatis ke main.
- API `GET /api/deploy/branches?repo=owner/name` mengembalikan branch
  main/staging yang tersedia, `default_branch` dan status pengaturan lama.
  `GET /api/deploy/readiness` mengembalikan status tidak siap jika target
  sudah hilang, tanpa menampilkan raw GitHub 404.
- Jika repository tidak memiliki `main` maupun `staging`, deployment ditahan
  karena belum ada target yang didukung.

### Deploy terkunci pada branch, SHA, dan QA

- Pilih **main** atau **staging** di pengaturan aplikasi, kemudian **Simpan aplikasi**.
- Jalankan QA pada **checkout Git yang sesuai branch target** di container QA.
  Jembatan memeriksa `git rev-parse HEAD` serta perubahan tracked files sebelum
  dan sesudah tes, lalu mencocokkannya dengan SHA GitHub branch target.
- QA bisa **PASS tetapi SHA belum terverifikasi** jika container tidak memiliki
  Git checkout, sumbernya kotor, atau commitnya berbeda. Status tersebut tidak
  mengizinkan deploy.
- Tombol **Periksa kesiapan** menampilkan branch, SHA dan alasan kelayakan.
  Deploy ditolak pada backend bila SHA terbaru dan bukti QA tidak cocok.
- Untuk local Docker: verifikasi folder mapping Git, branch aktif, kebersihan
  checkout; `git fetch`, `git pull --ff-only origin <branch>`, cek HEAD
  hasil pull **persis sama SHA yang lulus QA**, lalu `docker compose up -d --build`.
  Mode SSH melakukan pemeriksaan ekuivalen pada host remote.
- Setelah build, hasil `docker compose ps --format json` dipakai untuk menilai
  healthcheck. Tanpa healthcheck terpasang, tampil **belum terverifikasi**,
  bukan klaim healthy. Jika service unhealthy, hasil deploy dianggap gagal.
- Belum ada rollback otomatis; persetujuan deploy selalu manual. Bukti QA
  disimpan **di memori Jembatan**, sehingga restart backend menghilangkan
  eligibility dan perlu menjalankan QA lagi. Status ini bukan bukti kriptografis
  bahwa semua runtime mounted volumes sesuai commit.

## Manajemen branch (fitur baru)

Setelah repository disinkronkan, buka **Manajemen branch** di bawah daftar issue.
Jembatan akan mengambil refs GitHub terbaru dan menampilkan setiap branch, hash commit,
jumlah commit yang di depan/tertinggal dari default branch, tempat branch tergabung,
serta bukti yang digunakan:

- **Riwayat Git:** commit HEAD branch benar-benar menjadi ancestor branch tujuan.
- **PR #N:** pull request terkonfirmasi merged dan SHA branch sama persis dengan SHA terakhir PR (termasuk squash/rebase).
- **Aman dihapus:** sudah tergabung ke *default branch*, tidak terlindungi, tidak punya PR terbuka.
- **Perlu ditinjau:** sudah terdeteksi di branch lain seperti `staging` tetapi belum di default branch.
- **Belum tergabung / dilindungi / aktif:** tombol hapus dinonaktifkan.

Tombol **Hapus** meminta pengetikan ulang nama branch. Backend kemudian melakukan
`git fetch`, mengulang semua pemeriksaan GitHub, dan hanya menghapus remote branch
bila SHA-nya **tidak berubah** menggunakan `git push --force-with-lease`.
Branch default tidak dapat dihapus. Branch lokal yang mungkin masih ada tidak ikut dihapus.
Jika branch sudah berubah, masih dipakai PR terbuka, atau ada aturan proteksi,
penghapusan gagal dan pengguna perlu memuat ulang. Menutup issue **tidak** membuat
branch otomatis aman.

**Catatan:** label aman merujuk keamanan *pembersihan branch* berdasarkan bukti integrasi;
bukan pernyataan bahwa QA/deploy lulus. Bukti PR merged tidak selalu berarti commit SHA
menjadi ancestor karena squash merge. Riwayat PR diperiksa sampai 500 PR tertutup terbaru;
untuk riwayat lebih lama yang tidak bisa dibuktikan, branch ditandai perlu disimpan.

API: `GET /api/branches?repo=owner/name` dan
`POST /api/branches/delete` dengan JSON
`{"repo":"owner/name","branch":"feature","sha":"<full SHA>"}`.
Keduanya memerlukan autentikasi UI.

Uji modul tanpa layanan eksternal: `python -m unittest discover -s tests -v`.
Pengujian integrasi pada GitHub dan pengecekan proteksi branch tetap harus dilakukan
pada repo uji sebelum penghapusan branch produksi.

## S0 — Audit production lokal (collector tersedia, belum dijalankan di servermu)

Sebelum mengaktifkan QA, kumpulkan metrik read-only selama 24 jam pada **host Linux production**, bukan di dalam kontainer Jembatan:

```bash
python3 scripts/s0_prod_audit.py snapshot
python3 scripts/s0_prod_audit.py collect --hours 24 --interval 60 --output .s0-audit/samples.jsonl
# Setelah selesai, ganti nama container sesuai yang benar-benar kritis:
python3 scripts/s0_prod_audit.py report --input .s0-audit/samples.jsonl --critical nama-container-production
```

Bukti disimpan privat pada `.s0-audit/` (diabaikan Git). Monitoring hanya mengakses `/proc` dan perintah Docker read-only; **tidak menjalankan QA, mengubah container, mengubah daemon, atau melakukan deploy**.
Laporan **tidak pernah otomatis mengaktifkan QA**. Lihat [panduan S0 lengkap](docs/S0-PRODUCTION-SURVEY.md).

### Saat survei S0 terhenti atau ingin melihat progres

`Ctrl+C` saat pengumpulan 24 jam sebelumnya menghasilkan `KeyboardInterrupt` dan file
sampel tidak lengkap. Script S0 sekarang menangani penghentian tanpa traceback,
menyimpan sampel, menampilkan nama container yang terbaca, dan memungkinkan melihat progres.

```bash
python3 scripts/s0_prod_audit.py containers
python3 scripts/s0_prod_audit.py status --input .s0-audit/samples.jsonl
```

Jika jeda lebih dari 3 menit, mulai **file baru** agar baseline tetap kontinu.
Untuk pengumpulan tanpa menahan terminal utama, ikuti panduan
[Survei production S0](docs/S0-PRODUCTION-SURVEY.md) (contoh `nohup` dan
pemantauan `status`). `qa_enabled` tetap `false` sampai QA terisolasi
benar-benar dibangun dan diverifikasi.

## Safe Runner — desain server production lokal (belum aktif)

Untuk server production lokal **8 GB RAM / 4 CPU / tanpa VM**, tersedia rancangan
**Safe Runner**: ChatGPT tetap manual copy/paste, workflow patch → guard
SAFE/WAIT/STOP → QA rootless terpisah → bukti QA per commit SHA → PR → merge/deploy
dengan persetujuan. Default **1 job**, maksimum **1 CPU ekuivalen + 1,5 GiB RAM
agregat QA**, dan hanya profil testing ringan setelah survei resource.

- [Arsitektur dan alur lengkap](docs/SAFE-RUNNER-ARCHITECTURE.md)
- [Tahap kerja, SOP, API usulan, dan 18 kriteria penerimaan](docs/SAFE-RUNNER-EXECUTION-PLAN.md)

**Peringatan:** ini masih dokumen rancangan, bukan fitur running.
`TEST_CMD` sekarang masih menggunakan proses Jembatan yang memiliki mount
`docker.sock` production. Jangan menjalankan patch yang tidak tepercaya dengan
mekanisme tersebut; jalur QA harus dipisah dan diuji terlebih dahulu.
Tidak ada perubahan ke Docker production melalui dokumen ini.

## Keamanan
- Bot hanya push ke branch `bridge/*`, tidak pernah ke branch utama.
- UI tidak menerima perintah bebas. Test dan deploy memakai perintah tetap dari `.env`.
- Folder `repos/` milik bot. Jangan edit manual di sana.
- Port 5111 terbuka untuk seluruh jaringan lokal. Jangan di-port-forward ke internet. Batasi lewat
  firewall, contoh: `sudo ufw allow from 192.168.1.0/24 to any port 5111`.
- Lewat http biasa, token akses UI dikirim tanpa enkripsi di jaringan lokal. Untuk akses dari luar rumah/kantor,
  wajib HTTPS (reverse proxy atau Tailscale). Token juga tersimpan di browser yang kamu pakai masuk.
- 10 kali salah token dari satu alamat IP memblokir alamat itu selama 5 menit.
- Pakai fine-grained token yang dibatasi ke repo yang perlu saja, supaya kalau bocor dampaknya kecil.
- Mounting `docker.sock` memberi akses penuh ke Docker host. Pakai hanya di server pribadimu.
- Untuk repo privat, kode yang kamu tempel ke ChatGPT keluar dari servermu. Cek aturan perusahaan.


## Arah produk: Development Control Center

**ChatGPT tetap manual tanpa API berbayar.** Jembatan adalah orchestrator pekerjaan GitHub lokal, bukan klien API model. Pembuatan solusi memakai prompt/balasan yang disalin-tempel. Semua tindakan otomatis selain itu harus memiliki kebijakan dan bukti yang tercatat.

- **Hari ini (sudah ada):** sinkron repository, deteksi issue/branch secara heuristik, prompt, tempel patch, uji opsional, push branch, buat PR, salin perintah merge, deploy Docker atas permintaan. Dashboard kini menampilkan status dengan istilah yang tidak menyamakan commit dengan kelulusan QA.
- **Target tahap berikutnya (belum tersedia):** pelacakan workflow persisten per issue dan commit SHA; QA wajib dan terisolasi; pemeriksaan status CI GitHub; auto-merge hanya jika semua gerbang lulus; deploy berbasis allowlist dengan rollback; audit log progres realtime.
- **Perhatian:** tombol deploy kini memerlukan bukti QA untuk SHA target, lalu menjalankan `docker compose up -d --build`. Itu belum CD pipeline lengkap dan tidak otomatis rollback. Jangan aktifkan pada repo tidak tepercaya.

Lihat [docs/PRODUCT-VISION.md](docs/PRODUCT-VISION.md) untuk state machine dan gerbang penerimaan serta [docs/UI-DESIGN.md](docs/UI-DESIGN.md) untuk arah UX dan verifikasi visual.

### Aturan aman sebelum otomatisasi penuh

1. **Jangan izinkan auto-merge ketika `TEST_CMD` kosong atau hasil tes belum tervalidasi di server.** Versi sekarang masih memungkinkan push/PR tanpa tes; jangan memperlakukannya sebagai QA PASS.
2. Jalankan patch/tes kode tidak tepercaya di runner terisolasi, tanpa `GITHUB_TOKEN`, `ADMIN_TOKEN`, atau Docker socket host.
3. Pisahkan token GitHub dari token login UI lewat `ADMIN_TOKEN`. Hindari membuka port 5111 ke internet; gunakan HTTPS untuk akses jarak jauh.
4. Jangan menjalankan merge berdasarkan kecocokan nama branch saja. Persyaratan minimal: PR terbaru, SHA terbaru, pemeriksaan GitHub, review, dan branch protection.
5. Simpan status workflow dan approval secara persisten agar restart aplikasi tidak memicu tindakan ulang.
