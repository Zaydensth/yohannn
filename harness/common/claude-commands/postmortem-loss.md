---
description: Ubah SATU kekalahan/regresi jadi post-mortem terukur, test yang mengunci, lalu fix minimal
argument-hint: [task-id atau gejala]
---

Bedah **satu** kekalahan/regresi sampai jadi test yang mengunci + fix minimal: $ARGUMENTS

Satu defek per commit. Defek kedua yang ditemukan di tengah jalan: **catat dan tinggalkan**, jangan digabung.
Urutannya wajib: angka -> rantai kausal -> checklist taksonomi -> **test** -> fix -> draf commit. Kalau langkah 1 tidak
menghasilkan angka, **berhenti di langkah 1** dan katakan apa yang kurang.

## 1. Gejala TERUKUR dulu — jangan menulis kode sebelum ada angka

Sasarannya satu kalimat kerugian berangka, mis. "checkpoint yang dikirim X% lebih buruk dari bobot awal pada held-out
validator" atau "batas langkah diprediksi A, terukur B". Ambil sinyal yang relevan (lihat `/logdig`): rasio train/eval,
effective batch, atrisi data, loss dev, baris guard, bukti apa yang benar-benar dikirim. Board resmi task ada di task
API (`hotkey_details`), data held-out-nya di arsip privatmu (URL presigned hanya hidup 7 hari).

**Gerbang langkah 1:** tanpa satu kalimat kerugian berangka, jangan lanjut. Sinyal hilang (log hilang, run belum
selesai) -> katakan sinyal mana yang kosong, dan berhenti. Menebak akar dari nama fungsi bukan diagnosis.

## 2. Telusuri ke SITUS KEPUTUSAN, bukan situs crash

Tempat run mati hampir tak pernah tempat keputusannya dibuat. Hasil langkah ini: **rantai berpanah `->`, tiap tautan
punya angka atau `file:line`**; tautan yang tidak bisa ditunjuk ditandai dugaan. Alat:

```bash
git log --all --oneline -S'<simbol yang dicurigai>' -- <dir kode>
git log --all --oneline -- <file> | head -20
git show <sha>
```

Tiga pertanyaan untuk tiap kandidat akar: **Urutan** (apakah ia jalan sebelum prasyaratnya ada?) · **Cakupan** (berapa
jalur yang benar-benar membacanya? perbaikan di 3 dari 4 jalur bukan perbaikan) · **Satuan** (baris, paket, token, atau
langkah optimizer?).

## 3. Checklist taksonomi kegagalan senyap — jawab YA/TIDAK dengan alasan, jangan skip

| # | Kelas | Pertanyaan |
|---|---|---|
| K1 | Guard men-set sinyal yang tidak dibaca jalur yang jalan | Apakah keputusan ini punya dua sinyal, dan hanya satu yang di-set? |
| K2 | Probe / hitungan jalan sebelum prasyaratnya ter-load | Apa yang belum ter-import / ter-patch saat baris ini dieksekusi? |
| K3 | Metrik buta pada satu bentuk data tapi tetap mengembalikan angka | Apa yang terjadi pada bentuk record lain (chat, 3-field, pre-tokenised)? |
| K4 | Dev split runtuh; banyak mekanisme buta serentak tanpa satu baris log | Berapa baris dev SETELAH tokenisasi, bukan sebelum? |
| K5 | Ketidakcocokan satuan | Ambangnya benar, tapi dalam satuan apa? |
| K6 | Fallback / penanda mati sesenyap run yang diselamatkannya | Kalau penyelamatnya gagal, apakah ada yang tahu? |
| K7 | Fitur INERT: ditulis, tidak pernah dipanggil, atau ditelan try/except | Siapa pemanggil produksinya? Buktikan dengan grep. |
| K8 | Komentar / log yang berbohong | Apakah komentar di sekitarnya masih benar setelah fix ini? |

Penanda yang tidak terbaca = tidak ada penanda (cek mode senyap). Guard yang bisa **buta** harus melapor buta, bukan
memvonis.

## 4. Test DULU, baru fix

Buat `test_<nama_defek>.py` **sebelum** menyentuh kode produksi, dan tunjukkan bahwa ia **GAGAL** pada kode sekarang
(test yang lahir sesudah fix tidak pernah membuktikan ia menangkap apa pun). Wajib:

1. Docstring = post-mortem: judul + tanggal, KENAPA INI ADA, kerugian berangka, rantai kausal berpanah, bukti terukur
   (alat ukurnya disebut), apa yang dijaga berkas ini.
2. Konstanta dan default env dibaca **dari sumber** lewat regex; tidak ketemu -> `AssertionError` yang bilang kode
   berubah bentuk (gagal berisik, bukan basi diam-diam).
3. Aritmetika produksi direplikasi dengan penunjuk `file:line`, atau fungsi produksinya diimpor langsung.
4. Bug lama direproduksi sebagai assert (test menolak basi dari dua arah).
5. Tabel kasus, termasuk kasus yang TIDAK boleh berubah.
6. Assert rentang untuk nilai hasil pengukuran, bukan angka kebetulan.
7. CPU saja, tanpa jaringan. Prasyarat hilang = `SKIP:` + exit 77 — **tidak pernah `return 0` diam-diam**.

Kalau defeknya menyentuh skor validator, unit test saja tidak cukup: jalankan juga harness pembanding (replika eval
yang sudah dikalibrasi ke board resmi).

## 5. Fix minimal

Satu defek per commit; perbaiki di situs keputusan, lalu grep **semua** pemanggil. Perilaku baru yang mengubah
aritmetika lewat `/lever` dengan default eksplisit. Komentar/log di sekitarnya diperbarui di commit yang sama (K8). Test
sekarang harus LULUS; jalankan juga test tetangga.

Kalau fix ini **mencabut** klaim lama: satu variabel, config identik selain itu; metriknya held-out nyata; sebut A/B
yang membantah **dan** cacat alat yang mendasari hipotesis lama; nyatakan apa yang dipertahankan; **hapus** kode yang
terbantah (jangan default-OFF); jalankan dalam urutan produksi.

Kosakata bukti — jangan dinaikkan kelasnya: `PLAUSIBLE-not-confirmed` -> `PENDING GPU validation` -> `GPU-validated`.

## 6. Draf commit message (jangan commit/push kecuali diminta)

```
fix(<scope>): <pernyataan DEFEK atau GEJALA - bukan nama fungsi>

<kenapa, dengan angka: sinyal terukur, before -> after>
<apa yang membantah/mendukung; alat yang dipakai>
<default tuas baru kalau ada>
<test yang mengunci: test_<nama>.py>
```

Jangan sebut nama, hotkey, atau task-id peserta lain di kode atau komentar.

## 7. Laporkan

Kalimat kerugian berangka + sumbernya; rantai kausal (tandai dugaan); hasil K1-K8; path test + output GAGAL sebelum
dan LULUS sesudah fix; diff minimal + daftar pemanggil yang di-grep; draf commit; apa yang belum divalidasi dan defek
lain yang sengaja tidak digabung.
