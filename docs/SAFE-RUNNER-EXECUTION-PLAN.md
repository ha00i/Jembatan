# Safe Runner — Pelaksanaan Bertahap, Acceptance Criteria dan SOP

**Status:** DESAIN / handoff engineering. Dokumen ini tidak memberi izin untuk mengubah Docker production secara otomatis.  
**Tergantung:** [SAFE-RUNNER-ARCHITECTURE.md](SAFE-RUNNER-ARCHITECTURE.md) (aturan keamanan dan state machine).  
**Baseline:** server Linux production lokal, RAM 8 GB, CPU 4 core, Docker aktif, tidak tersedia VM, operator tunggal.

## Ringkasan pendek yang harus dipahami semua pelaksana

**Tidak boleh langsung membuat QA Runner yang menggunakan `/var/run/docker.sock` production**. Jembatan sekarang memiliki mount tersebut dan `TEST_CMD` lama. Sebelum QA otomatis diaktifkan, jalur QA harus dipindahkan ke worker dengan identitas terpisah, cgroup dan jaringan teruji, tanpa kredensial production. Pada hari pertama hanya diperbolehkan monitoring read-only; hasil monitoring menentukan apakah host sanggup menguji secara lokal.

### Status S0 terkini (tooling)

Pengumpul sampel read-only `scripts/s0_prod_audit.py`, 15 unit test sintetis dan panduan
[S0-PRODUCTION-SURVEY.md](S0-PRODUCTION-SURVEY.md) telah ditambahkan pada fase S0.
**Baseline 24 jam pada server production asli belum dijalankan atau diverifikasi**.
Semua jalur QA tetap nonaktif; lanjut ke S1/S2 hanya setelah survei host dan persetujuan operator.

## Urutan implementasi dan siapa mengerjakan

| Fase | Pemilik utama | Pekerjaan | Verifikasi akhir | Gate |
| --- | --- | --- | --- | --- |
| S0 — Survey read-only | Ops/backend + pemilik server | Ukur beban normal 24 jam; OS/kernel/systemd/cgroup; storage; container; healthchecks; audit mount dan secrets | Data bertanggal dan tanpa kredensial | Tidak boleh mengubah daemon produksi |
| S1 — Contract dan persistence | Backend | SQLite job/event store, queue single-flight, read-only monitor API, idempotency SHA/profile, auth & logging | Unit tests restart, duplicate job, stale metrics, invalid profile | Tidak ada eksekusi shell |
| S2 — Separation of privilege | Ops/security | User/daemon Docker rootless QA, tidak punya prod socket/sudo/secret; batas cgroup agregat; isolasi mount/network/egress | Negatif: QA tidak dapat baca prod .env, prod DB, prod Docker API; limit CPU/memory/pids benar-benar enforce | WAJIB PASS sebelum QA aktif |
| S3 — Safe QA MVP | Backend + QA | Test profile allowlisted, patch preflight, 1 job/15min, resource guard, sandbox ephemeral, exit code & cleanup, progress events | Integrasi pada repo dummy; test limit dan emergency stop | Tidak menyentuh aplikasi production |
| S4 — UI Infrastructure | UI taste + frontend | Server health / guard SAFE-WAIT-STOP, QA queue/phase/log, reasons, stop QA, issue/branch integration | 360/768/1280px screenshots, keyboard/contrast/error/loading/offline/stale | Tidak tampilkan status palsu |
| S5 — PR gates | Backend + QA | Set QA evidence per SHA; PR pass hanya SHA cocok; checks & reviews GitHub; merge memerlukan approval manual | HEAD berubah → QA invalid → merge blocked | Tidak ada bypass branch protection |
| S6 — Deploy safety (terpisah) | Ops/backend + QA | Allowlist deploy, backup plan, healthchecks, rollback, approval terpisah dari QA | Dry-run pada service nonproduction, failure recovery | Default manual; production setelah review |

**Urutan mutlak:** S0→S1→S2→S3→S4→S5. S6 baru setelah S3–S5 berfungsi; tidak boleh menjadikan auto-deploy prasyarat uji.

## SOP operasional harian

### A. Sebelum patch
1. Pilih repository GitHub → lihat issue dan branch → pastikan kondisi branch terbaru.
2. Buat prompt → salin ke ChatGPT → tempel patch yang diterima.
3. Jembatan lakukan validasi path + SHA + `git apply --check` sebelum commit.
4. Jika gagal, tampilkan popup detail file/baris → buat prompt berbasis file terbaru → tunggu patch revisi; jangan memaksa `git apply` atau reset file produksi.

### B. Sesudah patch sukses
1. Commit dalam `bridge/*` → SHA dicatat, profil QA tersetujui dipilih.
2. `QA_REQUESTED` dikunci dengan idempotency key repo/SHA/profile.
3. Production Guard melakukan preflight. **SAFE:** lanjut; **WAIT:** antre dengan alasan; **STOP:** blokir hingga ada evaluasi.
4. Runner mengambil checkout SHA dan environment test sementara dengan pembatas resource; log dan progres dipancarkan ke Jembatan.
5. PASS: bukti terkait SHA tersimpan; PR dapat dibuat/ditinjau. FAIL: redaksi log, buat prompt perbaikan, revisi menghasilkan SHA baru dan pengujian baru.
6. Worker membersihkan hanya sumber daya yang diberi label `jembatan.qa.job_id`, melaporkan cleanup, mengecek healthcheck host. Jika cleanup gagal, status degraded dan operator diberi notifikasi.

### C. Sebelum merge atau deployment
1. Refresh PR dan SHA dari GitHub, cek review dan required checks.
2. QA PASS untuk SHA yang sama adalah syarat wajib, tidak boleh diambil dari screenshot/paste teks hasil test.
3. Merge manual setelah eligibility ditetapkan, lalu branch cleanup hanya dengan pemeriksaan keamanan terpisah yang sudah ada.
4. Deploy production memakai approval terpisah dan jalur eksekusi terotorisasi; healthcheck serta rollback plan diwajibkan sebelum mengaktifkan deployment otomatis.

## Keputusan sumber daya dan profil testing

Parameter baseline:
- 1 job / maksimum 1 CPU agregat / 1,5 GiB RAM agregat / 128 PIDs / durasi 15 menit / target ruang kerja 5 GiB (perlu quota nyata).
- MemAvailable minimal 3 GiB saat preflight; abort job jika <1,5 GiB berkelanjutan; CPU sebelum QA <50% rata-rata 5 menit; disk ≥15 GiB **dan** ≥20% kosong.
- Monitoring tidak valid, cgroup tidak enforce, service production unhealthy, deployment berlangsung → **WAIT/STOP** sesuai alasan. Batas **angka awal**, wajib dikalibrasi sesudah survey 24 jam.
- Profil MVP: syntax, lint, static check, unit test ringan. Integration test + test DB sendiri baru bila memori cukup. E2E browser besar, stress/load test, benchmark produksi, dan test DB production: **blocked**.

**Pengujian perlindungan wajib:** demonstrasikan bahwa sebuah job yang sengaja mencoba melewati limit tidak membuat host production OOM, tidak mendapat akses ke `/var/run/docker.sock`, tidak membaca `.env` produksi, dan tidak mencapai database production. Jangan menjalankan uji destruktif pada production; gunakan service tiruan dan limit kecil untuk failure tests, serta lakukan uji agresif di mesin lain jika diperlukan.

## Preliminary API contract — masih proposal, belum endpoint nyata

Semua endpoint baru memerlukan autentikasi dan tidak menerima perintah shell bebas. Respons JSON diberi `request_id` serta status code yang konsisten.

| Method | Rute rencana | Respons / kewenangan |
| --- | --- | --- |
| GET | `/api/infrastructure/host` | Read-only: CPU/RAM/disk, sampled_at, health, guard SAFE/WAIT/STOP + reasons |
| GET | `/api/infrastructure/qa-runner` | Read-only: unavailable/idle/busy/disabled, effective cgroup, active job |
| GET | `/api/qa/profiles?repo=...` | Profil uji allowlisted dan batas tiap profil |
| POST | `/api/qa/jobs` | `repo, commit_sha, profile_id`; idempotent create; 202 queued, 409 stale SHA, 422 invalid profile |
| GET | `/api/qa/jobs/{job_id}` | Status, stage, log ref, resource snapshots, exit, SHA evidence |
| GET | `/api/qa/jobs/{job_id}/events` | SSE, event ID monotonic, reconnect & replay aman; redacted logs |
| POST | `/api/qa/jobs/{job_id}/cancel` | Cancel **hanya QA job**; idempotent, never production container |
| POST | `/api/qa/emergency-stop` | Blocks new jobs + stops QA; returns affected QA IDs, never production |
| GET | `/api/qa/audit?repo=...` | Audit workflow/history, pagination + filter; redacted |

Bentuk error awal: `{ "error": { "code": "GUARD_WAIT", "message": "RAM tersedia di bawah ambang", "retryable": true }, "request_id": "..." }`. Rancangan ini harus disepakati sebelum frontend dibangun. Jangan memperkenalkan route ini ke README sebagai fitur yang sudah aktif.

## Acceptance criteria yang dapat diuji

| ID | Kriteria penerimaan / expected behavior |
| --- | --- |
| SR-001 | Monitoring S0 mencatat pemakaian CPU/RAM/disk 24 jam tanpa mengubah daemon/container production |
| SR-002 | Docker rootless QA tidak memiliki akses ke socket rootful, GitHub PAT, login UI, atau secrets produksi (negative tests) |
| SR-003 | CPU/memory/PIDs aggregate QA **terbukti** dibatasi dengan cgroup v2 dan systemd; jika tidak, QA unavailable |
| SR-004 | Satu job aktif maksimum; request duplikat repo/SHA/profile tidak membuat job ganda |
| SR-005 | Preflight WAIT pada MemAvailable <3 GiB atau CPU 5 menit ≥50%; saat START resource snapshot dicatat |
| SR-006 | Guard STOP pada production unhealthy, host OOM/critical, disk kritis atau privilege violation; tidak menghentikan production |
| SR-007 | Setiap job punya timeout 15 menit, hard-stop seluruh child process QA dan cleanup resources label scoped |
| SR-008 | Repo profile hanya boleh memakai approved commands; input ChatGPT tidak menjadi shell bebas |
| SR-009 | Tidak ada akses jaringan QA ke production DB, host Docker API, metadata, internal services atau host secrets |
| SR-010 | QA PASS memiliki SHA exact + profile version + exit 0 + log hash; perubahan HEAD membatalkan kelayakan merge |
| SR-011 | QA FAIL menampilkan log ter-redact dan tombol buat prompt perbaikan (human copy/paste) |
| SR-012 | Live stage/log update dengan reconnect, kondisi stale/offline, restart worker dan tidak ada PASS palsu |
| SR-013 | Stop QA dan Emergency Stop hanya mengenai QA sandbox, tidak menyentuh production container/volumes |
| SR-014 | PR/merge server-side check gating required checks + approval, default tanpa auto-merge |
| SR-015 | Deploy production memerlukan approval berbeda, allowlist, healthchecks dan rollback plan; tidak dijalankan oleh QA worker |
| SR-016 | UI 360px/768px/1280px, keyboard focus, readable status labels, error/empty/loading/offline state diuji browser nyata |
| SR-017 | Disk quota benar-benar berlaku atau resource disk status dianggap unavailable; cleanup tidak pernah `docker system prune` host |
| SR-018 | Riwayat pekerjaan dan approval tersimpan setelah restart; semua mutasi memiliki event audit dan idempotency key |

## Risiko dan mitigasi yang belum bisa diklaim selesai

| Risiko | Pencegahan wajib |
| --- | --- |
| 8 GB tidak cukup untuk production + QA | Survey pemakaian 24 jam, guard fail closed, profil ringan |
| Rootless masih berbagi kernel | Minimum privilege dan isolasi network; pekerjaan untrusted tingkat tinggi pindah server |
| Test saat ini berjalan dekat socket production | Larang QA otomatis hingga S2; pisahkan runner dan deployment |
| Service tanpa healthcheck | SAFE tidak boleh diasumsikan; buat probe read-only dulu |
| Batas CPU/RAM diabaikan oleh rootless | Validasi cgroup driver/controller dengan workload dummy |
| BuildKit atau image download memakai disk di luar job | Quota agregat untuk daemon/build cache dan monitoring filesystem |
| Proses QA meninggalkan resources | Namespace/label job, cleanup scoped, cleanup evidence & alert |
| Log mencetak password | Redaction, size limit, akses auth, retention policy |
| PR tidak sesuai SHA yang diuji | Periksa SHA lagi tepat sebelum merge dan wajib retest jika berubah |
| Deployment/rollback menyentuh production | Executor terpisah dan manual approval hingga bukti lengkap |

## Read-only survey checklist (perintah CONTOH, tidak dieksekusi oleh Jembatan saat penulisan)

```bash
uname -a
free -h
nproc
df -h
docker info --format '{{json .ServerVersion}} {{json .CgroupDriver}} {{json .CgroupVersion}}'
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'
cat /sys/fs/cgroup/cgroup.controllers
```

Perintah ini hanya menghasilkan snapshot. Mereka **tidak cukup** untuk membuktikan baseline selama 24 jam. Jangan menjalankan installer rootless, `systemctl stop docker`, `docker prune`, atau mengubah host firewall tanpa review langkah-per-langkah di fase S2.

## Handoff

- **@team-lead:** scope lock keputusan dan bukti survey S0.
- **@backend-dev:** rancangan kontrak dan persistence S1; jangan menjalankan TEST_CMD pada rootful host.
- **@qa-engineer:** susun matrix tes negatif di S2/S3 dan bukti isolation guard.
- **@ui-taste / @frontend-web:** Infrastructure + QA Jobs UI setelah event/API contract dari backend final.
- **Ops/operator:** approval terpisah untuk semua perubahan host dan production; tidak otomatis dari PR docs.

**Definition of Done seluruh inisiatif:** SR-001 hingga SR-018 memiliki hasil yang bisa diaudit, negatif dan positive checks PASS, screenshot browser aktual, serta pengesahan operator. Dokumen desain bukan tanda fitur sudah aktif.
