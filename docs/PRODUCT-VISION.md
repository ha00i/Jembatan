# Jembatan — Product Vision & Automation Contract

## Keputusan produk

Jembatan menghubungkan ChatGPT dan GitHub dengan **manual handoff**. Tidak ada panggilan API ke model dari server. ChatGPT menyusun analisis/patch; backend Jembatan bertanggung jawab atas clone/fetch, patch application, QA, PR, merge, deploy, observability, dan audit. Biaya API model tidak muncul dari sistem ini.

Status implementasi per 12 November 2026: versi awal hanya melakukan sinkron, analisis heuristik issue, apply patch, uji opsional, push + PR, dan deploy manual. Rencana di bawah adalah kontrak pengembangan, **bukan klaim fitur yang sudah tersedia**.

## Workflow yang diinginkan

```text
DISCOVERED → PROMPT_READY → AWAITING_CHATGPT
          → PATCH_RECEIVED → PATCH_APPLIED → QA_QUEUED → QA_RUNNING
                                      ├── QA_FAILED → AWAITING_CHATGPT (iterasi)
                                      └── QA_PASSED → PR_OPEN
PR_OPEN → CHECKS_PENDING → CHECKS_PASSED → MERGE_ELIGIBLE → MERGED
MERGED → DEPLOY_QUEUED → DEPLOY_RUNNING → HEALTHY
                                      └── DEPLOY_FAILED → ROLLBACK / HUMAN_REVIEW
```

Tiap transisi menyimpan waktu, actor, repo, issue, branch, HEAD SHA, output/log yang dibatasi ukuran, dan status. Tidak boleh mengandalkan browser tab tetap hidup. Workflow idempotent: duplikasi klik, restart, dan retry tidak boleh membuat PR, merge, atau deploy ganda.

## Automation gates

| Tahap | Syarat wajib | Jika gagal |
| --- | --- | --- |
| Prompt | Issue dan snapshot repo valid; sumber file dibatasi | Minta file relevan |
| Apply | Unified diff valid, path aman, branch `bridge/*`, repo bersih | Tidak commit |
| QA | Per-repo test profile tersedia, runner terisolasi, exit code 0, HEAD SHA sama | FAIL, tampilkan log dan prompt perbaikan |
| PR | QA PASS untuk SHA terakhir, push hanya `bridge/*` | Tahan PR |
| Merge | PR bukan draft, approvals terpenuhi, semua checks required PASS, branch terbaru, tidak conflict, bukan protected bypass | Tahan merge dan tampilkan alasan |
| Deploy | Hanya commit main yang sudah lulus gate, allowlist target, healthcheck, backup/rollback disiapkan | Tahan deploy atau rollback |
| Close issue | GitHub mengonfirmasi merge dan policy penutupan issue | Jangan tutup berdasarkan nama branch |

**Tidak boleh mengaktifkan auto-merge dan auto-deploy sebelum runner QA terisolasi dan pemeriksaan branch protection selesai.** Pengaturan produksi awal: manual approval untuk merge dan deploy; otomatisasi penuh baru boleh opt-in per repository.

## Arsitektur yang disarankan

1. **Repository scanner:** polling terjadwal GitHub dengan ETag/backoff, atau webhook HTTPS jika tersedia. Simpan cache per repository, default branch, PR, issue, SHA, checks.
2. **Workflow engine:** SQLite + append-only audit events; queue worker terbatas; lock per repository, bukan global. Idempotency key `repo/issue/head_sha/action`.
3. **Patch handler:** normalisasi unified diff, path allowlist repo, reject symlink traversal, commit hanya branch bot; tidak menerapkan script bebas sebagai perintah server.
4. **QA runner:** container/VM terisolasi, tanpa token akses dan tanpa `docker.sock`; timeouts, resource limits, test profiles yang ditentukan administrator per repository.
5. **GitHub delivery:** REST API PR/checks/merge, cek ulang HEAD SHA tepat sebelum merge dan hormati branch protection; tidak memakai shell merge langsung ke main dari UI.
6. **Deploy controller:** allowlist direktori dan service, identitas runtime terpisah, healthchecks + log + rollback target, satu deploy aktif per aplikasi.
7. **Read-only status API:** metrics di dashboard berasal dari event dan GitHub, bukan perkiraan tanggal, commit name, atau hasil uji yang disampaikan browser.

## UI information architecture

- **Overview:** repo terpilih, jumlah status valid, aktivitas terakhir, kegagalan yang perlu ditangani.
- **Issues:** searchable list + severity, workflow step, history, actions kontekstual.
- **ChatGPT handoff:** prompt dengan potongan kode sesuai issue dan budget karakter, copy; tempel balasan; preview patch sebelum apply; prompt follow-up saat gagal.
- **QA / PR:** status test per SHA, log, checks, alasan blokir merge, link PR.
- **Deployment:** environment, commit, tahap build, live log, health status, rollback.
- **Settings:** token scopes, runner profiles, branch / merge policy, deploy allowlist. Bahaya ditempatkan terpisah dan butuh konfirmasi.

## Acceptance criteria tahap berikutnya

- Setiap tombol bermakna dan punya loading/error/empty/success state.
- Pergantian repo tidak membocorkan state issue ke repo lain.
- Tidak ada tombol/klaim “QA lulus” tanpa bukti tes server untuk HEAD SHA yang sama.
- QA gagal, tes tidak dikonfigurasi, atau PR check merah memblokir auto-merge (ditegakkan server-side).
- Merge dan deploy berjalan setelah event persisten dan gate terverifikasi, tanpa koneksi browser terbuka.
- Log frontend dan server menampilkan progres per pekerjaan; restart mempertahankan state.
- Mobile 360px, tablet 768px dan desktop 1280px diuji, termasuk keyboard dan aksesibilitas.

## Urutan implementasi

**P0:** secret separation, isolated QA runner, persistent events, server-side gate. **P1:** multi-repo dashboard dan per-issue workflow, safe PR + merge. **P2:** deploy healthchecks, rollback, live progress, audit ekspor. Hindari menggabungkan P0–P2 dalam satu perubahan tanpa QA.
