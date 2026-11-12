# Jembatan

Alat bantu ChatGPT dan GitHub di servermu. Tanpa API berbayar: kamu yang menempel prompt ke
chat ChatGPT dan menempel balasannya kembali. Sisanya otomatis.

## Menjalankan
1. Buat token GitHub (fine-grained, repo yang dipilih): Contents (baca/tulis), Issues (baca),
   Pull requests (baca/tulis), Metadata (baca). Token classic: centang `repo`.
2. `cp .env.example .env`, isi `GITHUB_TOKEN`. Token yang sama dipakai untuk masuk ke web UI
   (tidak ada admin token terpisah). Kalau mau kata sandi UI sendiri, isi `ADMIN_TOKEN` (opsional).
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

## Keamanan
- Bot hanya push ke branch `bridge/*`, tidak pernah ke branch utama.
- UI tidak menerima perintah bebas. Test dan deploy memakai perintah tetap dari `.env`.
- Folder `repos/` milik bot. Jangan edit manual di sana.
- Port 5111 terbuka untuk seluruh jaringan lokal. Jangan di-port-forward ke internet. Batasi lewat
  firewall, contoh: `sudo ufw allow from 192.168.1.0/24 to any port 5111`.
- Lewat http biasa, token dikirim tanpa enkripsi di jaringan lokal. Untuk akses dari luar rumah/kantor,
  wajib HTTPS (reverse proxy atau Tailscale). Token juga tersimpan di browser yang kamu pakai masuk.
- 10 kali salah token dari satu alamat IP memblokir alamat itu selama 5 menit.
- Pakai fine-grained token yang dibatasi ke repo yang perlu saja, supaya kalau bocor dampaknya kecil.
- Mounting `docker.sock` memberi akses penuh ke Docker host. Pakai hanya di server pribadimu.
- Untuk repo privat, kode yang kamu tempel ke ChatGPT keluar dari servermu. Cek aturan perusahaan.
