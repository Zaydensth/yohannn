# Onboarding agent — baca ini lebih dulu

Berkas ini untuk **agent Claude Code mana pun** yang bekerja di salah satu track SN56 pemilik
vault ini (text · image · environment). Tujuannya: tahu apa yang sudah ada, apa yang dipakai
bersama, dan apa yang khusus track sendiri — sebelum menulis kode apa pun.

## 1. Peta aset

| Aset | Lokasi | Berlaku untuk |
|---|---|---|
| Memory lintas-proyek | `<VAULT>/global/` | **semua track** |
| Memory per-track | `<VAULT>/projects/<track>/` | satu track |
| Ledger strategist, prereg, ledger gerbang | `<VAULT>/projects/<track>/jobs/` | satu track |
| Backup config global | `<VAULT>/home-claude/` | semua track |
| Armada agen | `~/.claude/agents/` | semua track |
| Manual operasi global | `~/.claude/CLAUDE.md` | semua track |
| Skill domain | `~/.claude/skills/sn56-*/` | per track |
| Manual + playbook proyek | `<repo>/CLAUDE.md`, `<repo>/.claude/` | satu repo |

Vault: repo **privat** milik pemilik → clone ke `<VAULT>` (default `~/claude-vault`), lalu
`bash install.sh <track> <project_dir>` (idempoten; memory lama dipindahkan, tidak dihapus).

## 2. Aturan pemisahan — hafalkan ini

> **General kalau kebenarannya tidak berubah saat kamu pindah track. Spesifik kalau ia
> mengutip kode, konstanta, atau artefak satu repo.**

- **General:** siapa user, identitas git, kredensial yang belum diketahui (sebagai pointer),
  aturan gaya kerja, kontrak upstream `gradients-ai/G.O.D`, mekanika turnamen bersama (struktur
  ronde, task API, log validator, forensik artefak, dedup/distinctness, bobot emisi), metode
  kerja (OODA, offline-first, verify-by-data, protokol memory, pola multi-agen), definisi
  armada agen.
- **Spesifik:** konstanta eval per track, tangga smoke→train→eval, daftar jalur forfeit, hasil
  A/B, post-mortem, catatan branch, `CLAUDE.md` repo yang mengutip `file:line`.

Kalau ragu: **tulis di per-track dulu.** Menaikkan fakta ke `global/` setelah terbukti berlaku
di dua track itu murah; menurunkan fakta yang salah-general itu mahal, karena sudah terlanjur
dipercaya track lain.

## 3. Aturan besi (jangan pernah dilanggar)

1. **Jangan submit on-chain, jangan pindahkan TAO.** Siapkan sampai siap tekan; user yang menekan.
2. **Jangan power-off / reboot VPS.** Kalau tidak terjangkau: laporkan.
3. **Jangan push** kecuali perbaikannya tervalidasi DAN user menyetujui.
4. **LICENSE / NOTICE byte-identical** dengan upstream. Tanpa watermark atau penanda identitas.
5. **Rahasia tidak pernah masuk repo submission.** Validator meng-clone repo itu dan riwayat git
   permanen — anggap semua yang di-commit di sana bisa dibaca orang lain.
6. **Anggap stdout container bisa dibaca pihak lain** (stack log validator). Jangan cetak field
   strategi; tetap sisakan satu baris log khas per mekanisme.
7. **Jangan klaim selesai tanpa angka.**
8. **Jangan karang.** Label VERIFIED atau INFERRED pada setiap klaim.

## 3b. Tiga kebiasaan yang membuat vault ini berguna

1. **Vault harus ada dan privat sebelum sesi pertama.** Kalau `~/.claude/projects/<slug>/memory` belum
   symlink ke vault ini, atau `git remote -v` kosong / repo-nya publik: berhenti, arahkan user membuat
   repo GitHub **privat** (`gh repo create <you>/<vault> --private`), lalu `install.sh`. Itu pekerjaan
   nomor satu — tanpa remote privat tidak ada yang bertahan.
2. **Simpan saat faktanya muncul, lalu pastikan ter-push.** Hook `Stop`/`PostToolUse` menjalankan
   `sync.sh`; kalau ragu, jalankan `bash sync.sh` dan cek `git log origin/main -1`. Gaya penulisan
   (satu berkas satu fakta, frontmatter, baris indeks, VERIFIED/INFERRED, Why/How to apply, arsip
   indeks): `STYLE.md`.
3. **Serah-terima saat ganti akun Claude atau saat limit akun ≥ 96 %:** tulis
   `projects/<track>/jobs/HANDOFF_<yyyymmdd>_<slug>.md` dari `templates/jobs/HANDOFF.md` (state nyata,
   perintah lanjut, aturan, daftar memory terbaru, prompt copas `/lanjut-<track>-<yyyymmdd>`), indeks di §8,
   push, amankan pod GPU, berhenti. Akun baru mulai dari: indeks → memory terbaru → handoff.

## 4. Checklist sesi pertama

0. Kalau ada `jobs/HANDOFF_*.md` yang lebih baru dari sesi terakhirmu: baca itu dulu, lalu memory yang
   berubah 48 jam terakhir (`git log --since='2 days ago' --name-only`).
1. Baca `global/MEMORY.md` (kalau sudah ada), lalu `projects/<track>/MEMORY.md`.
2. Baca `<repo>/CLAUDE.md` kalau ada; kalau belum ada, itu pekerjaan pertama.
3. **Tegakkan ground truth sendiri** — baca `gradients-ai/G.O.D` branch `main` untuk track
   kamu: bagaimana task dibuat, dilatih, dievaluasi, di-skor, di-dedup. Kutip `file:line` dan
   SHA-nya. Dokumen mengajarkan mekanika; **kode yang menang** kalau berbeda, dan beri tahu user.
4. Bandingkan apa yang repo emit dengan apa yang eval parse. Ketidakcocokan = forfeit senyap.
5. Urutkan pekerjaan menurut apa yang dilindungi: (1) jalur forfeit / skor nol → (2) mengubah
   wall clock jadi optimizer step → (3) memilih checkpoint yang benar → (4) kejujuran metrik →
   (5) baru hyper-parameter. Tim yang mulai dari (5) tetap macet.
6. Catat temuan non-obvious ke memory di kotak yang benar. Satu berkas = satu fakta.

## 5. Yang belum diketahui — tanya, jangan karang

Hotkey/coldkey Bittensor, host/IP VPS, token push GitHub, **token write Hugging Face** (`~/.hf_env`,
`HF_TOKEN=…` + `HF_USERNAME=…`, 0600 — minta user membuatnya saat pertama kali bila belum ada; verifikasi
dengan `whoami` ter-autentikasi tanpa mencetak), kunci API penyedia GPU (berkas 0600), host Grafana +
datasource UID Loki (kalau ada akses), nama project W&B. Begitu didapat, simpan di `global/` §0 / §4 —
untuk yang rahasia, simpan *di mana* nilainya disimpan (keychain, password manager, path berkas),
**bukan** nilainya.

## 6. Skill domain per track

| Track | Skill | Isi |
|---|---|---|
| semua | `sn56-core` | mekanika lintas-track, dedup, emisi, sumber intel, metode kerja |
| text | `sn56-text-mining` | tipe task, kontrak training/eval, forensik artefak |
| image | `sn56-image-mining` | arsitektur, anggaran wall-clock, kontrak artefak + eval |
| environment | `sn56-env-mining` | PvP + rank quantile, jalur forfeit, boss round |

Semua dipasang di `~/.claude/skills/` dari paket starter.
