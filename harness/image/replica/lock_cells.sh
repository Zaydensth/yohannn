#!/usr/bin/env bash
# lock_cells.sh - bind a cells (queue) file to a LOCKED plan. TEMPLATE of the "no experiment without a locked plan"
# rule: run it on your workstation at LOCK time - after the strategist's verdict line
#   FABLE-PLAN: FABLE <yyyymmdd-hhmm> PLAN=<slug> POINT=lock VERDICT=GO BASIS=...
# is in the plan (above '## RESULTS') AND in the ledger, and the plan's status line reads 'Status: **LOCKED**' - and
# BEFORE stage.sh (which ships the bound cells file, the plan, the ledger and the pinned trainer commit). It writes one
# line on top of the cells file, which queue.sh's gate re-checks before any GPU work:
#   # lock plan_sha256=<sha256 of the plan text above '## RESULTS'> fable=<stamp> slug=<slug> rows_sha256=<rows> [pin_sha=<40 hex>]
# After the bind only the `enabled` column may change (switching a cell OFF is a stop, never a new experiment); any
# other edit - to the plan's frozen text or to a row - refuses at the gate until you lock again (--rebind).
#
#   bash lock_cells.sh --plan PLAN.md --cells cells.tsv --ledger FABLE_LEDGER.md --slug <slug> --fable <stamp> \
#        [--pin-sha <40-hex> --repo <miner repo> --branch <branch>] [--allow-bypass] [--rebind] [--dry-run]
#
# Verified first (any failure: REFUSE, exit 1, nothing written): plan_gate.py lockcheck (the rules in its docstring),
# the cells rows hash, and - with --pin-sha - that the pin is a commit of --repo ON --branch (an ancestor of its tip).
# Then the line is written (atomic) and plan_gate.py gate must PASS on the result, or this script fails.
# Exit: 0 bound (or already bound to the same values), 1 refused, 2 usage.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PLAN=""; CELLS=""; LEDGER=""; SLUG=""; FABLE=""; PIN=""; REPO=""; BRANCH=""; REBIND=0; DRYRUN=0; BYPASS=0
PY="${PY:-python3}"
usage() { sed -n '2,/^# Exit/p' "$0" >&2; exit 2; }
while [ "$#" -gt 0 ]; do
  case "$1" in
    --plan) PLAN="${2:-}"; shift 2 ;;
    --cells) CELLS="${2:-}"; shift 2 ;;
    --ledger) LEDGER="${2:-}"; shift 2 ;;
    --slug) SLUG="${2:-}"; shift 2 ;;
    --fable) FABLE="${2:-}"; shift 2 ;;
    --pin-sha) PIN="${2:-}"; shift 2 ;;
    --repo) REPO="${2:-}"; shift 2 ;;
    --branch) BRANCH="${2:-}"; shift 2 ;;
    --allow-bypass) BYPASS=1; shift ;;
    --rebind) REBIND=1; shift ;;
    --dry-run) DRYRUN=1; shift ;;
    -h|--help) usage ;;
    *) echo "lock_cells.sh: unknown argument '$1'" >&2; usage ;;
  esac
done
say() { printf '[lock_cells %s] %s\n' "$(date -u +%H:%M:%S)" "$*" >&2; }
refuse() { say "REFUSE: $*"; exit 1; }
[ -n "$PLAN" ] && [ -n "$CELLS" ] && [ -n "$LEDGER" ] && [ -n "$SLUG" ] && [ -n "$FABLE" ] || usage
for f in "$PLAN" "$CELLS" "$LEDGER"; do [ -f "$f" ] || refuse "no such file: $f"; done
BP=(); [ "$BYPASS" = "1" ] && BP=(--allow-bypass)

# 1. the plan: LOCKED, the binding line stamped --fable, the ledger line, no later NO-GO/REVISI
LC="$("$PY" -B "$HERE/plan_gate.py" lockcheck --plan "$PLAN" --slug "$SLUG" --fable "$FABLE" --ledger "$LEDGER" ${BP[@]+"${BP[@]}"} 2>&1)" \
  || refuse "the plan is not ready to bind: $(printf '%s' "$LC" | tr '\n' ' ')"
eval "$(printf '%s\n' "$LC" | grep -E '^(PLAN_SHA256|GO_LINE|LOCKCHECK)=')"
[ "${LOCKCHECK:-}" = "PASS" ] && [[ "${PLAN_SHA256:-}" =~ ^[0-9a-f]{64}$ ]] || refuse "lockcheck gave no PASS / plan hash"
say "plan $PLAN: LOCKED, binding line $FABLE (PLAN=$SLUG), ledger line present; frozen text sha256 ${PLAN_SHA256:0:16}.."

# 2. the rows
ROWS="$("$PY" -B "$HERE/plan_gate.py" rowshash --cells "$CELLS" 2>&1)" && [[ "$ROWS" =~ ^[0-9a-f]{64}$ ]] \
  || refuse "cannot hash the rows of $CELLS: $ROWS"
say "cells $CELLS: rows_sha256 ${ROWS:0:16}.. (every row, without the enabled column)"

# 3. the pinned trainer commit (optional)
if [ -n "$PIN" ]; then
  [[ "$PIN" =~ ^[0-9a-f]{40}$ ]] || refuse "--pin-sha '$PIN' is not a full 40-hex SHA (a short SHA is never a pin)"
  [ -n "$REPO" ] && [ -n "$BRANCH" ] || refuse "--pin-sha needs --repo and --branch (the pin must be a commit OF that branch)"
  git -C "$REPO" cat-file -e "${PIN}^{commit}" 2>/dev/null || refuse "$PIN is not a commit of $REPO"
  TIP="$(git -C "$REPO" rev-parse --verify --quiet "refs/heads/$BRANCH^{commit}" || true)"
  [ -n "$TIP" ] || refuse "no branch $BRANCH in $REPO"
  git -C "$REPO" merge-base --is-ancestor "$PIN" "$TIP" 2>/dev/null || refuse "$PIN is not on $BRANCH (not an ancestor of its tip ${TIP:0:12})"
  say "pin_sha $PIN is on $BRANCH (tip ${TIP:0:12})"
fi

# 4. bind, then the gate must pass on what was written
ARGS=(bind --cells "$CELLS" --plan-sha256 "$PLAN_SHA256" --fable "$FABLE" --slug "$SLUG" --rows-sha256 "$ROWS")
[ -n "$PIN" ] && ARGS+=(--pin-sha "$PIN")
[ "$REBIND" = "1" ] && ARGS+=(--rebind)
[ "$DRYRUN" = "1" ] && ARGS+=(--dry-run)
BC="$("$PY" -B "$HERE/plan_gate.py" "${ARGS[@]}" 2>&1)" || refuse "bind: $BC"
say "$(printf '%s' "$BC" | sed -n 's/^BIND=//p' | tr -d "'")"
[ "$DRYRUN" = "1" ] && exit 0
GATE="$("$PY" -B "$HERE/plan_gate.py" gate --plan "$PLAN" --cells "$CELLS" --slug "$SLUG" --ledger "$LEDGER" ${BP[@]+"${BP[@]}"} 2>&1)" \
  || refuse "BUG: the cells file is bound but the gate refuses it: $(printf '%s' "$GATE" | tr '\n' ' ')"
say "$GATE"
say "LOCKED - next: stage.sh (ships this cells file, the plan, the ledger and the pinned commit)"
