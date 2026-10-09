---
description: Evaluasi gerbang G0-G4 pada satu SHA lewat agent gate-auditor, lalu tampilkan baris ledger-nya
argument-hint: [sha atau branch, default HEAD]
---

Evaluasi gerbang untuk: $ARGUMENTS (kosong = `HEAD`).

Definisi gerbang, bukti yang wajib, dan nama ledger ada di runbook sesi ini (template:
`${SN56_HARNESS:-./harness}/common/templates/RUNBOOK-ab-template.md` §1) dan di agent `gate-auditor` — baca dari sana,
jangan disalin ulang di sini.

## 0. Resolve dulu, jangan percaya string mentah

```bash
SHA=$(git rev-parse "${ARGUMENTS:-HEAD}^{commit}") || { echo "ref tidak dikenal — berhenti"; exit 1; }
```

`git rev-parse` menerima nama branch, hash pendek, atau `HEAD` dan selalu mengembalikan SHA penuh 40 karakter.
Validator menolak commit yang bukan 40-hex (G.O.D `validator/tournament/tournament_manager.py:1091-1096`), jadi
kebiasaan ini mulai di sini. Pakai `$SHA` untuk semua langkah di bawah, tidak pernah `$ARGUMENTS` mentah.

## 1. Jalankan

`Agent(subagent_type='gate-auditor', prompt='SHA=<$SHA>')`. Agent membaca bukti nyata per gerbang dan menulis satu
baris `G<n> PASS/FAIL/UNKNOWN <bukti>` per gerbang ke ledger. Gerbang yang butuh box (mis. smoke container) dan tidak
bisa dijalankan di mesin ini = `UNKNOWN`, bukan lolos diam-diam.

## 2. Setelah kembali

Tampilkan baris `G<n>` apa adanya — jangan diringkas jadi "lolos"/"gagal" tanpa baris aslinya. SHA ini **kandidat
submit** hanya kalau SEMUA gerbang PASS pada SHA persis ini; satu `FAIL` atau `UNKNOWN` berarti **belum** — sebut
gerbang mana yang menahan, jangan simpulkan "hampir lolos".

## Selesai bila

Semua baris `G<n>` tercetak dengan bukti dan kesimpulan kandidat/bukan-kandidat tertulis, atau agent melaporkan bukti
yang tidak bisa didapat. Di kedua kasus jangan lanjut ke keputusan submit di sini: push dan registrasi adalah keputusan
user.
