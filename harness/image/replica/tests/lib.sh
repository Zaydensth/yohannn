# tests/lib.sh - shared helpers for tests/test_*.sh (sourced). Sets KIT, WORK, LOGS, STUBS, SPATH and the check helpers.
KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KEEP="${KEEP:-0}"
if [ -n "${WORK:-}" ]; then mkdir -p "$WORK"; else WORK="$(mktemp -d "${TMPDIR:-/tmp}/kit_test.XXXXXX")"; fi
LOGS="$WORK/logs"; mkdir -p "$LOGS"
export PYTHONDONTWRITEBYTECODE=1
NPASS=0; NFAIL=0; N=0; LAST_LOG=""
ok() { NPASS=$((NPASS + 1)); printf 'PASS %s\n' "$1"; }
bad() { NFAIL=$((NFAIL + 1)); printf 'FAIL %s   [log %s]\n' "$1" "$2"; }
run() {  # run NAME WANT_RC CMD...   (output -> numbered log)
  local name="$1" want="$2" rc; shift 2
  N=$((N + 1)); LAST_LOG="$LOGS/$(printf '%03d' "$N")_$(printf '%s' "$name" | tr -c 'A-Za-z0-9._-' '_' | cut -c1-60).log"
  "$@" > "$LAST_LOG" 2>&1; rc=$?
  if [ "$rc" = "$want" ]; then ok "$name (rc=$rc)"; else bad "$name: rc=$rc, want $want" "$LAST_LOG"; fi
}
has() { if grep -qF -- "$2" "$1" 2>/dev/null; then ok "$3"; else bad "$3: missing '$2'" "$1"; fi; }
hasnt() { if grep -qF -- "$2" "$1" 2>/dev/null; then bad "$3: unexpected '$2'" "$1"; else ok "$3"; fi; }
eq() { if [ "$1" = "$2" ]; then ok "$3 ($1)"; else bad "$3: got '$1', want '$2'" "${LAST_LOG:--}"; fi; }
finish_tests() {
  echo "== summary: PASS=$NPASS FAIL=$NFAIL  (logs: $LOGS)"
  if [ "$KEEP" != "1" ] && [ "$NFAIL" = "0" ]; then rm -rf "$WORK"; else echo "work dir kept: $WORK"; fi
  [ "$NFAIL" = "0" ]
}
# stubs: docker (tests/docker_stub.py), nvidia-smi (idle GPU), flock (tests/flock_stub.py when the OS has none),
# timeout (runs the command, ignores the limit - the kit's own deadlines are tested through the docker stub)
STUBS="$WORK/stubs"; mkdir -p "$STUBS"
printf '#!/bin/sh\nexec python3 %s "$@"\n' "$KIT/tests/docker_stub.py" > "$STUBS/docker"
printf '#!/bin/sh\nexit 0\n' > "$STUBS/nvidia-smi"
command -v flock >/dev/null 2>&1 || printf '#!/bin/sh\nexec python3 %s "$@"\n' "$KIT/tests/flock_stub.py" > "$STUBS/flock"
command -v timeout >/dev/null 2>&1 || cat > "$STUBS/timeout" <<'EOS'
#!/bin/sh
while [ $# -gt 0 ]; do case "$1" in --*) shift ;; *) break ;; esac; done
shift
exec "$@"
EOS
chmod +x "$STUBS"/*
SPATH="$STUBS:$PATH"
export STUB_STATE="$WORK/stub_state"; mkdir -p "$STUB_STATE"
