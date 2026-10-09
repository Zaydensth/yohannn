#!/usr/bin/env bash
# queue.sh [run|plan] - the SEQUENTIAL queue over a cells file (file order = execution order), on the GPU box.
# It never trains anything itself (cell_run.sh makes one attempt per cell) and never prints a verdict - numbers only;
# the pre-registered rules of your locked plan decide.
#
#   bash queue.sh plan                 validate the cells file, show the plan gate's verdict and what each cell would do
#   bash queue.sh                      run (or resume) the queue
#   ONLY_CELLS="a b" bash queue.sh     only these cells (file order kept)
#   FORCE_CELLS="a" bash queue.sh      re-run these although their latest row is terminal - an EXTRA draw, only after a
#                                      RE-SCOPE decision; audited in RERUN_LOG_TSV as action=forced
#   DRY=1 bash queue.sh                every cell DRY (cell_run.sh DRY=1: commands printed, mock HF half, no rows)
#   touch $KIT_ROOT/STOP_queue         ends the queue at the next cell boundary (the running cell finishes); a run
#                                      refuses to start while the file exists - remove it to resume
#   kill -TERM <pid of this script>    stops NOW: TERM is forwarded to the running cell_run.sh (its own trap removes
#                                      the container, token files and any live HF repo), the end gate runs, exit 143
#
# Rules (method, not a decision rule):
#   * a real run REFUSES unless the plan gate passes (plan_gate.py gate: the plan is LOCKED by a strategist GO, the
#     cells file is bound to it, nothing in the plan's frozen text or the rows changed since, the staged checkout is
#     the pinned commit). DRY reports the gate's verdict and goes on.
#   * one queue at a time (QUEUE_LOCK_FILE); the GPU itself is serialised by the GPU flock inside each cell.
#   * per cell: enabled=0 -> skip; latest result row terminal (OK, FAIL_TRAIN(..), PRECONDITION(..)) -> RESUME SKIP
#     (a terminal row is never re-trained unless FORCE_CELLS names it); free disk >= MIN_FREE_GB; then cell_run.sh.
#   * classify by cell_run.sh's exit code: 0 / 3 / 6 terminal; 1 REFUSED, 4 FAIL_HF, 5 FAIL_SCORE = box-level ->
#     ONE OPS-RERUN for that cell (RESCORE=1 of the same checkpoint when training finished, else a fresh attempt);
#     a second consecutive box-level failure (that rerun) -> STOP + ALERT_FILE (the box is sick, not the cell);
#     2 usage -> STOP; 130/143 -> the queue stops (that cell is attempted again next run).
#   * END GATE (hf_flow.py endgate) however the queue ends: with HF_DELETE_AFTER_SCORE=1 no repo created by the queue
#     may still be public (one sweep first) - else exit 1; with 0 every public repo is listed.
#
# env [defaults]: KIT_ROOT [/root/kit] LATENT_ROOT [$KIT_ROOT/latent] CELLS_TSV [$KIT_ROOT/plan/cells.tsv]
#   PLAN [$KIT_ROOT/plan/PLAN.md] LEDGER [$KIT_ROOT/plan/FABLE_LEDGER.md] SLUG [required for a real run]
#   ALLOW_BYPASS [0] (a BYPASS-USER lock line binds: the user's own direct order) PIN_DIR [$KIT_ROOT/repo]
#   RESULTS_TSV [$LATENT_ROOT/results_cells.tsv] RERUN_LOG_TSV [$LATENT_ROOT/queue_reruns.tsv]
#   HF_LEDGER [$LATENT_ROOT/hf_flow.tsv] HF_DELETE_AFTER_SCORE [1] STOP_FILE [$KIT_ROOT/STOP_queue]
#   ALERT_FILE [$KIT_ROOT/ALERT_queue.txt] HEARTBEAT_FILE [$KIT_ROOT/queue.heartbeat] QUEUE_LOCK_FILE
#   [$KIT_ROOT/locks/queue.lock] MIN_FREE_GB [30] ONLY_CELLS [] FORCE_CELLS [] DRY [0] PY [python3]
# Exit: 0 queue done (or nothing to do) and the end gate passed | 1 refused / STOP / end gate FAIL | 2 usage |
#   130/143 interrupted.
set -uo pipefail
MODE="${1:-run}"
case "$MODE" in run|plan) ;; *) sed -n '2,/^# Exit:/p' "$0" >&2; exit 2 ;; esac
HERE="$(cd "$(dirname "$0")" && pwd)"
KIT_ROOT="${KIT_ROOT:-/root/kit}"
LATENT_ROOT="${LATENT_ROOT:-$KIT_ROOT/latent}"
CELLS_TSV="${CELLS_TSV:-$KIT_ROOT/plan/cells.tsv}"
PLAN="${PLAN:-$KIT_ROOT/plan/PLAN.md}"
LEDGER="${LEDGER:-$KIT_ROOT/plan/FABLE_LEDGER.md}"
SLUG="${SLUG:-}"
ALLOW_BYPASS="${ALLOW_BYPASS:-0}"
PIN_DIR="${PIN_DIR:-$KIT_ROOT/repo}"
RESULTS_TSV="${RESULTS_TSV:-$LATENT_ROOT/results_cells.tsv}"
RERUN_LOG_TSV="${RERUN_LOG_TSV:-$LATENT_ROOT/queue_reruns.tsv}"
HF_LEDGER="${HF_LEDGER:-$LATENT_ROOT/hf_flow.tsv}"
HF_DELETE_AFTER_SCORE="${HF_DELETE_AFTER_SCORE:-1}"
STOP_FILE="${STOP_FILE:-$KIT_ROOT/STOP_queue}"
ALERT_FILE="${ALERT_FILE:-$KIT_ROOT/ALERT_queue.txt}"
HEARTBEAT_FILE="${HEARTBEAT_FILE:-$KIT_ROOT/queue.heartbeat}"
QUEUE_LOCK_FILE="${QUEUE_LOCK_FILE:-$KIT_ROOT/locks/queue.lock}"
MIN_FREE_GB="${MIN_FREE_GB:-30}"
ONLY_CELLS="${ONLY_CELLS:-}"
FORCE_CELLS="${FORCE_CELLS:-}"
DRY="${DRY:-0}"
PY="${PY:-python3}"
export PYTHONDONTWRITEBYTECODE=1 KIT_ROOT LATENT_ROOT CELLS_TSV RESULTS_TSV HF_LEDGER HF_DELETE_AFTER_SCORE DRY PY
TS="$(date -u +%Y%m%dT%H%M%SZ)"
_log() { printf '[queue %s] %s\n' "$(date -u +%H:%M:%S)" "$*" >&2; }
die() { _log "REFUSE: $*"; exit 1; }
for v in DRY ALLOW_BYPASS HF_DELETE_AFTER_SCORE; do case "${!v}" in 0|1) ;; *) _log "$v must be 0 or 1"; exit 2 ;; esac; done
[[ "$MIN_FREE_GB" =~ ^[0-9]+$ ]] || { _log "MIN_FREE_GB must be an integer"; exit 2; }
[ -f "$CELLS_TSV" ] || die "no cells file $CELLS_TSV (stage.sh ships the bound one to \$KIT_ROOT/plan/cells.tsv)"
VAL="$("$PY" "$HERE/cells.py" validate --cells "$CELLS_TSV" 2>&1)" || { printf '%s\n' "$VAL" >&2; die "$CELLS_TSV does not validate"; }

# ----------------------------------------------------------------------------------------------- the plan gate
GATE_ARGS=(gate --plan "$PLAN" --cells "$CELLS_TSV" --slug "${SLUG:-<unset>}" --ledger "$LEDGER" --pin-dir "$PIN_DIR")
[ "$ALLOW_BYPASS" = "1" ] && GATE_ARGS+=(--allow-bypass)
GATE_OUT="$("$PY" "$HERE/plan_gate.py" "${GATE_ARGS[@]}" 2>&1)"; GATE_RC=$?
printf '%s\n' "$GATE_OUT" >&2
if [ "$GATE_RC" != "0" ] || [ -z "$SLUG" ]; then
  if [ "$DRY" = "1" ] || [ "$MODE" = "plan" ]; then
    _log "the plan gate REFUSES (above) - a real run would stop here; $MODE goes on"
  else
    die "the plan gate refuses (above): no GPU work without a locked plan${SLUG:+ }$([ -z "$SLUG" ] && echo ' (SLUG unset)')"
  fi
fi

LATEST="$(mktemp "${TMPDIR:-/tmp}/queue_latest.XXXXXX")"; trap 'rm -f "$LATEST"' EXIT
refresh_latest() { "$PY" "$HERE/cells.py" latest --tsv "$RESULTS_TSV" > "$LATEST" 2>/dev/null || : > "$LATEST"; }
latest_of() { awk -F'\t' -v c="$1" -v k="$2" '$1 == c {print $k}' "$LATEST" | tail -n1; }   # k: 2 status 3 exit 4 run_dir
is_terminal() { case "$1" in OK|FAIL_TRAIN*|PRECONDITION*) return 0 ;; *) return 1 ;; esac; }
in_list() { case " $2 " in *" $1 "*) return 0 ;; *) return 1 ;; esac; }
CELLS="$("$PY" "$HERE/cells.py" list --cells "$CELLS_TSV")"
for c in $ONLY_CELLS $FORCE_CELLS; do printf '%s\n' "$CELLS" | cut -f1 | grep -qxF "$c" || die "ONLY_CELLS/FORCE_CELLS name an unknown cell: $c"; done
refresh_latest

if [ "$MODE" = "plan" ]; then
  printf '%-28s %-8s %-34s %s\n' cell enabled latest_status would
  while IFS='	' read -r cell enabled; do
    [ -n "$cell" ] || continue
    st="$(latest_of "$cell" 2)"; would="RUN"
    if [ -n "$ONLY_CELLS" ] && ! in_list "$cell" "$ONLY_CELLS"; then would="skip (ONLY_CELLS)"
    elif [ "$enabled" != "1" ]; then would="skip (enabled=0)"
    elif is_terminal "$st" && ! in_list "$cell" "$FORCE_CELLS"; then would="skip (terminal)"
    elif in_list "$cell" "$FORCE_CELLS"; then would="RUN (forced extra draw)"; fi
    printf '%-28s %-8s %-34s %s\n' "$cell" "$enabled" "${st:--}" "$would"
  done <<< "$CELLS"
  exit 0
fi

# ----------------------------------------------------------------------------------------------- run
[ -e "$STOP_FILE" ] && die "$STOP_FILE exists - remove it to resume"
mkdir -p "$(dirname "$QUEUE_LOCK_FILE")" "$LATENT_ROOT"
exec 8>"$QUEUE_LOCK_FILE"
flock -n 8 || die "another queue holds $QUEUE_LOCK_FILE"
if [ "$DRY" = "1" ]; then HF_LEDGER="$LATENT_ROOT/dry/hf_flow.$TS.tsv"; mkdir -p "$LATENT_ROOT/dry"; fi
rerun_log() {  # rerun_log CELL ACTION RC NOTE
  [ -s "$RERUN_LOG_TSV" ] || printf 'utc\tcell\taction\trc\tnote\n' > "$RERUN_LOG_TSV"
  printf '%s\t%s\t%s\t%s\t%s\n' "$(date -u +%FT%TZ)" "$1" "$2" "$3" "$(printf '%s' "$4" | tr '\t\n' '  ')" >> "$RERUN_LOG_TSV"
}
end_gate() {
  local args=(endgate --tsv "$HF_LEDGER" --policy "$([ "$HF_DELETE_AFTER_SCORE" = 1 ] && echo delete || echo keep)")
  [ "$DRY" = "1" ] && args+=(--no-sweep)
  if [ "$DRY" = "1" ] && [ ! -f "$HF_LEDGER" ]; then _log "end gate: no DRY ledger written"; return 0; fi
  local out rc
  out="$("$PY" "$HERE/hf_flow.py" "${args[@]}" 2>&1)"; rc=$?
  printf '%s\n' "$out" >&2
  return "$rc"
}
CHILD=""
on_signal() {
  local code="$1"
  trap '' INT TERM
  _log "signal: stopping (exit $code)"
  if [ -n "$CHILD" ]; then kill -TERM "$CHILD" 2>/dev/null; wait "$CHILD" 2>/dev/null; fi
  end_gate || _log "END GATE FAIL - see above"
  exit "$code"
}
trap 'on_signal 143' TERM
trap 'on_signal 130' INT
run_cell() {  # run_cell CELL [extra env assignments...] -> sets RC
  local cell="$1"; shift
  printf 'utc=%s cell=%s pid=%s\n' "$(date -u +%FT%TZ)" "$cell" "$$" > "$HEARTBEAT_FILE"
  env "$@" HF_LEDGER="$HF_LEDGER" HF_DRY_LEDGER="$HF_LEDGER" bash "$HERE/cell_run.sh" "$cell" &
  CHILD=$!
  wait "$CHILD"; RC=$?
  CHILD=""
}
free_gb() { df -Pk "$1" 2>/dev/null | awk 'NR==2 {printf "%d", $4/1024/1024}'; }

N_RUN=0; END_RC=0; STOPPED=""
while IFS='	' read -r cell enabled; do
  [ -n "$cell" ] || continue
  if [ -e "$STOP_FILE" ]; then STOPPED="STOP file $STOP_FILE"; break; fi
  if [ -n "$ONLY_CELLS" ] && ! in_list "$cell" "$ONLY_CELLS"; then continue; fi
  [ "$enabled" = "1" ] || { _log "$cell: enabled=0 - skip"; continue; }
  refresh_latest
  st="$(latest_of "$cell" 2)"
  if is_terminal "$st"; then
    if in_list "$cell" "$FORCE_CELLS"; then rerun_log "$cell" forced - "latest was $st"; _log "$cell: FORCED extra draw (latest $st)"
    else _log "$cell: latest row $st is terminal - RESUME SKIP"; continue; fi
  fi
  fg="$(free_gb "$KIT_ROOT")"
  if [ "$DRY" != "1" ] && { [ -z "$fg" ] || [ "$fg" -lt "$MIN_FREE_GB" ]; }; then
    printf '%s disk: %s GB free under %s < MIN_FREE_GB=%s before cell %s\n' "$(date -u +%FT%TZ)" "${fg:-?}" "$KIT_ROOT" "$MIN_FREE_GB" "$cell" >> "$ALERT_FILE"
    STOPPED="disk below MIN_FREE_GB"; break
  fi
  _log "$cell: attempt"
  run_cell "$cell"; N_RUN=$((N_RUN + 1))
  case "$RC" in
    0|3|6) _log "$cell: done rc=$RC (terminal)"; continue ;;
    2) STOPPED="cell_run.sh usage error on $cell (rc 2) - fix the cells file / environment"; break ;;
    130|143) STOPPED="cell $cell interrupted (rc $RC)"; break ;;
  esac
  # box-level: one OPS-RERUN for this cell
  refresh_latest
  rd="$(latest_of "$cell" 4)"
  if { [ "$RC" = "4" ] || [ "$RC" = "5" ]; } && [ -n "$rd" ] && [ -d "$rd/checkpoint" ]; then
    rerun_log "$cell" rescore "$RC" "from $rd"; _log "$cell: OPS-RERUN = rescore of $rd"
    run_cell "$cell" RESCORE=1 RESCORE_FROM="$rd"
  else
    rerun_log "$cell" rerun "$RC" "fresh attempt"; _log "$cell: OPS-RERUN = fresh attempt"
    run_cell "$cell"
  fi
  N_RUN=$((N_RUN + 1))
  case "$RC" in
    0|3|6) _log "$cell: OPS-RERUN done rc=$RC (terminal)" ;;
    2) STOPPED="cell_run.sh usage error on $cell (rc 2)"; break ;;
    130|143) STOPPED="cell $cell interrupted (rc $RC)"; break ;;
    *) printf '%s %s failed its OPS-RERUN too (rc %s): two consecutive box-level failures, run %s\n' "$(date -u +%FT%TZ)" "$cell" "$RC" "${rd:--}" >> "$ALERT_FILE"
       STOPPED="$cell failed its OPS-RERUN - two consecutive box-level failures (ALERT $ALERT_FILE)"; break ;;
  esac
done <<< "$CELLS"

end_gate || END_RC=1
if [ -n "$STOPPED" ]; then _log "QUEUE STOPPED: $STOPPED ($N_RUN attempt(s))"; exit 1; fi
[ "$END_RC" = "0" ] || { _log "QUEUE ENDED (HF end gate FAIL)"; exit 1; }
_log "QUEUE DONE: $N_RUN attempt(s); results $RESULTS_TSV"
exit 0
