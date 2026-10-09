#!/usr/bin/env bash
# pre-submit-smoke-test.sh - TEMPLATE pre-submit gate: build and exercise the EXACT commit you will submit, the way the
# validator will, before you submit it. Run it inside your miner repo:
#
#   bash pre-submit-smoke-test.sh [commit-ish]          (default HEAD; always resolved to the full 40-hex SHA)
#
# Stages (every enabled stage must PASS; a stage that could not run makes the verdict INCONCLUSIVE, never PASS):
#   0 contract   check-repo-contract.sh on the commit (cheap, and the failure that excludes the entry outright)
#   1 build      a FRESH build of that commit from a clean git worktree (your working tree is never touched): production
#                builds with docker-py `client.api.build(..., nocache=True)` (G.O.D trainer/runtime.py:93-146) - the
#                daemon's classic builder is INFERRED, so this uses DOCKER_BUILDKIT=0 --no-cache - and kills the build
#                at 30 min (trainer/constants.py:41). Wall time is measured; the FULL log is kept; SMOKE_BUILD_MARKERS
#                (strings your dockerfile prints) prove the dependency layers really re-ran.
#   2 entrypoint the image's ENTRYPOINT/CMD, and the file it names, exist
#   3 imports    SMOKE_IMPORTS imported by SMOKE_PYTHON - the interpreter your ENTRYPOINT really uses (testing another
#                venv is how a gate passes while production crashes)
#   4 cli        SMOKE_HELP_CMD (e.g. "python -m my_trainer --help") in the production invocation form
#   5 one step   ONE real optimizer step inside the image: SMOKE_PROBE_CMD, or for the text track the toy-model probe
#                (../text/train_step_probe.py, mounted read-only - it stays out of the submission tree)
#   6 suite      SMOKE_SUITE_CMD inside the image (e.g. "bash -c 'cd /workspace/scripts && for t in test_*.py; do
#                python $t >/dev/null 2>&1 || echo FAIL $t; done; echo TOTAL $(ls test_*.py | wc -l)'") - prints
#                "FAIL <name>" lines and one "TOTAL <n>" line; tests failing BY NAME in SMOKE_SUITE_ALLOW are tolerated
#                (with a reason in your notes), anything else fails the gate; TOTAL < SMOKE_SUITE_MIN = the container
#                did not really run the suite - an EMPTY result is not a CLEAN one
# env: SMOKE_DOCKERFILE [ops/docker/standalone-text-trainer.dockerfile, else dockerfiles/...] SMOKE_TAG [smoke:<sha8>]
#      SMOKE_TRACK [text|image] SMOKE_BUILD_MARKERS [] SMOKE_PYTHON [python] SMOKE_IMPORTS [] SMOKE_HELP_CMD []
#      SMOKE_PROBE_CMD [] SMOKE_SUITE_CMD [] SMOKE_SUITE_ALLOW [] SMOKE_SUITE_MIN [1] SMOKE_KEEP_IMAGE [1]
# Exit: 0 SAFE TO SUBMIT | 1 DO NOT SUBMIT | 3 INCONCLUSIVE (a stage did not run). Pushing / registering stay YOUR call.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REF="${1:-HEAD}"
SHA="$(git rev-parse --verify "$REF^{commit}")" || { echo "REFUSE: '$REF' is not a commit here"; exit 1; }
TRACK="${SMOKE_TRACK:-text}"
TAG="${SMOKE_TAG:-smoke:${SHA:0:8}}"
PY="${SMOKE_PYTHON:-python}"
SUITE_MIN="${SMOKE_SUITE_MIN:-1}"
LOG_DIR="$(mktemp -d "${TMPDIR:-/tmp}/presubmit.XXXXXX")"
INCOMPLETE=""
say() { printf '%s\n' "$*"; }
stop() { say "  FAIL: $* - DO NOT SUBMIT ($SHA)"; exit 1; }
skip() { say "  NOT RUN: $1 - $2"; INCOMPLETE="$INCOMPLETE $1"; }
say "===== pre-submit gate: $SHA (logs: $LOG_DIR) ====="

say "[0/6] repo contract"
bash "$HERE/check-repo-contract.sh" "$SHA" || stop "repo contract violated"

say "[1/6] fresh build of exactly $SHA (classic builder, no cache)"
WT="$LOG_DIR/worktree"
git worktree add -q --detach "$WT" "$SHA"
trap 'git worktree remove --force "$WT" >/dev/null 2>&1 || true' EXIT
DF="${SMOKE_DOCKERFILE:-}"
if [ -z "$DF" ]; then
  if [ "$TRACK" = "image" ]; then CANDS="ops/docker/standalone-image-toolkit-trainer.dockerfile dockerfiles/standalone-image-toolkit-trainer.dockerfile ops/docker/standalone-image-trainer.dockerfile dockerfiles/standalone-image-trainer.dockerfile"
  else CANDS="ops/docker/standalone-text-trainer.dockerfile dockerfiles/standalone-text-trainer.dockerfile"; fi
  for c in $CANDS; do [ -f "$WT/$c" ] && { DF="$c"; break; }; done
fi
[ -n "$DF" ] && [ -f "$WT/$DF" ] || stop "no dockerfile at the paths the validator looks for (trainer/constants.py:1-12)"
docker image rm -f "$TAG" >/dev/null 2>&1 || true            # a stale tag must never stand in for a failed build
T0=$(date +%s)
set +e
( cd "$WT" && DOCKER_BUILDKIT=0 docker build --no-cache -f "$DF" -t "$TAG" . ) > "$LOG_DIR/build.log" 2>&1
RC=$?
set -e
SEC=$(( $(date +%s) - T0 ))
tail -n 15 "$LOG_DIR/build.log"
say "  build rc=$RC wall=${SEC}s (production limit 1800 s)"
[ "$RC" -eq 0 ] || stop "docker build failed (full log $LOG_DIR/build.log)"
[ "$SEC" -lt 1800 ] || stop "the build took ${SEC}s >= 1800 s: in production there would be no container and no score"
[ "$SEC" -lt 1200 ] || say "  WARNING: over 20 min - thin margin against the 30-min limit"
if [ -n "${SMOKE_BUILD_MARKERS:-}" ]; then
  IFS='|' read -r -a MARKS <<< "$SMOKE_BUILD_MARKERS"
  for m in "${MARKS[@]}"; do grep -qF -- "$m" "$LOG_DIR/build.log" || stop "build marker '$m' absent: a cached layer was reused or the dependency step did not run"; done
  say "  build markers present (${#MARKS[@]})"
else
  skip build-markers "set SMOKE_BUILD_MARKERS='marker one|marker two' from lines your dockerfile prints"
fi

say "[2/6] entrypoint"
EP="$(docker image inspect -f '{{json .Config.Entrypoint}} {{json .Config.Cmd}}' "$TAG")"
say "  ENTRYPOINT/CMD: $EP"
FIRST="$(printf '%s' "$EP" | python3 -c 'import json,sys
s=sys.stdin.read().split(" ",1)
for part in s:
    try: v=json.loads(part)
    except Exception: continue
    if v: print(next((x for x in v if x.startswith("/")), "")); break')"
if [ -n "$FIRST" ]; then docker run --rm --entrypoint test "$TAG" -e "$FIRST" || stop "the entrypoint file $FIRST is not in the image"; say "  $FIRST present"; fi

say "[3/6] imports with the production interpreter ($PY)"
if [ -n "${SMOKE_IMPORTS:-}" ]; then
  docker run --rm --entrypoint "$PY" "$TAG" -c "import importlib, sys
for m in sys.argv[1:]:
    importlib.import_module(m); print('  import', m, 'OK')" ${SMOKE_IMPORTS} || stop "an import failed in the production interpreter"
else
  skip imports "set SMOKE_IMPORTS='torch transformers your_trainer_module ...'"
fi

say "[4/6] CLI in the production invocation form"
if [ -n "${SMOKE_HELP_CMD:-}" ]; then
  docker run --rm --entrypoint sh "$TAG" -c "$SMOKE_HELP_CMD" > "$LOG_DIR/help.log" 2>&1 || { tail -5 "$LOG_DIR/help.log"; stop "SMOKE_HELP_CMD failed"; }
  tail -n 5 "$LOG_DIR/help.log"
else
  skip cli "set SMOKE_HELP_CMD, e.g. 'python -m my_trainer --help'"
fi

say "[5/6] one real optimizer step"
if [ -n "${SMOKE_PROBE_CMD:-}" ]; then
  docker run --rm --network none --entrypoint sh "$TAG" -c "$SMOKE_PROBE_CMD" || stop "the one-step probe failed"
elif [ "$TRACK" = "text" ] && [ -f "$HERE/../text/train_step_probe.py" ]; then
  docker run --rm --network none -v "$(cd "$HERE/../text" && pwd):/probe:ro" --entrypoint "$PY" "$TAG" /probe/train_step_probe.py \
    || stop "not even one training step succeeded"
else
  skip one-step "set SMOKE_PROBE_CMD to a command that runs ONE real optimizer step of your trainer"
fi

say "[6/6] CPU test suite inside the image"
if [ -n "${SMOKE_SUITE_CMD:-}" ]; then
  OUT="$(docker run --rm --network none --entrypoint sh "$TAG" -c "$SMOKE_SUITE_CMD" 2>&1 || true)"
  TOTAL="$(printf '%s\n' "$OUT" | awk '/^TOTAL /{print $2}' | tail -n1)"
  [ -n "$TOTAL" ] && [ "$TOTAL" -ge "$SUITE_MIN" ] 2>/dev/null || stop "the suite reported TOTAL='${TOTAL:-none}' (< $SUITE_MIN): an empty result is not a clean one"
  UNEXPECTED=""
  for f in $(printf '%s\n' "$OUT" | awk '/^FAIL /{print $2}'); do
    case " ${SMOKE_SUITE_ALLOW:-} " in *" $f "*) say "  tolerated by name: $f" ;; *) UNEXPECTED="$UNEXPECTED $f" ;; esac
  done
  [ -z "$UNEXPECTED" ] || stop "tests failing outside SMOKE_SUITE_ALLOW:$UNEXPECTED"
  say "  $TOTAL test file(s) ran; no unexpected failure"
else
  skip suite "set SMOKE_SUITE_CMD"
fi

[ "${SMOKE_KEEP_IMAGE:-1}" = "1" ] || docker image rm -f "$TAG" >/dev/null 2>&1 || true
if [ -n "$INCOMPLETE" ]; then
  say "===== INCONCLUSIVE for $SHA: not run:$INCOMPLETE - a stage that did not run is not a pass ====="
  exit 3
fi
say "===== ALL STAGES PASSED - safe to submit $SHA (image kept for audit: $TAG) ====="
