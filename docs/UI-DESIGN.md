# Jembatan UI — Design Brief & Review

## Arahan

Surface: panel web developer operations lokal. Pengguna: operator tunggal atau tim kecil yang memelihara banyak repository, sering membuka issue/branch dan melakukan handoff manual ke ChatGPT. Tidak ada aset foto atau font luar. Arah terpilih: **D11 Product Instrument** — sidebar graphite, area kerja putih hangat, warna hanya untuk status/aksi, tipografi system sans + monospace untuk nomor issue dan kode. **Bukan** portal marketing atau kumpulan dekorasi.

## Token utama

- `--page: #f5f6f4`, `--surface: #ffffff`, `--rail: #172633`, `--ink: #1b2a32`, `--muted: #62727c`, `--line: #dce3e4`.
- `--accent: #08766d` untuk aksi primer dan status perlu QA; semantic green `--done: #26774e`; violet `--review: #76539b`; neutral pending.
- Border radius 8–12px, spacing 8/12/16/20/32px, font fallback Segoe UI/system sans, monospace untuk log, kode dan nomor issue. Tidak ada gambar eksternal, font eksternal atau gerak dekoratif.

## Fitur UI diterapkan

- Sidebar terpisah untuk akses, pencarian repo, multi-select yang tidak hilang saat filter, sinkronisasi, dan logout.
- Header workspace, empty-state tiga langkah, metrics empat status berdasarkan data issue aktual.
- Pencarian judul/nomor issue, filter status, issue disclosure, prompt/apply/QA/PR lama tetap tersedia.
- Label status menjelaskan **heuristik**, tidak mengklaim “siap merge” hanya karena ada commit.
- Informasi deploy disertai konfirmasi; status QA ditampilkan jika `TEST_CMD` berjalan.
- Token login berpindah dari `localStorage` ke `sessionStorage`; backend dapat memakai `ADMIN_TOKEN` terpisah.
- Responsive breakpoint 1100/820/540px, focus-visible dan reduced-motion.

## Review dan keterbatasan verifikasi

- Pemeriksaan JS statis: disarankan `node --check` pada script yang diekstrak, atau jalankan check di browser. File belum difoto dari browser live.
- **Belum diverifikasi secara visual:** screenshot 360px/768px/1280px, kontras aktual, pembesaran 200%, interaksi dengan API nyata.
- **Belum diterapkan backend:** event persisten, QA wajib, auto-merge, rollback dan realtime logs.
- **Risiko yang tersisa:** tes pada versi awal dapat dieksekusi di lingkungan yang memegang rahasia dan docker socket; jangan jalankan repo tidak tepercaya atau mengaktifkan merge otomatis sebelum sandbox P0.

## Checklist pengujian manual

1. Login admin dan GitHub token terpisah; logout lalu muat ulang: sesi hilang.
2. Seleksi beberapa repo, pakai pencarian, pastikan seleksi tetap ada, lalu sinkron.
3. Buka repo dengan issue kosong, banyak issue, branch tertinggal, serta issue tertutup; pastikan label tidak mengklaim QA PASS.
4. Filter status dan cari issue; buka detail, salin prompt, tempel patch tes, periksa log dan push PR.
5. Periksa keyboard Tab/Enter/focus dan viewport 360/768/1280; jangan menganggap CSS lint sebagai bukti screenshot PASS.
