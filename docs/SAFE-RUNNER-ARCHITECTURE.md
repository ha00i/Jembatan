# Jembatan Safe Runner — Arsitektur Production Lokal (DESAIN, bukan implementasi)

**Versi keputusan:** 12 November 2026  
**Status:** Proposed / belum diaktifkan; persetujuan pemasangan host dan QA harus dilakukan terpisah.  
**Sumber kebenaran:** pembahasan pengguna tentang server production lokal milik sendiri, 8 GB RAM, 4 core CPU, Linux + Docker, tanpa VM, dengan aplikasi production yang tetap harus berjalan.  
**Tujuan:** menjalankan QA berbasis kode GitHub/patch ChatGPT tanpa API model berbayar, dengan risiko seminimal mungkin terhadap production.

## 1. Keputusan produk dan batasan

1. ChatGPT adalah **manual handoff**: pengguna menyalin prompt ke ChatGPT dan menempel hasilnya ke Jembatan. Tidak ada API model berbayar atau akses otomatis ke isi percakapan.
2. Jembatan menjadi **control plane**: GitHub, issue, branch, patch, PR, progres, notifikasi, log, bukti QA dan aturan rilis. Ia **bukan** shell bebas pada mesin production.
3. Target awal satu server production lokal **8 GB RAM / 4 core / tanpa VM**. Tidak diasumsikan tersedia RAM bebas 3 GB; harus diukur sebelum QA diizinkan.
4. Jangan mengubah, me-restart, me-rebuild atau menghapus container/volume production untuk memasang pengujian.
5. Pelaksanaan QA hanya di **akun non-root terpisah dengan Docker Engine rootless terpisah**, workload dengan resource dan akses yang dibatasi; satu job maksimal. Ini pemisahan hak akses, **bukan sandbox sekuat VM**, karena kernel dan hardware tetap sama.
6. **Tidak boleh membuka otomatisasi QA** jika batas cgroup tidak didukung/dapat dibuktikan, akses jaringan production belum diblokir, atau hak akses daemon belum dipisahkan.
7. Merge dan deploy production adalah proses berbeda. Default: persetujuan manusia; tidak ada aksi destructive otomatis.

## 2. Fakta keadaan repository (diamati pada 12 Nov 2026)

Saat dokumen ini dibuat, `bridge.py` sudah bisa melakukan git sync, prompt/patch, menjalankan `TEST_CMD` (opsional), push PR, branch inventory + safe delete dan deploy Docker atas permintaan. UI telah mempunyai popup konflik patch. Namun **belum ada** Safe Runner, antrean persisten, monitoring guard, audit per-job, server API, stream log, atau deployment rollback otomatis.

**Risiko P0:** `docker-compose.yml` saat ini masih me-mount `/var/run/docker.sock` dari host dan memberi Jembatan otoritas production Docker; `TEST_CMD` berjalan dari proses dengan akses tersebut. Konfigurasi ini **bukan mode aman untuk menjalankan patch yang tidak sepenuhnya tepercaya**. Implementasi Safe Runner wajib memisahkan kewenangan sebelum mengizinkan job dari patch ChatGPT. Penambahan menu UI saja tidak menyelesaikan risiko.

## 3. Diagram tanggung jawab

```text
             OPERATOR (satu orang; pengguna lokal)
                            |
              ChatGPT: copy/paste prompt & patch
                            |
                 JEMBATAN CONTROL PLANE
  GitHub sync / issue / patch preview / queue / event log / UI
                   |                    |
            READ-ONLY HOST MONITOR    QA DISPATCHER
            status, metrics, health   allowlisted profiles
                   |                    |
       production daemon observer   isolated Unix user
           (hak terbatas)                |
                                         v
                               ROOTLESS QA DAEMON
                          (bukan /var/run/docker.sock)
                                         |
                        1 job / ephemeral project & filesystem
                          test DB + restricted network + cgroups
                                         |
                                result + log + exit code
                                         |
                         JEMBATAN QA EVIDENCE PER HEAD SHA

        PRODUCTION DOCKER: tidak menjadi target QA, tidak diubah.
        DEPLOYMENT: jalur terpisah, approval + allowlist + guard.
```

Prinsip: **control plane tidak memiliki jalur komando langsung ke production daemon untuk QA**. Monitoring cukup read-only dan, bila membutuhkan jembatan privilege, memakai komponen observer dengan izin sesempit mungkin. Rootless daemon harus menggunakan `DOCKER_HOST`/socket milik user QA sendiri, bukan socket rootful. Bila komponen lama untuk deploy tetap terpasang, jangan pernah menaruh runner atau proses QA pada domain privilege yang sama.

## 4. Resource policy konservatif (angka AWAL, perlu kalibrasi)

| Parameter | Batas awal | Cara menegakkan / catatan |
| --- | --- | --- |
| Concurrent QA jobs | **1** | Queue worker melakukan exclusive reservation |
| CPU agregat semua proses QA | **1 CPU ekuivalen** | Limit cgroup pada QA slice (daemon, build, test, DB), bukan hanya container utama |
| RAM agregat runner + kontainer + builder + database | **1,5 GiB** | Limit cgroup pada user slice / scope QA; OOM protection untuk host, bukan jaminan zero-impact |
| Job wall-time | **15 menit** | Supervisor mengakhiri job & seluruh child process |
| PIDs | **128 agregat**, kalibrasi per profil | Tegakkan melalui cgroup; jika tidak tersedia, **block** |
| Workspace sementara | **target 5 GiB per job** | Hanya bila filesystem/quota runner dapat dibatasi; `docker system prune` global **dilarang** |
| Mulai QA — MemAvailable host | **≥ 3 GiB** selama window preflight | Angka contoh; tidak berarti semua 3 GiB bisa dipakai |
| Berhenti QA — MemAvailable host | **< 1,5 GiB** berkelanjutan | Abort hanya QA; production tetap hidup |
| Mulai QA — CPU host | **< 50% rata-rata 5 menit** | Tunda jika host sibuk |
| Disk host sebelum job | **≥ 15 GiB DAN ≥ 20% kosong** | Ambil kondisi filesystem tempat runner menyimpan image dan workspace |
| Critical memory / OOM host | **STOP + manual review** | Tidak auto-restart job berulang sampai masalah dipahami |
| Profile berat | **Blocked by default** | Chromium E2E penuh, build besar, load/performance test, migration berisiko |

Nilai **1,5 GiB** adalah anggaran agregat seluruh proses QA, bukan untuk setiap kontainer. Batas 1 CPU adalah konsumsi maksimum QA dan **bukan reservasi tiga CPU** untuk production. Untuk host 8 GB, jika beban normal tidak menyisakan MemAvailable 3 GB, baseline QA tidak dijalankan.

Cgroup enforcement adalah gate keras: cek `cgroup v2`, `systemd` cgroup driver, delegasi controller `cpu memory pids`, dan **buktikan** pembatas bekerja pada job uji yang aman. Dalam Docker rootless, limit `--cpus`/`--memory`/`--pids-limit` dapat diabaikan tanpa kondisi ini. Juga verifikasi batas daemon/buildkit di luar container. Jangan menerapkan perubahan systemd/sudo production tanpa review dan rencana rollback.

## 5. Production Guard (server-side, fail-closed)

**SAFE:** seluruh preflight hijau dan monitoring valid; tepat satu job berizin dapat dimulai.  
**WAIT:** resource kurang / deploy sedang aktif / healthy status tidak cukup diketahui; job tetap antre dan tidak dibuat kontainernya.  
**STOP:** OOM, disk kritis, healthcheck production gagal, isolasi hilang, limit tidak dapat ditegakkan, atau operator menekan Emergency Stop; QA dibatalkan atau tidak dimulai, tanpa merusak production.

Preflight wajib:
- Monitoring resource host yang **segar**; jika stale/unavailable status **WAIT**, bukan dianggap aman.
- Baseline RAM, CPU dan disk sesuai tabel; window preflight cukup lama untuk menangkap lonjakan.
- Allowlist semua service production kritis; jika salah satu *unhealthy* → STOP. Jika tidak memiliki healthcheck, status UNKNOWN → WAIT sampai operator mengonfigurasi probe read-only yang kredibel.
- Tidak ada deployment production berlangsung.
- Cgroups, quota, daemon QA dan firewall/network denylist valid (uji isolasi); akun runner **tidak** termasuk docker group production / akses sudo / akses secrets.
- Test profile repository eksplisit; setiap image yang digunakan diverifikasi dan akses registrynya dibatasi.

Selama QA: sampling resource dan health berkala, backpressure, timeout absolut, stop berjenjang (TERM kemudian KILL) ke **hanya job QA**, lalu cleanup job-owned resources. Emergency Stop menghentikan queue dan pekerjaan QA saja; tidak berhak menghentikan service production.

## 6. Keamanan dan batas kepercayaan

- **Credential:** akses GitHub tulis hanya pada control plane; kode QA tidak menerima `GITHUB_TOKEN`, `ADMIN_TOKEN`, SSH key, production `.env` atau Docker socket.
- **Filesystem:** job hanya bind mount direktori staging miliknya; larang root directory host, symlink escape, privileged mount, host PID/IPC/network, host devices dan external production volumes. Cleanup terbatas pada label/job ID sendiri.
- **Network:** default deny egress dan blokir akses ke production host, subnet lokal, Docker bridge/rootful daemon, metadata endpoint, serta service internal. Network Docker terpisah **tidak cukup** sebagai isolasi; butuh enforcement host. Allowlist hanya endpoint yang diperlukan, dengan pengambilan dependency dilakukan sebelum fase eksekusi bila mungkin.
- **Process:** non-root UID, seccomp dan capabilities minimum, read-only root filesystem bila profile memungkinkan, no-new-privileges, pids & memory hard limit.
- **Code provenance:** job hanya menjalankan commit SHA / content digest yang divalidasi. Patch preview + `git apply --check` lebih dahulu; path aman; **jangan eksekusi shell dari teks ChatGPT**. Profiles test adalah konfigurasi admin tetap per repo.
- **Trust model:** code di repositori/patch bisa menyebabkan efek tak diinginkan walaupun kamu satu-satunya operator. Kernel host masih berbagi dengan production; kode benar-benar tidak tepercaya atau test yang membutuhkan privilege harus dialihkan ke server lain, bukan rootless pada production.
- **Logging:** redaksi tokens, passwords, env, file secrets; event append-only dengan ukuran log maksimum, retry policy dan retention. Jangan menampilkan akses credential pada browser/API.

## 7. Alur end-to-end terorganisasi

```text
GITHUB ISSUE / REPO SYNC
   ↓
CHATGPT HANDOFF: generate prompt → operator copy → ChatGPT → paste patch
   ↓
PATCH PREFLIGHT: allowed paths + base SHA + preview + git apply --check
   ├─ PATCH_CONFLICT → toast + current file context → "Salin Prompt Perbaikan"
   └─ PATCH_APPLIED → commit SHA immutable
                         ↓
                  QA_REQUESTED / QUEUED
                         ↓
                  PRODUCTION GUARD
              ├─ WAIT  → queue + alasan
              ├─ STOP  → blocked + operator intervention
              └─ SAFE  → 1 slot/reservation
                         ↓
               PREPARE EPHEMERAL QA
          check out exact SHA / isolated DB / network
                         ↓
                   QA_RUNNING
            progress stages + live log
       ┌─────────────────┼─────────────────┐
       ↓                 ↓                 ↓
   QA_FAILED         QA_ABORTED        QA_PASSED
       ↓                 ↓                 ↓
  prompt followup     reason + log      evidence (SHA,
  to ChatGPT          safe cleanup      profile, timestamp,
                                      result, hash log)
       ↓                                   ↓
  revised patch                         PR / CI gates
       ↓                                   ↓
  NEW SHA → re-QA            PR APPROVED & CHECKS PASS
                                           ↓
                                   MANUAL MERGE GATE
                                           ↓
                                     MERGED TO MAIN
                                           ↓
                                MANUAL PRODUCTION DEPLOY
                                   (separate executor)
                                           ↓
                                  HEALTH CHECK / ROLLBACK
```

**Tidak ada QA PASS lintas SHA.** Bila commit berubah setelah PASS, status wajib kembali `QA_REQUIRED`; merge diblokir sampai pengujian ulang. QA gagal tidak diulang otomatis pada SHA dan profile yang sama tanpa ada perubahan penyebab yang jelas.

### Tabel state dan event audit

| Status | Pemicu masuk | Boleh melakukan |
| --- | --- | --- |
| `PROMPT_READY` | issue + snapshot repo valid | copy prompt |
| `PATCH_CONFLICT` | `git apply --check` gagal | tampilkan error, ambil file terbaru, copy prompt fix |
| `PATCH_APPLIED` | patch valid dan SHA commit tercatat | request QA |
| `QA_QUEUED` | profil tersedia, job idempotent dibuat | cancel / tunggu guard |
| `QA_WAITING_RESOURCE` | guard WAIT | retry preflight, tanpa container |
| `QA_BLOCKED` | guard STOP / isolasi tidak lulus | intervensi admin saja |
| `QA_PREPARING` | reservasi slot & resource | buat sandbox sementara |
| `QA_RUNNING` | runner mengonfirmasi mulai | stream logs, timeout, stop |
| `QA_FAILED` | exit non-zero | log, prompt perbaikan |
| `QA_ABORTED` | guard STOP/timeout/emergency | cleanup, log reason |
| `QA_PASSED` | exit 0 untuk SHA yang sama | PR gate (bukan auto-deploy) |
| `PR_WAITING_CHECKS` | PR terbuka | cek required checks dan reviews |
| `MERGE_ELIGIBLE` | semua checks/approvals, SHA konsisten | approval manual merge |
| `MERGED` | GitHub mengonfirmasi PR merged | manual deployment request |
| `DEPLOY_PENDING_APPROVAL` | permintaan rilis | konfirmasi operator |
| `DEPLOY_VERIFYING` | rilis berlangsung | live log + healthcheck |
| `DEPLOY_FAILED` | healthcheck gagal | rollback berizin/human review |
| `HEALTHY` | service lolos healthcheck | catat release & close workflow |

Event minimal: `job_id`, `repo`, `issue/pr`, `branch`, `commit_sha`, `runner_id`, `test_profile_id/version`, `timestamp`, `phase`, `state`, `reason_code`, `exit_code`, `resource_snapshot`, `log_ref/hash`, `actor`. Persist ke SQLite + event log; restart server tidak boleh mengubah hasil PASS atau mengulangi merge/deploy. Kunci idempotency minimal `repo + commit_sha + test_profile_version + operation`.

## 8. UX Jembatan (rancangan, belum dikodekan)

Sidebar: **Overview · Repositories · Issues · Branches · QA Jobs · Infrastructure · Deployments · Settings**.

`Infrastructure → Local Production`:
- kartu server: RAM 8 GB, 4 core, RAM tersedia aktual, CPU aktual, disk aktual, freshness/last seen;
- daftar container production status **read-only** dan healthcheck yang jelas;
- **Production Guard** SAFE/WAIT/STOP dengan alasan eksplisit, tidak cukup warna saja;
- **QA Runner** status: disabled / unavailable / idle / busy / stopping; profil tersetujui;
- 1-slot queue, tahap 1..N, log streaming dengan redaksi secrets, tombol Cancel/Stop QA;
- panel konfigurasi batas, deployment lock dan audit. Tombol deploy **tidak** berdampingan dengan Stop QA.
- popup kegagalan patch saat ini tetap ada; dikembangkan tombol `Salin Prompt Perbaikan` berdasarkan konflik dan file SHA terbaru.

UI wajib punya keadaan loading/empty/error/offline/stale, timestamp, akses keyboard, 360/768/1280px, no fake live data. Jangan menampilkan "Safe" hanya karena rootless daemon berjalan; seluruh safety gates harus lulus.

## 9. Batas non-goals fase awal

Tidak membuat auto-merge production, auto-deploy, auto-restart service, auto-prune Docker global, stress/load testing pada production, E2E browser berat, akses database production untuk migration/seeding, instalasi VM, atau AI API integration. Fitur tersebut tetap di luar scope sampai isolasi dan kapasitas tervalidasi.

## 10. Referensi teknis

- Docker rootless: https://docs.docker.com/engine/security/rootless/
- Rootless resource controller dan systemd delegation: https://docs.docker.com/engine/security/rootless/tips/
- Rootless troubleshooting/limitations: https://docs.docker.com/engine/security/rootless/troubleshoot/
- Docker memory/CPU/resource constraints: https://docs.docker.com/engine/containers/resource_constraints/

**Penting:** ini dokumen kebijakan dan arsitektur. Tidak ada service atau konfigurasi produksi yang diubah oleh dokumen ini.
