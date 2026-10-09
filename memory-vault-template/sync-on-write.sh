#!/usr/bin/env bash
# Hook PostToolUse: dipanggil setiap Write/Edit. Membaca payload hook dari stdin,
# dan HANYA men-sync kalau berkas yang disentuh berada di dalam vault ini.
# Tidak pernah keluar non-zero — kegagalan sync tidak boleh menggagalkan tool call.
VAULT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
payload="$(cat)"
# path bisa datang lewat symlink ~/.claude/projects/<slug>/memory -> vault/projects/<track>
case "$payload" in
  *"$VAULT"*|*"/memory/"*) ;;
  *) exit 0 ;;
esac
exec bash "$VAULT/sync.sh"
