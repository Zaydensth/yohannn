---
description: Tarik record publik satu turnamen lewat agent tournament-intel, lalu simpan temuan VERIFIED ke memory
argument-hint: [tournament id, mis. tourn_<hash>_<yyyymmdd>]
---

Tarik intel turnamen: $ARGUMENTS

Apa yang ditarik, dari mana, dan format keluarannya ada di docstring
`${SN56_HARNESS:-./harness}/common/intel/pull_tournament.py` — jangan diulang di sini.

## 1. Jalankan

`Agent(subagent_type='tournament-intel', prompt='<tournament id>')`. Agent membaca hotkey milikmu dari env
`SN56_OUR_HOTKEY` (jangan sodorkan nilainya di prompt ini, dan jangan tulis ke repo), menjalankan `pull_tournament.py`
(`--hf --data`; `--loki` hanya kalau `SN56_GRAFANA_HOST` / `SN56_LOKI_DS` ada), dan menyusun tabel rank/gap per task.
Untuk turnamen image, arsipkan data held-out-nya juga (`image/archive/hf_archive_tournament.py fetch` lalu `upload`).

## 2. Setelah kembali — SIMPAN, jangan hanya cetak

Tampilkan tabel apa adanya. Draf memory yang dikembalikan agent **belum tersimpan** (agent itu read-only). Simpan
sendiri sekarang ke direktori memory proyekmu: satu berkas satu fakta, frontmatter lengkap, dan satu baris indeks di
`MEMORY.md`. Jangan laporkan "tersimpan" sebelum kedua langkah itu benar-benar dijalankan.

## 3. Kaveat wajib

`test_data` / `training_data` adalah URL presigned yang kedaluwarsa 7 hari sejak task dibuat — kalau lewat, katakan
eksplisit (TIDAK TERSEDIA), jangan diam-diam dilewati. Jangan tulis hotkey / repo / task-id peserta lain ke repo miner
mana pun — hanya ke memory privatmu.

## Selesai bila

Tabel tercetak, berkas memory baru benar-benar tertulis DAN `MEMORY.md` bertambah satu baris — atau agent melaporkan
data presigned sudah kedaluwarsa, ditandai TIDAK TERSEDIA, bukan dikosongkan diam-diam.
