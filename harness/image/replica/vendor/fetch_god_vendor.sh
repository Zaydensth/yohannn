#!/usr/bin/env bash
# vendor/fetch_god_vendor.sh - fetch the upstream G.O.D files that DRY runs and the CPU tests execute (vendor/god/VENDOR.md)
# from gradients-ai/G.O.D at the commit you pinned, into vendor/god/. Prints sha256 + git blob id of each file.
#
#   GOD_SHA=<40-hex> bash vendor/fetch_god_vendor.sh            download via raw.githubusercontent.com
#   GOD_SHA=<40-hex> GOD_CHECKOUT=/path/to/G.O.D bash ...       copy from a local clone instead (git show <sha>:<path>)
#
# Network: raw.githubusercontent.com only. Nothing is uploaded. Exit 0 = all three files present and non-empty.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DEST="$HERE/god"
GOD_SHA="${GOD_SHA:-}"
GOD_CHECKOUT="${GOD_CHECKOUT:-}"
[[ "$GOD_SHA" =~ ^[0-9a-f]{40}$ ]] || { echo "GOD_SHA must be the full 40-hex G.O.D commit you reviewed" >&2; exit 2; }
FILES="trainer/containers/uploader.py:trainer/containers/uploader.py
validator/evaluation/image_artifacts.py:validator/evaluation/image_artifacts.py
ops/docker/hf-uploader.dockerfile:hf-uploader.dockerfile"
sha256() { if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi; }
blob() { git hash-object "$1" 2>/dev/null || echo "-"; }
rc=0
while IFS=: read -r up here; do
  [ -n "$up" ] || continue
  dst="$DEST/$here"; mkdir -p "$(dirname "$dst")"; tmp="$dst.part"
  if [ -n "$GOD_CHECKOUT" ]; then
    git -C "$GOD_CHECKOUT" show "$GOD_SHA:$up" > "$tmp" || { echo "FAIL $up: not in $GOD_CHECKOUT at $GOD_SHA" >&2; rm -f "$tmp"; rc=1; continue; }
  else
    code="$(curl -sS -m 60 -o "$tmp" -w '%{http_code}' "https://raw.githubusercontent.com/gradients-ai/G.O.D/$GOD_SHA/$up" || true)"
    [ "$code" = "200" ] || { echo "FAIL $up: HTTP ${code:-none}" >&2; rm -f "$tmp"; rc=1; continue; }
  fi
  [ -s "$tmp" ] || { echo "FAIL $up: empty" >&2; rm -f "$tmp"; rc=1; continue; }
  mv -f "$tmp" "$dst"
  printf '%s  sha256=%s  blob=%s  (G.O.D %s)\n' "$here" "$(sha256 "$dst")" "$(blob "$dst")" "${GOD_SHA:0:12}"
done <<< "$FILES"
[ "$rc" = 0 ] && echo "vendor/god ready - compare the hashes with VENDOR.md; READ uploader.py, then record its sha256 in reviewed_uploader.sha256"
exit "$rc"
