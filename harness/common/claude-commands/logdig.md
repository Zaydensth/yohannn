---
description: Bedah log training atau run live — baca sinyal diagnostik dengan urutan tetap, lalu laporkan temuan
argument-hint: [path log penuh, atau kosong untuk run yang sedang jalan]
---

Bedah satu run training dan laporkan **apa yang sebenarnya terjadi**, untuk: $ARGUMENTS

Aturannya: **bukti dulu, baru kesimpulan.** Setiap klaim punya baris log atau keluaran tool di transkrip ini. Sinyal
yang tidak ada bukan "sehat" — ia **TIDAK ADA DATA**, dan ditulis begitu.

## 0. Gerbang nol — apakah log ini bisa dibaca sama sekali?

Sebelum menyimpulkan apa pun dari log yang tipis: apakah trainer punya mode senyap (stdout dibungkam, logging
dimatikan, hanya stderr yang selamat)? Cek jumlah baris (`grep -c . "$LOG"`) dan penanda mode log yang dipakai trainer.
Kalau log tipis dan mode senyap aktif, **katakan bahwa sebagian besar §2 tidak bisa dijawab** — jangan melaporkan
"tidak ada masalah" dari log yang dibungkam. Pastikan juga: env sesi Claude Code bukan env container trainer.

## 1. Cabang input

- **Path log** -> mode forensik: baca log mentahnya. Kalau ada skrip ekstraksi ringkasan, curigai dulu: daftar
  penandanya bisa basi (penanda yang emitter-nya sudah tidak ada, penanda hidup yang tidak dicari, filter yang justru
  membuang baris penting). Untuk sinyal yang hilang, grep langsung ke log mentah.
- **Kosong** -> run live: pakai alat read-only yang ada (state, tail log). Periksa dulu apakah alat itu bisa buta
  (state global yang ditimpa run lain, fallback in-memory yang mengembalikan `{}` tanpa error, path di luar root yang
  ditolak) — list kosong terlihat identik dengan "run belum mulai".
- **Bukan keduanya** -> tanya user. Jangan menebak berkas mana yang dimaksud.

## 2. Baca sinyal — urutan ini TETAP

Untuk setiap sinyal tulis satu kalimat: apa yang ia **singkirkan** dan apa yang ia **kuatkan**. Sinyal yang tidak
ditemukan = `TIDAK ADA DATA`. Ambang "sehat"/"rusak" **dikalibrasi dari run sehat milikmu sendiri** — jangan pakai
angka orang lain.

1. **Rasio train loss : eval loss** (detektor hafalan). `eval_loss` tidak ada sama sekali adalah temuan tersendiri.
2. **Effective batch**: di HF Trainer, `train_samples_per_second / train_steps_per_second` = sampel per langkah
   optimizer. Dua angka yang sama persis = effective batch 1 (LR yang dikalibrasi untuk batch lain jadi salah).
3. **Atrisi data**: baris dimuat -> difilter -> setelah tokenisasi -> split train/dev. Penurunan drastis adalah AKAR;
   §2.1 dan §2.2 hanyalah gejalanya. Dev split yang tinggal segelintir baris membutakan semua mekanisme yang
   bergantung padanya.
4. **Pencarian LR** (kalau ada): apakah kandidatnya informatif, dan apakah yang terbaik benar-benar yang dipakai.
5. **Checkpoint terbaik**: kapan ia berpindah, dan apakah ada penolakan nilai non-finite yang membuat checkpoint lama
   yang terkirim.
6. **Rantai seleksi & pengiriman**: apa yang benar-benar ditulis ke `/app/checkpoints/<task>/<repo>/` (hanya itu yang
   diunggah uploader validator) dan guard mana yang jalan / diam.
7. **Deadline & anggaran waktu**: keputusan "selesai sekarang", sisa waktu, dan apakah run kena timeout (validator
   membunuh lalu tetap mengunggah apa adanya).

## 3. Laporan — bentuk tetap

```
SUMBER   : <path log | live>  (mode senyap? ya/tidak/tak diketahui)
TASK     : <task_id kalau ketahuan, kalau tidak: TIDAK DIKETAHUI>
SINYAL
  train/eval   : <a / b = rasio>      -> <sehat | hafalan | TIDAK ADA DATA>
  eff-batch    : <samples/s, steps/s> -> <normal | runtuh ke 1 | TIDAK ADA DATA>
  atrisi data  : <awal -> akhir>      -> <normal | runtuh | TIDAK ADA DATA>
  LR search    : <ringkas>            -> <informatif | datar | TIDAK ADA DATA>
  best ckpt    : <step, eval_loss>    -> <berpindah | ditolak | TIDAK ADA DATA>
  pengiriman   : <apa yang dikirim>   -> <lengkap | terputus | TIDAK ADA DATA>
  deadline     : <sisa / verdict>     -> <cukup | mepet | timeout | TIDAK ADA DATA>
TEMUAN   : <maks 3 butir, masing-masing dengan baris log asli atau file:line>
TAK TERBACA: <sinyal yang hilang + KENAPA: mode senyap / penanda basi / alat buta>
```

Butir TEMUAN tanpa sitasi **dihapus**, bukan dilunakkan. `TAK TERBACA` tidak boleh kosong kecuali semua sinyal benar-
benar didapat. Nol temuan = "tidak ada defek yang terbukti dari sinyal yang terbaca", bukan "run sehat".

**Handoff:** defek nyata (regresi, guard buta, sinyal kontradiktif) -> berhenti dan sarankan `/postmortem-loss` dengan
gejala terukur yang sudah dikumpulkan. Jangan menambal kode dari perintah ini.

## 4. Yang TIDAK boleh

Mengarang sinyal atau angka; menyamakan sunyi dengan sehat; menyunting kode, `LICENSE.md` / `NOTICE` dari perintah ini;
commit atau push tanpa diminta; menulis hotkey / task-id peserta lain ke kode atau komentar.
