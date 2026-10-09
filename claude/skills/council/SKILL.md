---
name: council
description: Jalankan council lima penasihat untuk satu keputusan sulit — lima sudut pandang independen di model yang berbeda menjawab pertanyaan yang sama, saling me-review secara anonim, lalu seorang chairman menyintesis satu verdict dengan langkah konkret dan daftar apa yang tidak bisa diputuskan. Pakai saat keputusan mahal kalau salah dan sudah berhari-hari bolak-balik — merge branch atau tidak, entry mana yang dikirim, delta ini cukup khas atau tidak, go/no-go sebelum push atau submit. JANGAN pakai kalau jawabannya sudah tahu dan cuma cari validasi.
---

# Council — lima penasihat, review anonim, satu verdict

## Masalah yang diselesaikan

Model cenderung setuju denganmu. Tanya "haruskah saya merge branch ini?" dan ia menemukan
lima alasan ya; tanya "apakah merge ini ide buruk?" dan ia menemukan lima alasan tidak.
Objek yang sama, framing berbeda, jawaban berlawanan. Untuk menulis email itu tidak masalah.
Untuk keputusan turnamen yang mahal, itu berbahaya.

Council mematahkannya dengan memaksa lima sudut pandang yang **tidak bisa semuanya sejalan
denganmu**, lalu membuat mereka saling memeriksa **tanpa tahu siapa menulis apa**.

Asalnya: [LLM Council](https://github.com/karpathy/llm-council) oleh Andrej Karpathy (poll
banyak model → ranking anonim → chairman), dan adaptasi lima-persona oleh Ole Lehmann.
Versi ini menggabungkan keduanya — **diversitas model DAN diversitas sudut pandang** — lalu
menambahkan disiplin bukti yang jadi aturan besi di workflow ini.

## Kapan dipakai, kapan tidak

**Pakai** kalau biaya salahnya tinggi dan kamu sudah berputar lebih dari beberapa hari:
- merge branch fitur ke main sekarang atau tunggu?
- entry A atau entry B yang dikirim ke ronde ini?
- delta ini cukup khas untuk lolos gerbang T2, atau kita sedang menipu diri sendiri?
- go/no-go sebelum push, sebelum submit, sebelum menyalakan GPU semalaman
- arsitektur mana yang layak dapat sisa anggaran siklus ini

**Jangan pakai** untuk: pertanyaan faktual (baca kodenya), keputusan yang sudah jelas, atau
saat kamu sebenarnya hanya ingin divalidasi. Council akan mengatakan hal yang tidak ingin
kamu dengar — itu justru gunanya.

## Aturan yang membedakan ini dari sekadar bertanya lima kali

**Setiap penasihat wajib mengutip.** Klaim tanpa `file:line`, output perintah, angka
terukur, atau URL ditandai INFERRED oleh penasihat itu sendiri — dan reviewer diminta
menghukumnya. Tanpa aturan ini, council hanya menghasilkan lima opini percaya diri yang
saling menguatkan. Ini menegakkan aturan VERIFIED/INFERRED di `~/.claude/CLAUDE.md`, bukan
mengendurkannya.

**Review-nya anonim dan diacak.** Penasihat dipetakan ke huruf secara acak sebelum review,
jadi reviewer tidak bisa memihak peran favoritnya.

**Pertanyaan ketiga adalah yang paling berharga.** "Apa yang dilewatkan kelimanya?" —
celah antar-jawaban hampir selalu memuat sesuatu yang tidak terpikir oleh siapa pun sendirian.

## Lima penasihat

| Peran | Tugasnya | Kelas kesalahan yang ditangkap |
|---|---|---|
| **Contrarian** | Mengasumsikan ada cacat fatal dan memburunya. Kalau semua tampak kokoh, gali lebih dalam. | "Kedengarannya bagus, tapi sudah kamu pikirkan…" |
| **First-principles** | Abaikan pertanyaannya; apa sebenarnya yang sedang dipecahkan? Bongkar asumsinya. | Mengoptimasi variabel yang salah sejak awal |
| **Forfeit-hunter** | Hanya peduli satu hal: bagaimana ini menghasilkan skor NOL? Exit code, wall clock, artefak, parser, kontrak eval. | Kalah bukan karena kualitas, tapi karena jalur mati |
| **Dedup adversary** | Berperan sebagai juri T2 tanpa konteks: apakah delta ini benar-benar milik kita, dan apakah ia menyala di runtime? | Kekhasan yang hanya ada di kepala kita |
| **Executor** | Senin pagi kerjakan apa, di hardware apa, diukur bagaimana, selesai kapan? | Rencana brilian tanpa langkah pertama |

Tiga peran pertama umum (dari sumber aslinya); dua berikutnya diganti agar memetakan cara
miner SN56 benar-benar kalah — jalur forfeit dan gerbang kekhasan. Ganti kalau domainnya beda.

## Cara menjalankan

Pakai tool **Workflow**. Skrip lengkap ada di `council-workflow.js` di folder skill ini —
salin, isi `QUESTION` dan `CONTEXT`, jalankan.

Tiga hal yang membuatnya bekerja dan jangan diubah tanpa alasan:

1. **Model dibedakan per penasihat.** `fable` untuk yang butuh penalaran terdalam,
   `opus` untuk yang butuh ketelitian teknis, `sonnet` untuk yang butuh sudut segar.
   Menjalankan kelimanya di satu model membuat kesalahannya berkorelasi — itu menghapus
   sebagian besar nilai council.
2. **Fase review adalah barrier.** Reviewer harus melihat **semua** jawaban sekaligus, jadi
   di sinilah `parallel()` memang benar dipakai, bukan `pipeline()`.
3. **Chairman jalan di `fable` dengan effort tinggi.** Ini satu-satunya bagian yang benar-benar
   menuntut penalaran kelas atas.

## Bentuk output yang wajib

Chairman mengembalikan, dalam urutan ini:

1. **VERDICT** — satu kalimat, keputusan sebenarnya, tanpa pagar-pagaran
2. **Keyakinan** — tinggi / sedang / rendah, beserta alasannya
3. **Alasan utama** — dua atau tiga, masing-masing berjejak ke bukti
4. **Apa yang akan membalikkan verdict ini** — konkret dan bisa diuji
5. **Langkah Senin pagi** — satu tindakan, bisa dikerjakan hari itu juga
6. **Yang tidak bisa diputuskan council** — eksplisit, jangan disembunyikan

Poin 4 dan 6 tidak boleh dilewati. Verdict tanpa kondisi pembatalan adalah tebakan yang
berpakaian rapi.

## Setelah selesai

Kalau verdict-nya mengubah arah kerja, simpan sebagai memory `project` di kotak yang sesuai —
sertakan **Why** dan **How to apply**, dan catat apa yang akan membalikkannya. Keputusan yang
dilupakan alasannya akan diperdebatkan lagi tiga minggu kemudian.
