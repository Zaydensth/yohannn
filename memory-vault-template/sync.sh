#!/usr/bin/env bash
# Commit + push perubahan vault. Dipanggil otomatis oleh hook Stop di akhir sesi,
# atau manual kapan saja. Tidak pernah keluar non-zero — kegagalan jaringan tidak
# boleh menggagalkan sesi Claude Code.
# Identitas commit = git config repo vault ini sendiri. Set sekali:
#   git -C <vault> config user.name "<nama>" && git -C <vault> config user.email "<email>"
VAULT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$VAULT" || exit 0
git diff --quiet && git diff --cached --quiet && [ -z "$(git ls-files -o --exclude-standard)" ] && exit 0

n=$(( $(git diff --name-only | wc -l) + $(git ls-files -o --exclude-standard | wc -l) ))
git add -A >/dev/null 2>&1
if git commit -q -m "memory: sync $(date +%Y-%m-%d\ %H:%M) - ${n} file(s)" >/dev/null 2>&1; then
  # Vault berisi temuan riset Anda: push HANYA setelah Anda memastikan origin adalah repo PRIVAT, lalu
  #   touch "$VAULT/.private-remote-ok"   (berkas ini di-.gitignore)
  if [ ! -f "$VAULT/.private-remote-ok" ]; then
    echo "[vault] ${n} file(s) di-commit lokal; push DILEWATI — buat $VAULT/.private-remote-ok setelah origin dipastikan PRIVAT"
  else
  GIT_TERMINAL_PROMPT=0 git push -q origin main >/dev/null 2>&1 \
    && echo "[vault] ${n} file(s) synced" \
    || echo "[vault] commit lokal OK, push gagal (offline?) — jalankan sync.sh lagi nanti"
  fi
else
  # Tanpa ini, commit yang gagal (mis. identitas git belum di-set) lolos diam-diam dan
  # push "berhasil" tanpa mengirim apa pun.
  echo "[vault] commit GAGAL — cek: git -C \"$VAULT\" config user.name / user.email"
fi

# Cek kesehatan graf — hanya berjalan saat ADA perubahan (early-exit di atas sudah
# menyaring sesi tanpa tulisan baru), dan hanya bicara kalau ada yang salah. Dibungkus
# penuh: timeout, stderr dibuang, `|| true` — kontrak "tidak pernah gagal" di atas tetap utuh.
if command -v python3 >/dev/null 2>&1 && [ -f "$VAULT/tools/graph_check.py" ]; then
  ( cd "$VAULT" && python3 tools/graph_check.py --quiet 2>/dev/null ) || true
fi
exit 0
