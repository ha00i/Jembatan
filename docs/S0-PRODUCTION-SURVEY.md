# S0 — Survei Production Read-only (Panduan Operator)

**Status per 12 November 2026:** pengumpul metrik dan generator laporan sudah tersedia di repo. **Belum pernah dijalankan pada server production milik pengguna dari sesi ini.** Tidak ada otomatisasi QA, Docker daemon baru, atau deployment yang diaktifkan.

## Tujuan

Mengumpulkan bukti 24 jam untuk menentukan apakah host 8 GB RAM / 4 core dengan Docker production memiliki kapasitas aman untuk Safe Runner lokal. Survei ini mengamati **kondisi host yang sudah berjalan**; ia tidak membuat container, menjalankan test, menjalankan docker compose, memodifikasi konfigurasi production, atau melakukan auto restart.

Script: `scripts/s0_prod_audit.py`, Python 3 standard library, dieksekusi **pada host Linux production itu sendiri**, bukan komputer lain, karena ia membaca `/proc`, disk mount, dan `/var/run/docker.sock` lokal. Menjalankan di dalam container Jembatan akan membaca metrik dengan konteks container dan berpotensi keliru; gunakan shell host.

## Langkah operator — tanpa sudo, tanpa mengubah Docker

Dari hasil clone repo `ha00i/Jembatan` terbaru **pada host Linux production**:

```bash
# Periksa akses Docker dan nama container yang BENAR (read-only).
python3 scripts/s0_prod_audit.py containers

# Snapshot satu kali untuk diagnosis (CPU None di snapshot tunggal itu normal).
python3 scripts/s0_prod_audit.py snapshot
```

### Menjalankan survei tanpa menahan terminal utama

Gunakan `tmux` bila tersedia. Jika tidak, kamu bisa menjalankan collector dengan `nohup` seperti ini:

```bash
mkdir -p .s0-audit
chmod 700 .s0-audit
nohup python3 -u scripts/s0_prod_audit.py collect \
  --hours 24 --interval 60 \
  --output .s0-audit/samples-24h.jsonl \
  > .s0-audit/collector.log 2>&1 < /dev/null &
echo "Collector PID: $!"
```

**Perhatian:** file `samples-24h.jsonl` harus belum ada; jika sebelumnya digunakan, pilih nama file baru. Proses yang dimulai menggunakan `nohup` umumnya tetap hidup saat terminal ditutup, tetapi kebijakan sesi Linux tertentu dapat menghentikannya; verifikasi progres secara berkala. Berbeda dengan ChatGPT, collector ini berjalan di **servermu sendiri**, bukan di latar belakang percakapan.

Pantau progres tanpa menghentikan collector:

```bash
python3 scripts/s0_prod_audit.py status --input .s0-audit/samples-24h.jsonl
tail -n 5 .s0-audit/collector.log
```

Setelah 24 jam, pilih nama container production kritis dari hasil `containers`, lalu laporkan:

```bash
python3 scripts/s0_prod_audit.py report \
  --input .s0-audit/samples-24h.jsonl \
  --output .s0-audit/report.json \
  --critical nama-container-YANG-SEBENARNYA
```

**Wajib mengganti** contoh `nama-container-YANG-SEBENARNYA` dengan nama container yang ditampilkan subcommand `containers`. Kalau beberapa container kritis, ulangi `--critical` untuk setiap nama.

### Ketika collector dihentikan dengan Ctrl+C

- Data yang sudah terkumpul **tetap ada**. Script versi baru mencetak ringkasan berhenti tanpa traceback Python.
- Jika kamu melanjutkan **dalam waktu maksimal 3 menit** setelah sampel terakhir, gunakan `collect --resume` dengan `--output` dan `--hours` yang sama. CPU dihitung dari delta sampel, jadi pengukuran pertama boleh `None`.
- Jika jeda lebih dari 3 menit, **jangan gabungkan baseline**. Gunakan nama file output baru; laporan 24 jam memang membutuhkan rangkaian sampel yang kontinu.
- Contoh dari percakapan: 1 sampel dan durasi 0 jam memang menghasilkan `S0_NEEDS_REVIEW`, bukan kerusakan Docker.

### Jika Docker atau disk terbaca unavailable

Jalankan perintah pemeriksaan read-only berikut dari akun yang sama:

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}'
docker info --format 'cgroup={{.CgroupVersion}} driver={{.CgroupDriver}} root={{.DockerRootDir}}'
df -h /
```

- Jika `docker ps` mendapat **permission denied**, account ini tidak mendapat hak baca Docker socket. Jangan otomatis menambahkan akun ke grup `docker` atau mengubah permission `/var/run/docker.sock`; kebijakan monitoring yang aman ditentukan pada S2.
- Jika output Docker berhasil tetapi disk data-root tidak dapat dibaca, hasil tetap *needs review*. Memerlukan pengamatan dengan mekanisme observer read-only yang hak aksesnya dipisahkan.
- Jika `df -h /` menunjukkan ruang kosong di bawah 15 GiB atau 20%, itu masalah kapasitas nyata menurut kebijakan awal; jangan memaksa QA dan jangan menjalankan `docker system prune` secara otomatis.
- Jika `cgroup v2/systemd` tidak terdeteksi, verifikasi pada host Linux (bukan dalam container), karena informasi dari container dapat berbeda.

**Jangan mengubah izin socket Docker atau menambahkan user ke grup `docker` hanya untuk membuat survei berhasil.** Grup `docker` pada daemon rootful secara praktis memberi privilege setara root. Seluruh perubahan host harus melalui review terpisah.

## Apa yang dikumpulkan?

- Waktu UTC, CPU busy dari delta `/proc/stat`, `MemAvailable` dan total RAM, kenaikan `oom_kill` dari `/proc/vmstat`.
- Disk root dan lokasi data Docker (jika tidak terbaca hasil baseline tetap ditahan), `cgroup v2`, controller dan status systemd PID 1.
- `docker info` **read-only** untuk versi cgroup, driver dan Docker root directory; `docker ps -a` **read-only** untuk nama container, state dan string healthcheck.
- **Tidak mengumpulkan** image layers, `docker inspect` secrets, env, token GitHub, `.env`, IP/port, volume contents, database, private logs atau proses shell aplikasi.

Data mentah disimpan dalam `.s0-audit/samples.jsonl`, laporan ringkas di `.s0-audit/report.json`. Folder sengaja di-`gitignore`; file baru dibuat mode 0600 (folder 0700). Tetap perlakukan nama container dan rincian host sebagai informasi internal. Jangan commit file ini atau mengirim raw sample ke tempat publik.

## Memahami keputusan report

- `S0_NEEDS_REVIEW`: data tidak lengkap, baseline tidak memenuhi ambang konservatif, healthcheck belum valid, Docker tidak dapat dibaca, atau kontainer kritis belum dikonfigurasi.
- `S0_BASELINE_CANDIDATE`: hanya berarti baseline host memenuhi skrining awal; **tidak berarti otomatis aman menjalankan QA**.
- `qa_enabled: false`: **SELALU false**. S2 memerlukan bukti bahwa rootless daemon, cgroup aggregate, firewall, batas disk, dan pemisahan privilege benar-benar bekerja sebelum job apa pun diaktifkan.

Ambang awal: MemAvailable minimum 3 GiB, disk root dan storage Docker ≥15 GiB & ≥20% free, baseline 24 jam tanpa gap >3 menit, Docker read-only selalu tersedia, service critical selalu running + healthy, cgroup v2/systemd/controller CPU/memory/pids tersedia, dan tidak ada OOM. CPU lonjakan tinggi harus dianalisis ulang sebelum jadwal QA ditetapkan. Ini **nilai desain**, bukan angka yang sudah diukur pada servermu.

Container tanpa `HEALTHCHECK` akan berstatus `unknown` dan tidak bisa dinyatakan safe otomatis. Jangan memasang healthcheck baru selama S0; kebutuhan probe akan ditangani kemudian.

## Bukti verifikasi di development (bukan server production)

Jalankan:

```bash
python3 -m unittest discover -s tests -p "test_s0_prod_audit.py" -v
python3 -m py_compile scripts/s0_prod_audit.py
```

Pengujian menggunakan sample sintetis dan Docker mock; tidak menyentuh Docker production. Untuk memeriksa kesiapan host sebenarnya, jalankan survei 24 jam dan tinjau `.s0-audit/report.json` bersama daftar service kritis.

## Kriteria keluar fase S0

- **SR-001**: sampel 24 jam, gap terdeteksi, bukti CPU/RAM/disk dan daftar service kritis tersimpan privat.
- Faktor kondisi host dan cgroup terdokumentasi.
- Tidak ada perubahan service production.
- Pemilik server meninjau hasil dan menyetujui/tolak tahap S1/S2 secara terpisah.

**Status implementasi setelah dokumen ini:** S0 tooling siap dipasang; S0 baseline production **belum terverifikasi**, S1–S6 belum dikerjakan.