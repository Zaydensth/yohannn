---
description: Tambah satu tuas (env lever) baru end-to-end — cek nama, default aman, log provenance, test CPU, dokumentasi
argument-hint: [nama-tuas] [apa yang diubah]
---

Tambahkan satu tuas environment baru, **end to end**, untuk: $ARGUMENTS

Kerjakan berurutan. Jangan menulis kode sebelum langkah 0 dan 1 selesai.

## 0. Jangan tabrakan nama

```bash
grep -rn "<PREFIX>_<NAMA_KANDIDAT>" <direktori kode trainer>/
```

Pakai satu prefix untuk semua tuas proyekmu dan pola `<PREFIX>_<AREA>_<HAL>`. Kalau nama (atau varian yang
membingungkan) sudah ada, pilih yang lain. Daftar tuas yang hidup dan yang sudah mati ada di dokumentasi proyekmu —
perbarui daftar itu di langkah 5.

## 1. Baca pola baca env yang sudah ada sebelum menulis

Cari bagaimana kode ini membaca env hari ini, lalu tiru bentuknya. Aturan:

1. **Nama tuas di konstanta modul-level**, dengan komentar default-nya — bukan string literal yang tersebar.
2. **Boolean pakai parser permisif** (`"1", "true", "yes", "y", "on"`), jangan `== "1"` telanjang: dua dialek
   boolean berdampingan adalah sumber bug diam (`=true` mematikan fitur tanpa suara).
3. **Nilai numerik pakai helper** dengan try/except + guard NaN/non-positif, bukan `float(os.getenv(...))` telanjang.
4. **Satu tuas = satu sumber kebenaran.** Kalau dibaca di lebih dari satu modul, ekspor fungsi gate-nya dan import
   fungsinya; jangan menyalin ekspresi env.
5. **Fail-soft.** Logika baru dibungkus supaya exception apa pun kembali ke perilaku lama.

## 2. Default HARUS eksplisit — dan pahami harga default-OFF

- Tuas yang mengubah aritmetika dan **belum** lolos A/B GPU: boleh lahir default-OFF untuk percobaan, tapi tuas
  default-OFF **tidak pernah jalan di turnamen** (validator tidak menyetel env kustom) dan juri dedup T2 membacanya
  sebagai kode mati: *"Code that only diverges on conditions that never hold at training time is dead: branching on an
  unset env var"* (G.O.D `validator/infrastructure/repo_dedup_config.json`, system_prompt). Jadi tuas default-OFF bukan
  pembeda dan bukan fitur.
- Setelah A/B: ADOPT = flip ke default-ON (perbarui komentar konstantanya di commit yang sama); REFUTE = **hapus
  kodenya**, jangan dibiarkan default-OFF.
- Kalau pilihan default tidak jelas dari $ARGUMENTS: **tanya user**, jangan menebak.

Sebut default-nya secara literal di subject commit: `feat(<scope>): <defek/gejala yang diatasi> (<NAMA>, default-OFF)`.

## 3. Satu baris log provenance saat tuas mengubah keputusan

Kalau tuas aktif tapi tidak mengubah apa pun: jangan cetak apa-apa. Saat nilai berubah, satu baris:
`[scope] NAMA: nilai_lama -> nilai_baru (alasan / parameter)`, `flush=True`. Pastikan baris itu benar-benar terlihat di
log run (cek apakah ada mode "quiet" yang membungkam stdout — kalau ada, tulis ke stderr atau sebutkan cara
menyalakannya di docstring).

## 4. Test CPU yang membaca konstanta DARI SUMBER

Buat `test_<nama_tuas>.py` di commit yang sama:

1. Docstring = post-mortem singkat: kenapa tuas ini ada, dengan angka terukur (atau tulis jujur apa yang belum
   diketahui — jangan mengarang).
2. Nama env **dan** default-nya dibaca dari sumber lewat regex, bukan diketik ulang; kalau tidak ketemu,
   `AssertionError("... kode berubah bentuk, test perlu ditinjau")` — test harus gagal berisik, bukan basi diam-diam.
3. Replikasi aritmetika produksi dengan penunjuk `file:line`, atau impor fungsi produksi aslinya.
4. Kasus fail-soft (`None`, `0`, negatif, NaN) ikut diuji.
5. CPU saja, tanpa jaringan; exit lewat `sys.exit(main())`. Prasyarat hilang = `SKIP:` + exit 77, tidak pernah `return 0`.

Jalankan langsung dan tunjukkan output-nya: `python3 test_<nama_tuas>.py`.

## 5. Dokumentasi

Catat tuas di daftar tuas proyekmu (nama, default, apa yang diubah, status A/B). Daftar yang basi adalah alat diagnosis
yang rusak.

## 6. Yang TIDAK boleh

- Mengklaim validasi GPU yang belum terjadi: tulis "pending GPU A/B".
- Menyentuh `LICENSE.md` / `NOTICE`.
- Menaruh tooling dev di pohon submission.
- Menggabungkan tuas ini dengan perbaikan lain: satu defek per commit.
- Commit atau push tanpa diminta.

## 7. Laporkan

Nama + default + alasan default; `file:line` deklarasi, pembacaan, dan titik keputusan; contoh baris log provenance;
output test; draf commit message; dan A/B apa yang dibutuhkan untuk mem-flip default.
