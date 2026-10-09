#!/usr/bin/env bash
# Pasang vault ini sebagai direktori memory Claude Code, satu track per proyek.
#
#   bash install.sh <track> <project_dir> [<track> <project_dir> ...]
#
#   <track>        nama folder di projects/ (mis. text | image | environment)
#   <project_dir>  direktori tempat kamu menjalankan `claude` untuk track itu
#
# Per pasangan:
#   ~/.claude/projects/<slug(project_dir)>/memory -> <vault>/projects/<track>
#   <vault>/projects/<track>/MEMORY.md             disalin dari MEMORY.md (kalau belum ada)
#   <vault>/projects/<track>/jobs/FABLE_LEDGER.md  disalin dari templates/jobs/ (kalau belum ada)
#
# Idempoten: aman dijalankan berulang. Memory lama dipindahkan (backup), tidak dihapus;
# MEMORY.md / ledger yang sudah ada tidak pernah ditimpa.
set -euo pipefail

VAULT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STAMP="$(date +%Y%m%d-%H%M%S)"
PROJECTS="$HOME/.claude/projects"

# Claude Code menamai direktori proyeknya dari path absolut direktori kerja; '/' dan '.'
# menjadi '-' (VERIFIED dari slug yang ada). Karakter lain di luar [A-Za-z0-9-] juga diganti
# '-' di sini (INFERRED) — check_variant di bawah memperingatkan kalau Claude Code ternyata
# memakai slug lain.
slug_of() { printf '%s' "$1" | sed 's/[^A-Za-z0-9-]/-/g'; }

seed() {
  local t="$VAULT/projects/$1"
  mkdir -p "$t/jobs"
  if [ ! -f "$t/MEMORY.md" ]; then
    sed "s/<track>/$1/g" "$VAULT/MEMORY.md" > "$t/MEMORY.md"
    echo "  + projects/$1/MEMORY.md (indeks kosong)"
  fi
  if [ ! -f "$t/jobs/FABLE_LEDGER.md" ]; then
    sed "s/<track>/$1/g" "$VAULT/templates/jobs/FABLE_LEDGER.md" > "$t/jobs/FABLE_LEDGER.md"
    echo "  + projects/$1/jobs/FABLE_LEDGER.md (ledger kosong)"
  fi
}

link_one() {
  local track="$1" slug="$2"
  local target="$VAULT/projects/$track"
  local dest="$PROJECTS/$slug/memory"
  mkdir -p "$(dirname "$dest")"
  if [ -L "$dest" ]; then
    if [ "$(readlink "$dest")" = "$target" ]; then
      echo "  ✓ $track sudah tertaut ($slug)"
      return 0
    fi
    rm "$dest"   # hanya tautan lama; datanya tidak tersentuh
  elif [ -e "$dest" ]; then
    mv "$dest" "$dest.backup-$STAMP"
    echo "  ! $slug punya memory lama -> disimpan di memory.backup-$STAMP"
  fi
  ln -s "$target" "$dest"
  echo "  ✓ $track -> $dest"
}

check_variant() {
  local track="$1" slug="$2" d base
  for d in "$PROJECTS"/*/; do
    [ -d "$d" ] || continue
    base="$(basename "$d")"
    [ "$base" = "$slug" ] && continue
    [ "$(slug_of "$base")" = "$slug" ] || continue
    ls "$d"*.jsonl >/dev/null 2>&1 || continue
    echo "  !! Claude Code memakai slug '$base' untuk path ini, bukan '$slug'." >&2
    echo "     Tautkan manual: ln -s \"$VAULT/projects/$track\" \"${d%/}/memory\"" >&2
  done
}

if [ $# -lt 2 ] || [ $(( $# % 2 )) -ne 0 ]; then
  sed -n '2,15p' "${BASH_SOURCE[0]}"
  exit 2
fi

echo "Menautkan memory proyek:"
while [ $# -gt 0 ]; do
  track="$1"; dir="$2"; shift 2
  if ! [[ "$track" =~ ^[a-z0-9][a-z0-9-]*$ ]]; then
    echo "  ! nama track tidak sah (pakai huruf kecil, angka, '-'): $track" >&2; exit 2
  fi
  if [ ! -d "$dir" ]; then
    echo "  ! bukan direktori: $dir" >&2; exit 2
  fi
  seed "$track"
  logical="$(cd "$dir" && pwd -L)"
  physical="$(cd "$dir" && pwd -P)"
  link_one "$track" "$(slug_of "$logical")"
  check_variant "$track" "$(slug_of "$logical")"
  if [ "$physical" != "$logical" ]; then
    link_one "$track" "$(slug_of "$physical")"
    check_variant "$track" "$(slug_of "$physical")"
  fi
  [ -n "$(ls "$PROJECTS/$(slug_of "$logical")"/*.jsonl 2>/dev/null)" ] \
    || echo "  · belum ada sesi Claude Code di $logical — jalankan \`claude\` sekali di sana, lalu jalankan ulang skrip ini untuk cek slug."
done

echo
if [ "$VAULT" != "$HOME/claude-vault" ]; then
  echo "CATATAN: settings.template.json (paket starter) mengasumsikan vault di ~/claude-vault."
  echo "  Ubah kedua path hook ke: $VAULT"
fi
echo "Hook sync: gabungkan claude/settings.template.json (paket starter) ke ~/.claude/settings.json."
echo "Backup config global (dipakai gerbang penerimaan CLAUDE.md §4):"
echo "  cp -a ~/.claude/CLAUDE.md ~/.claude/agents ~/.claude/commands \"$VAULT/home-claude/\""
echo "  (salin settings.json juga hanya kalau isinya bebas rahasia)"
echo "Selesai. Buka $VAULT sebagai vault di Obsidian (opsional)."
