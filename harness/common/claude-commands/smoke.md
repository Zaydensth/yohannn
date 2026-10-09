---
description: Gerbang pre-submit — kontrak repo lalu smoke test container pada SHA persis, lalu verdict boleh-submit
argument-hint: [commit-hash, default HEAD]
---

Jalankan **gerbang pre-submit lengkap** untuk commit: $ARGUMENTS (kosong = `HEAD`). Resolve dulu ke SHA penuh:
`C=$(git rev-parse "${ARGUMENTS:-HEAD}^{commit}")`, lalu pakai `$C` di semua perintah.

Urutannya sengaja: **kontrak repo dulu** (murah, dan inilah yang membuat entry **dikeluarkan** — bukan sekadar skor
jelek), baru build container yang mahal. Jangan menyimpulkan lulus dari langkah yang tidak dijalankan.

## 0. Prasyarat — cek, jangan asumsikan

```bash
git status --short                                  # boleh kotor: gate membangun dari worktree bersih, tapi catat
docker info > /dev/null 2>&1 && echo "docker OK" || echo "docker TIDAK JALAN"
```

Docker mati -> berhenti, verdict **TIDAK KONKLUSIF**.

## 1. Kontrak repo

```bash
bash ${SN56_HARNESS:-./harness}/common/check-repo-contract.sh "$C"
```

Wajib bersih. Perhatikan khusus: `LICENSE.md` / `NOTICE` harus identik dengan upstream **di default branch**
(validator membandingkannya di sana, G.O.D `validator/tournament/github_validation.py:57-170`). Kalau meleset satu byte:
**JANGAN SUBMIT** — kembalikan dari upstream, jangan disunting supaya cocok. Set `CONTRACT_COPY_ALLOW` dan
`CONTRACT_ALLOWED_TOP` supaya cek COPY-set dan permukaan pohon benar-benar ditegakkan, bukan hanya dicetak.

## 2. Smoke test container

```bash
SMOKE_TRACK=<text|image> SMOKE_IMPORTS="..." SMOKE_HELP_CMD="..." SMOKE_SUITE_CMD="..." \
  bash ${SN56_HARNESS:-./harness}/common/pre-submit-smoke-test.sh "$C"
```

Isi variabelnya dari proyekmu (lihat header skrip). Yang dibuktikan tiap tahap: build segar commit itu dari worktree
bersih (waktu vs batas 30 menit), entrypoint ada, impor di **interpreter yang dipakai entrypoint produksi**, CLI dalam
bentuk pemanggilan produksi, **satu langkah optimizer nyata**, dan suite CPU di dalam image (hasil kosong ≠ bersih).
Exit 3 = ada tahap yang tidak jalan = TIDAK KONKLUSIF.

## 3. Postur offline + kontrak path output

- Training jalan di jaringan Docker `internal=True` tanpa rute keluar (G.O.D `trainer/runtime.py:71-76, 277`):
  `from_pretrained` dengan repo-id **tidak** fail-fast — ia mencoba koneksi lalu menggantung sampai deadline. Grep
  kode trainer untuk unduhan saat training; semua harus membaca `/cache` (lokal).
- Uploader validator **hanya** melihat `/app/checkpoints/<task_id>/<expected_repo_name>/`
  (`trainer/training_paths.py:13-14`, `trainer/containers/uploader.py:174-184`): pastikan trainer menulis ke sana, dan
  sebut lapisan penyelamat apa yang ada saat trainer gagal / kehabisan waktu (timeout -> kill -> upload apa adanya,
  `runtime.py:1086-1092`).

## 4. Verdict

```
VERDICT: AMAN DISUBMIT | JANGAN SUBMIT | TIDAK KONKLUSIF
SHA    : <40 karakter penuh>
```

- **AMAN DISUBMIT** — hanya kalau §1 bersih, skrip §2 exit 0, dan §3 tidak menemukan unduhan saat training.
- **JANGAN SUBMIT** — satu saja gagal; sebut yang mana, dengan output aslinya.
- **TIDAK KONKLUSIF** — ada langkah yang tidak bisa dijalankan. Ini bukan lulus.

## 5. Yang TIDAK boleh

- Mengarang lulus: exit 0 dari satu skrip bukan bukti kalau tahapnya tidak benar-benar jalan.
- Menyimpulkan dari nama file atau ingatan — setiap klaim punya output perintah di transkrip ini.
- Menyunting `LICENSE.md` / `NOTICE`.
- "Memperbaiki" skrip gate sambil jalan tanpa diminta (usulkan sebagai commit terpisah).
- Commit, push, atau memanggil endpoint miner kecuali user memintanya.
