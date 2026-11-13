# Jembatan × Superpowers — status implementasi

Dokumen ini mencatat implementasi bertahap dari spesifikasi **Jembatan ×
Superpowers spesifikasi integrasi untuk ChatGPT** (13 November 2026).
Alur Superpowers **opt-in per issue**. Alur lama `POST /api/prompt` tetap aktif.

## Iterasi 1 — Fondasi backend (diimplementasikan)

- `workflow.py`: SQLite hanya stdlib, disimpan di
  `${JEMBATAN_DATA:-/srv/jembatan}/workflow.db`. Docker Compose mengeset
  `JEMBATAN_DATA=/srv/jembatan/workflow-state` dan memasang bind mount
  `/srv/jembatan/workflow-state:/srv/jembatan/workflow-state`.
- Tabel `workflow_runs`, `workflow_events` (append-only dengan trigger
  UPDATE/DELETE yang menolak), dan `workflow_artifacts`.
- Kunci unik **run aktif per (repo, issue)**, lock per issue pada proses,
  serta transaksi SQLite `BEGIN IMMEDIATE` untuk konsistensi antarproses.
- `POST /api/workflow/start` memvalidasi issue GitHub (PR ditolak), default
  branch GitHub, dan SHA tip. Pilihan:
  `mode=penuh` dengan `execution=per_tugas` / `sekaligus`, atau
  `mode=cepat` dengan `execution=sekaligus`.
- `GET /api/workflow/state` mengembalikan run, fase, status, alasan gate,
  iterasi, metadata artefak, dan peristiwa audit.
- Klik start ulang pada run aktif dengan mode yang sama memakai run dan
  event yang sama. Mode yang bertentangan ditolak dengan HTTP 409.
- Peristiwa audit saat ini hanya menerima metadata server yang dibolehkan,
  bukan balasan ChatGPT, log mentah, atau token. Batas payload 20 KB.
- Kedua API menggunakan autentikasi `X-Admin-Token` yang sudah ada;
  tidak ada token, model API, atau layanan pihak ketiga yang ditambah.

### Contoh kontrak API

```http
POST /api/workflow/start
X-Admin-Token: <token login Jembatan yang sudah ada>
Content-Type: application/json

{"repo":"ha00i/Vortex-ERP","issue":12,
 "mode":"penuh","execution":"per_tugas"}
```

```http
GET /api/workflow/state?repo=ha00i%2FVortex-ERP&issue=12
X-Admin-Token: <token login Jembatan yang sudah ada>
```

Respons sukses mengandung `run.id`, `run.base_branch`,
`run.base_sha`, `run.phase`, `run.status`, `gate`, `iterations`,
`artifacts`, dan `events`. `start` juga mengandung `reused`.
Error mengikuti kontrak Jembatan `{"error": "pesan"}`.

### Gate yang sengaja belum aktif

Iterasi 1 **belum** membuat worktree, menjalankan baseline, menghasilkan
prompt per fase, menerima balasan, menerapkan patch, menjalankan RED/GREEN,
mereview kode, membuat PR, merge, atau deploy. Setiap run baru memunculkan
`gate.blocked=true`, `gate.can_advance=false`, `qa_verified=false`,
`merge_allowed=false`, `deploy_allowed=false`. Jangan menampilkan run
sebagai QA PASS atau pekerjaan selesai.

### Pengujian dan operasional

```bash
python3 -W error::ResourceWarning -m unittest discover -s tests -p "test_workflow_state.py" -v
python3 -m unittest discover -s tests -v
python3 -m py_compile workflow.py bridge.py
```

Tes baru mencakup persistensi setelah restart, idempotensi saat retry, race
antar-thread/store SQLite, isolasi repository, metadata audit, dan input
berbahaya. Untuk deployment baru, siapkan direktori state pada host
sebelum Compose bila perlu. **Jangan menghapus direktori
`/srv/jembatan/workflow-state` saat membangun ulang container.**

## Tahap berikutnya (belum dikerjakan)

1. **Iterasi 2:** parser rencana/review; generator prompt fase; rute
   `/api/workflow/prompt` dan `/api/workflow/submit` untuk brainstorming,
   rencana, dan review.
2. **Iterasi 3:** UI stepper dan gate yang opt-in per issue.
3. **Iterasi 4:** worktree terisolasi, setup dan baseline test.
4. **Iterasi 5:** patch per tugas dan gate TDD RED/GREEN; integrasi QA.
5. **Iterasi 6:** penyelesaian PR/merge manual, artefak audit dan diagnosa.

Tidak ada auto-merge, auto-deploy, API model berbayar, ataupun perubahan pada
mechanisme Docker QA/SSH production. Gate yang belum diimplementasikan
harus tetap tertutup hingga ada tes dan bukti server yang sah.
