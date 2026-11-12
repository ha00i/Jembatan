# S0 — Survei Production Read-only (Panduan Operator)

**Status per 12 November 2026:** pengumpul metrik dan generator laporan sudah tersedia di repo. **Belum pernah dijalankan pada server production milik pengguna dari sesi ini.** Tidak ada otomatisasi QA, Docker daemon baru, atau deployment yang diaktifkan.

## Tujuan

Mengumpulkan bukti 24 jam untuk menentukan apakah host 8 GB RAM / 4 core dengan Docker production memiliki kapasitas aman untuk Safe Runner lokal. Survei ini mengamati **kondisi host yang sudah berjalan**; ia tidak membuat container, menjalankan test, menjalankan docker compose, memodifikasi konfigurasi production, atau melakukan auto restart.

Script: \`scripts/s0_prod_audit.py\`, Python 3 standard library, dieksekusi **pada host Linux production itu sendiri**, bukan komputer lain, karena ia membaca \`/proc\`, disk mount, dan \`/var/run/docker.sock\` lokal. Menjalankan di dalam container Jembatan akan membaca metrik dengan konteks container dan berpotensi keliru; gunakan shell host.

## Langkah operator — tanpa sudo, tanpa mengubah Docker

Dari direktori hasil clone \`ha00i/Jembatan\` yang terbaru:

\`\`\`bash
# 1. Verifikasi satu snapshot (hanya membaca).
python3 scripts/s0_prod_audit.py snapshot

# 2. Mulai pengumpulan 24 jam (1 sampel/60 detik).
# Terminal perlu tetap hidup; gunakan tmux/screen jika memang sudah tersedia.
python3 scripts/s0_prod_audit.py collect --hours 24 --interval 60 --output .s0-audit/samples.jsonl

# 3. Setelah pengumpulan selesai, pilih NAMA kontainer production
#    yang benar-benar kritis dari hasil snapshot atau 'docker ps'.
#    Ulangi --critical untuk setiap kontainer penting.
python3 scripts/s0_prod_audit.py report --input .s0-audit/samples.jsonl \
  --output .s0-audit/report.json --critical nama-container-production
\`\`\`

**Jangan mengubah izin socket Docker atau menambahkan user ke grup \`docker\` hanya untuk membuat survei berhasil.** Jika akun tidak berhak membaca Docker, audit melaporkan akses Docker tidak tersedia dan readiness ditahan. Grup \`docker\` pada daemon rootful secara praktis memberi privilege setara root; solusinya harus dirancang secara terpisah pada S2.

**Perhatian:** jangan menjalankan collector kedua pada path yang sama. Script menolak timpa file existing. Jika pengumpulan terputus, simpan bukti lama dan mulai file baru; jeda panjang membuat laporan tidak memenuhi baseline utuh.

## Apa yang dikumpulkan?

- Waktu UTC, CPU busy dari delta \`/proc/stat\`, \`MemAvailable\` dan total RAM, kenaikan \`oom_kill\` dari \`/proc/vmstat\`.
- Disk root dan lokasi data Docker (jika tidak terbaca hasil baseline tetap ditahan), \`cgroup v2\`, controller dan status systemd PID 1.
- \`docker info\` **read-only** untuk versi cgroup, driver dan Docker root directory; \`docker ps -a\` **read-only** untuk nama container, state dan string healthcheck.
- **Tidak mengumpulkan** image layers, \`docker inspect\` secrets, env, token GitHub, \`.env\`, IP/port, volume contents, database, private logs atau proses shell aplikasi.

Data mentah disimpan dalam \`.s0-audit/samples.jsonl\`, laporan ringkas di \`.s0-audit/report.json\`. Folder sengaja di-\`gitignore\`; file baru dibuat mode 0600 (folder 0700). Tetap perlakukan nama container dan rincian host sebagai informasi internal. Jangan commit file ini atau mengirim raw sample ke tempat publik.

## Memahami keputusan report

- \`S0_NEEDS_REVIEW\`: data tidak lengkap, baseline tidak memenuhi ambang konservatif, healthcheck belum valid, Docker tidak dapat dibaca, atau kontainer kritis belum dikonfigurasi.
- \`S0_BASELINE_CANDIDATE\`: hanya berarti baseline host memenuhi skrining awal; **tidak berarti otomatis aman menjalankan QA**.
- \`qa_enabled: false\`: **SELALU false**. S2 memerlukan bukti bahwa rootless daemon, cgroup aggregate, firewall, batas disk, dan pemisahan privilege benar-benar bekerja sebelum job apa pun diaktifkan.

Ambang awal: MemAvailable minimum 3 GiB, disk root dan storage Docker ≥15 GiB & ≥20% free, baseline 24 jam tanpa gap >3 menit, Docker read-only selalu tersedia, service critical selalu running + healthy, cgroup v2/systemd/controller CPU/memory/pids tersedia, dan tidak ada OOM. CPU lonjakan tinggi harus dianalisis ulang sebelum jadwal QA ditetapkan. Ini **nilai desain**, bukan angka yang sudah diukur pada servermu.

Container tanpa \`HEALTHCHECK\` akan berstatus \`unknown\` dan tidak bisa dinyatakan safe otomatis. Jangan memasang healthcheck baru selama S0; kebutuhan probe akan ditangani kemudian.

## Bukti verifikasi di development (bukan server production)

Jalankan:

\`\`\`bash
python3 -m unittest discover -s tests -p "test_s0_prod_audit.py" -v
python3 -m py_compile scripts/s0_prod_audit.py
\`\`\`

Pengujian menggunakan sample sintetis dan Docker mock; tidak menyentuh Docker production. Untuk memeriksa kesiapan host sebenarnya, jalankan survei 24 jam dan tinjau \`.s0-audit/report.json\` bersama daftar service kritis.

## Kriteria keluar fase S0

- **SR-001**: sampel 24 jam, gap terdeteksi, bukti CPU/RAM/disk dan daftar service kritis tersimpan privat.
- Faktor kondisi host dan cgroup terdokumentasi.
- Tidak ada perubahan service production.
- Pemilik server meninjau hasil dan menyetujui/tolak tahap S1/S2 secara terpisah.

**Status implementasi setelah dokumen ini:** S0 tooling siap dipasang; S0 baseline production **belum terverifikasi**, S1–S6 belum dikerjakan.