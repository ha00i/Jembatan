# Jembatan

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
4. Tempel balasan, Terapkan dan uji. Patch masuk ke branch baru `bridge/issue-N-...`.
   Bila test gagal, ada prompt lanjutan untuk ditempel ke chat yang sama.
5. Push dan buat PR. Setelah ditinjau, pakai perintah merge yang ditampilkan.
6. Deploy dengan Docker menjalankan `docker compose up -d --build` dari branch utama.

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
- **Target tahap berikutnya (belum tersedia):** pelacakan workflow persisten per issue dan commit SHA; QA wajib dan terisolasi; pemeriksaan status CI GitHub; auto-merge hanya jika semua gerbang lulus; deploy berbasis allowlist, healthcheck dan rollback; audit log progres realtime.
- **Perhatian:** tombol deploy saat ini menjalankan `docker compose up -d --build` setelah konfirmasi. Itu bukan CD pipeline bergate, tidak otomatis rollback. Jangan aktifkan pada repo tidak tepercaya.

Lihat [docs/PRODUCT-VISION.md](docs/PRODUCT-VISION.md) untuk state machine dan gerbang penerimaan serta [docs/UI-DESIGN.md](docs/UI-DESIGN.md) untuk arah UX dan verifikasi visual.

### Aturan aman sebelum otomatisasi penuh

1. **Jangan izinkan auto-merge ketika `TEST_CMD` kosong atau hasil tes belum tervalidasi di server.** Versi sekarang masih memungkinkan push/PR tanpa tes; jangan memperlakukannya sebagai QA PASS.
2. Jalankan patch/tes kode tidak tepercaya di runner terisolasi, tanpa `GITHUB_TOKEN`, `ADMIN_TOKEN`, atau Docker socket host.
3. Pisahkan token GitHub dari token login UI lewat `ADMIN_TOKEN`. Hindari membuka port 5111 ke internet; gunakan HTTPS untuk akses jarak jauh.
4. Jangan menjalankan merge berdasarkan kecocokan nama branch saja. Persyaratan minimal: PR terbaru, SHA terbaru, pemeriksaan GitHub, review, dan branch protection.
5. Simpan status workflow dan approval secara persisten agar restart aplikasi tidak memicu tindakan ulang.
