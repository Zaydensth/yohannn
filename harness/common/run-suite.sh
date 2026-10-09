#!/bin/bash
# run-suite.sh - run a CPU test suite and classify every result HONESTLY.
#
# The trap this avoids: a runner that files a failing test as "skipped" by grepping its output for words like
# ModuleNotFoundError / ImportError / usage: cannot tell a missing OPTIONAL dependency from a real bug - a test whose
# code under test raises a genuine ModuleNotFoundError prints the same word, gets filed as a skip, and a broken test can
# never fail the suite. A guard that cannot fail loudly is not a guard.
#
# The rule: a test is a SKIP only when it SAYS so itself -
#   - it exits 77 (the autotools convention for "skip"), or
#   - it prints a line matching ^SKIP: (stdout or stderr) giving the reason.
# Every other non-zero exit is FAIL, with the last 5 lines of its output shown so the failure is legible without a
# re-run. Tests that legitimately need CLI arguments or a heavy optional dependency declare that at the point they
# discover it (exit 77 / "SKIP: <why>") instead of crashing with a bare traceback.
#
# Buckets: PASS / FAIL / SKIP (declared, with a reason). Only FAIL is a problem; the exit code is non-zero only then.
#
# Usage: run-suite.sh [python] [glob]     python defaults to python3 (pass a venv's python to include torch-gated
#                                         tests); glob defaults to SUITE_GLOB or 'scripts/test_*.py', relative to
#                                         SUITE_ROOT (default: the current directory)
set -u
PY="${1:-python3}"
GLOB="${2:-${SUITE_GLOB:-scripts/test_*.py}}"
cd "${SUITE_ROOT:-.}" || exit 2
export PYTHONDONTWRITEBYTECODE=1

pass=0; fail=0; skip=0
skip_names=(); skip_reasons=(); fail_names=(); fail_tails=()
found=0
for t in $GLOB; do
  [ -f "$t" ] || continue
  found=$((found + 1))
  out=$("$PY" "$t" 2>&1); rc=$?
  name=$(basename "$t" .py)
  if [ $rc -eq 0 ]; then pass=$((pass+1)); continue; fi
  skip_line=$(printf '%s\n' "$out" | grep -m1 -E '^SKIP:')
  if [ $rc -eq 77 ] || [ -n "$skip_line" ]; then
    skip=$((skip+1)); skip_names+=("$name"); skip_reasons+=("${skip_line:-(exit $rc, no SKIP: line printed)}")
  else
    fail=$((fail+1)); fail_names+=("$name"); fail_tails+=("$(printf '%s\n' "$out" | tail -5)")
  fi
done
if [ "$found" -eq 0 ]; then
  echo "no test matched '$GLOB' under $(pwd) - an empty suite is not a passing suite"; exit 2
fi
echo "python:     $("$PY" -c 'import sys;print(sys.version.split()[0])') at $PY"
echo "PASS:       $pass"
echo "FAIL:       $fail"
echo "SKIP:       $skip (declared explicitly: exit 77 or a ^SKIP: line - reasons below)"
if [ ${#fail_names[@]} -gt 0 ]; then
  echo "failed (last 5 lines of output shown):"
  for i in "${!fail_names[@]}"; do echo "  ${fail_names[$i]}"; printf '%s\n' "${fail_tails[$i]}" | sed 's/^/    /'; done
fi
if [ ${#skip_names[@]} -gt 0 ]; then
  echo "skipped:"
  for i in "${!skip_names[@]}"; do printf '  %-32s %s\n' "${skip_names[$i]}" "${skip_reasons[$i]}"; done
fi
exit $([ $fail -eq 0 ] && echo 0 || echo 1)
