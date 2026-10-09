#!/bin/bash
# stage.sh BOX - run on your WORKSTATION. Sends everything a GPU session needs to the box, verifies it there, prints
# the next steps. BOX is user@host (never hard-coded here; pass the box you rented as the argument).
#
#   ARCHIVE=<archives>/tourn_<id>_<yyyymmdd> REPO=<your miner repo> BRANCH=<branch> \
#     CELLS_TSV=<bound cells.tsv> PLAN_FILE=<PLAN.md> LEDGER_FILE=<FABLE_LEDGER.md> bash stage.sh root@<box>
#   DRY=1 ... bash stage.sh root@<box>          verify the local side and print the transfer plan; no network
#   BOX=local KIT_ROOT=/tmp/fakebox/kit ... bash stage.sh local    self-test: the "box" is a local directory (no ssh)
#
# What lands on the box (KIT_ROOT [/root/kit]; provision.sh must have run there first):
#   ops/           this kit (rsync --delete; __pycache__ / .DS_Store excluded; vendor/god/* is shipped if you fetched it)
#   data/<tourn>/  the archive: <task>/{test_data,training_data}.zip (or tasks/<round>_<family>_<task8>/ + uuid links made
#                  on the box by archive_layout.py link), api/*.json, artifacts/lora/ (pinned anchors + MANIFEST.tsv) -
#                  never --delete: data on the box is never removed by this script
#   api/           flat copy of the task jsons (anchors.sh, board.py)
#   repo/          ONE clean clone of REPO at the PINNED commit: pin_sha of the bound CELLS_TSV (lock_cells.sh), else
#                  REPO_PIN, else the BRANCH tip with a WARNING (the queue's plan gate then refuses a real run). Only
#                  committed state is shipped - your working tree is never read - and the box HEAD must equal the pin.
#   plan/          cells.tsv (CELLS_TSV), PLAN.md (PLAN_FILE) and FABLE_LEDGER.md (LEDGER_FILE, only with a plan): what
#                  queue.sh's plan gate reads. A verdict written to your ledger after staging reaches the box only by
#                  staging again.
#
# Order: (1) local preflight with verify_stage.py on the SOURCE (zips CRC + captions, api json, LoRA sha256 vs the HF LFS
# oids in MANIFEST.tsv, kit syntax); (2) box preflight: reachable, provisioned, no trainer/evaluator container running
# (never swap code under a live run), enough disk; (3) transfers with retries (rsync over ssh with keepalives and
# --partial: long streams do drop); (4) verify_stage.py ON THE BOX against what landed - a transfer's exit code is not
# proof of delivery. Exit 0 only if (4) passes. Idempotent: re-running re-sends only what differs.
# LORA_VIA: rsync (send the anchors) | box (the box runs fetch_artifacts.py itself: same pins, same sha256 check; forced
# when the archive carries PINS.tsv but no MANIFEST.tsv) | none (task data only).
#
# env: ARCHIVE [required] REPO [] BRANCH [] REPO_PIN [] CELLS_TSV [] PLAN_FILE [] LEDGER_FILE [] KIT_ROOT [/root/kit]
#      DRY [0] ATTEMPTS [3] SKIP_LOCAL_HASH [0] MIN_FREE_GB [20] LORA_VIA [rsync] ALLOW_NO_MANIFEST [0] BUSY_RE
set -euo pipefail
usage() { sed -n '2,8p' "$0" >&2; exit 2; }
[ "$#" -eq 1 ] || usage
BOX="$1"
HERE="$(cd "$(dirname "$0")" && pwd)"
ARCHIVE="${ARCHIVE:-}"; ARCHIVE="${ARCHIVE%/}"
REPO="${REPO:-}"; BRANCH="${BRANCH:-}"; REPO_PIN="${REPO_PIN:-}"
CELLS_TSV="${CELLS_TSV:-}"; PLAN_FILE="${PLAN_FILE:-}"; LEDGER_FILE="${LEDGER_FILE:-}"
KIT_ROOT="${KIT_ROOT:-/root/kit}"
DRY="${DRY:-0}"; ATTEMPTS="${ATTEMPTS:-3}"; SKIP_LOCAL_HASH="${SKIP_LOCAL_HASH:-0}"; MIN_FREE_GB="${MIN_FREE_GB:-20}"
LORA_VIA="${LORA_VIA:-rsync}"; ALLOW_NO_MANIFEST="${ALLOW_NO_MANIFEST:-0}"
BUSY_RE="${BUSY_RE:-(^| )(image-trainer-|downloader-|kitup-|latent-)|god-image-eval:|gradientsio/}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
SSH_OPTS="-o ConnectTimeout=20 -o ServerAliveInterval=15 -o ServerAliveCountMax=8 -o BatchMode=yes"
export PYTHONDONTWRITEBYTECODE=1
_log() { printf '[stage %s] %s\n' "$(date -u +%H:%M:%S)" "$*" >&2; }
die() { _log "REFUSE: $*"; exit 1; }
case "$DRY" in 0|1) ;; *) die "DRY must be 0 or 1" ;; esac
case "$LORA_VIA" in rsync|box|none) ;; *) die "LORA_VIA must be rsync, box or none" ;; esac
case "$ALLOW_NO_MANIFEST" in 0|1) ;; *) die "ALLOW_NO_MANIFEST must be 0 or 1" ;; esac
[[ "$ATTEMPTS" =~ ^[1-9][0-9]*$ ]] || die "ATTEMPTS must be a positive integer"
[[ "$MIN_FREE_GB" =~ ^[0-9]+$ ]] || die "MIN_FREE_GB must be an integer"
[ -n "$ARCHIVE" ] && [ -d "$ARCHIVE" ] || die "ARCHIVE must be an archive dir (tourn_<id>_<yyyymmdd>), got '${ARCHIVE:-<unset>}'"
TOURN="$(basename "$ARCHIVE")"
[[ "$TOURN" =~ ^tourn_[0-9a-f]+_[0-9]{8}$ ]] || die "ARCHIVE basename '$TOURN' is not tourn_<id>_<yyyymmdd>"
[ -z "$LEDGER_FILE" ] || [ -n "$PLAN_FILE" ] || die "LEDGER_FILE is shipped only together with PLAN_FILE"
for f in "$CELLS_TSV" "$PLAN_FILE" "$LEDGER_FILE"; do [ -z "$f" ] || [ -f "$f" ] || die "no such file: $f"; done

PINS_ONLY=0
if [ -f "$ARCHIVE/artifacts/lora/PINS.tsv" ] && [ ! -f "$ARCHIVE/artifacts/lora/MANIFEST.tsv" ] && [ "$LORA_VIA" != "none" ]; then
  [ "$LORA_VIA" = "box" ] || _log "the archive carries PINS.tsv and no MANIFEST.tsv: its anchors are fetched ON THE BOX (LORA_VIA=box)"
  LORA_VIA=box; PINS_ONLY=1
fi
if [ "$LORA_VIA" = "rsync" ] && [ ! -f "$ARCHIVE/artifacts/lora/MANIFEST.tsv" ]; then
  die "no $ARCHIVE/artifacts/lora/MANIFEST.tsv nor PINS.tsv - pin + fetch the anchors (fetch_artifacts.py) or LORA_VIA=none"
fi
LORA_EXCLUDE=""; [ "$LORA_VIA" != "rsync" ] && LORA_EXCLUDE="/artifacts/lora/*.safetensors"

# the checkout to ship: pin_sha of the bound cells file > REPO_PIN > branch tip
SHIP_SHA=""
if [ -n "$REPO" ]; then
  [ -n "$BRANCH" ] || die "REPO needs BRANCH (the pin must be a commit of that branch)"
  git -C "$REPO" rev-parse --git-dir >/dev/null 2>&1 || die "not a git repo: $REPO"
  TIP="$(git -C "$REPO" rev-parse --verify --quiet "refs/heads/$BRANCH^{commit}" || true)"
  [ -n "$TIP" ] || die "no branch $BRANCH in $REPO (only committed state is shipped)"
  BIND_PIN=""
  if [ -n "$CELLS_TSV" ]; then
    eval "$(python3 -B "$HERE/plan_gate.py" binding --cells "$CELLS_TSV")" || die "$CELLS_TSV line 1 carries a malformed pin_sha"
    BIND_PIN="${BIND_PIN_SHA:-}"
  fi
  [ -z "$REPO_PIN" ] || [[ "$REPO_PIN" =~ ^[0-9a-f]{40}$ ]] || die "REPO_PIN must be a full 40-hex SHA"
  if [ -n "$BIND_PIN" ] && [ -n "$REPO_PIN" ] && [ "$BIND_PIN" != "$REPO_PIN" ]; then
    die "REPO_PIN=$REPO_PIN but the cells binding pins $BIND_PIN - one SHA"
  fi
  SHIP_SHA="${BIND_PIN:-${REPO_PIN:-}}"
  if [ -n "$SHIP_SHA" ]; then
    git -C "$REPO" cat-file -e "${SHIP_SHA}^{commit}" 2>/dev/null || die "the pin $SHIP_SHA is not a commit of $REPO"
    git -C "$REPO" merge-base --is-ancestor "$SHIP_SHA" "$TIP" || die "the pin $SHIP_SHA is not on $BRANCH (tip $TIP)"
    _log "repo: $BRANCH PINNED at $SHIP_SHA (tip $TIP)"
  else
    SHIP_SHA="$TIP"
    _log "WARNING: no pin (cells file unbound, REPO_PIN unset): shipping the $BRANCH TIP $TIP - the queue's plan gate refuses real runs until a bound cells file is staged"
  fi
fi

if [ "$BOX" = "local" ]; then
  case "$KIT_ROOT" in /root|/root/*) die "BOX=local needs KIT_ROOT pointing at a scratch dir, not $KIT_ROOT" ;; esac
  rsh() { bash -c "$1"; }
  target() { printf '%s' "$1"; }
else
  [[ "$BOX" =~ ^[A-Za-z0-9._-]+@[A-Za-z0-9._:-]+$ ]] || die "BOX must look like user@host, got '$BOX'"
  rsh() { ssh $SSH_OPTS "$BOX" "$1"; }
  target() { printf '%s:%s' "$BOX" "$1"; }
  export RSYNC_RSH="ssh $SSH_OPTS"
fi
q() { printf '%q' "$1"; }

# (1) local preflight
HASH_FLAG=""; [ "$SKIP_LOCAL_HASH" = "1" ] && HASH_FLAG="--no-hash"
MAN_FLAG=""; [ "$ALLOW_NO_MANIFEST" = "1" ] && MAN_FLAG="--allow-no-manifest"
LORA_FLAG=""; [ "$PINS_ONLY" = "1" ] && LORA_FLAG="--pins-only"; [ "$LORA_VIA" = "none" ] && LORA_FLAG="--no-lora"
LAYOUT="$(python3 -B "$HERE/archive_layout.py" detect --tourn "$ARCHIVE" 2>&1 | sed -n 's/^LAYOUT=//p' || true)"
case "$LAYOUT" in uuid|tasks|linked) ;; *) die "cannot read the task layout of $ARCHIVE (archive_layout.py detect: '${LAYOUT:-no answer}')" ;; esac
VERIFY_ROOT="$ARCHIVE"; VIEW_DIR=""
if [ "$LAYOUT" != "uuid" ]; then
  VIEW_DIR="$(mktemp -d "${TMPDIR:-/tmp}/stage_view.XXXXXX")"; trap 'rm -rf "$VIEW_DIR"' EXIT
  VERIFY_ROOT="$(python3 -B "$HERE/archive_layout.py" view --tourn "$ARCHIVE" --out "$VIEW_DIR" 2>&1)" \
    || die "the tasks/ layout of $ARCHIVE does not map to its api/<task_id>.json: $VERIFY_ROOT"
fi
LOCAL_LOG="${TMPDIR:-/tmp}/stage_local_$TS.log"
_log "local preflight: verify_stage.py on the SOURCE archive + kit${LORA_FLAG:+ ($LORA_FLAG)}"
python3 -B "$HERE/verify_stage.py" --tourn "$VERIFY_ROOT" --ops "$HERE" $HASH_FLAG $LORA_FLAG $MAN_FLAG > "$LOCAL_LOG" 2>&1 \
  || { grep -E '^\[verify\] FAIL' "$LOCAL_LOG" >&2; tail -n 2 "$LOCAL_LOG" >&2; die "local source is not intact (full log: $LOCAL_LOG)"; }
_log "$(tail -n 1 "$LOCAL_LOG")"
if [ -n "$CELLS_TSV" ]; then python3 -B "$HERE/cells.py" validate --cells "$CELLS_TSV" >/dev/null || die "$CELLS_TSV does not validate"; fi
ARCHIVE_GB="$(du -sk "$ARCHIVE" | awk '{printf "%d", $1/1024/1024 + 1}')"

if [ "$DRY" = "1" ]; then
  _log "DRY: would check $BOX (reachable, $KIT_ROOT provisioned, no container matching $BUSY_RE, >= $((ARCHIVE_GB + MIN_FREE_GB)) GB free)"
  _log "DRY: rsync kit $HERE/ -> $(target "$KIT_ROOT/ops/") (--delete)"
  _log "DRY: rsync archive $ARCHIVE/ -> $(target "$KIT_ROOT/data/$TOURN/")${LORA_EXCLUDE:+ (excluding $LORA_EXCLUDE)} (${ARCHIVE_GB} GB)"
  [ "$LAYOUT" != "uuid" ] && _log "DRY: on the box: archive_layout.py link --tourn $KIT_ROOT/data/$TOURN"
  [ "$LORA_VIA" = "box" ] && _log "DRY: on the box: fetch_artifacts.py $KIT_ROOT/data/$TOURN"
  [ -n "$SHIP_SHA" ] && _log "DRY: clone $BRANCH@$SHIP_SHA -> $(target "$KIT_ROOT/repo/")"
  for f in "$CELLS_TSV:cells.tsv" "$PLAN_FILE:PLAN.md" "$LEDGER_FILE:FABLE_LEDGER.md"; do
    [ -n "${f%%:*}" ] && _log "DRY: ${f%%:*} -> $(target "$KIT_ROOT/plan/${f##*:}")"
  done
  _log "DRY: then on the box: verify_stage.py --tourn $KIT_ROOT/data/$TOURN --api-flat $KIT_ROOT/api --ops $KIT_ROOT/ops${SHIP_SHA:+ --repo-spec $KIT_ROOT/repo=$SHIP_SHA}"
  exit 0
fi

# (2) box preflight
rsh "echo ok" >/dev/null 2>&1 || die "box not reachable: $BOX"
rsh "test -d $(q "$KIT_ROOT/locks") && test -d $(q "$KIT_ROOT/data") && test -d $(q "$KIT_ROOT/ops")" \
  || die "$KIT_ROOT is not provisioned on $BOX - run provision.sh there first"
running="$(rsh "docker ps --format '{{.Names}} {{.Image}}' 2>/dev/null | grep -E $(q "$BUSY_RE") || true")"
[ -z "$running" ] || die "a trainer/evaluator container is running on the box ($running) - never swap code under a live run"
FREE_GB="$(rsh "df -Pk $(q "$KIT_ROOT") | awk 'NR==2 {printf \"%d\", \$4/1024/1024}'")"
[[ "$FREE_GB" =~ ^[0-9]+$ ]] || die "cannot read free disk on the box"
[ "$FREE_GB" -ge $((ARCHIVE_GB + MIN_FREE_GB)) ] || die "box has ${FREE_GB} GB free under $KIT_ROOT, need >= $((ARCHIVE_GB + MIN_FREE_GB))"
_log "box ok: provisioned, idle, ${FREE_GB} GB free"

# (3) transfers
push() {  # push SRC DST [rsync args...] - retried; the box-side check (4) is the real verdict
  local src="$1" dst="$2" i=1; shift 2
  while :; do
    if rsync -rlpt --partial --timeout=120 "$@" "$src" "$(target "$dst")"; then return 0; fi
    [ "$i" -lt "$ATTEMPTS" ] || return 1
    i=$((i + 1)); _log "rsync to $dst failed - attempt $i/$ATTEMPTS in 10 s"; sleep 10
  done
}
T0=$(date +%s)
push "$HERE/" "$KIT_ROOT/ops/" --delete --exclude __pycache__ --exclude .DS_Store || die "kit transfer failed"
rsh "mkdir -p $(q "$KIT_ROOT/data/$TOURN")"
if [ -n "$LORA_EXCLUDE" ]; then
  push "$ARCHIVE/" "$KIT_ROOT/data/$TOURN/" --exclude .DS_Store --exclude "$LORA_EXCLUDE" || die "archive transfer failed (re-run: idempotent)"
else
  push "$ARCHIVE/" "$KIT_ROOT/data/$TOURN/" --exclude .DS_Store || die "archive transfer failed (re-run: idempotent, --partial resumes)"
fi
if [ "$LAYOUT" != "uuid" ]; then
  rsh "python3 -B $(q "$KIT_ROOT/ops/archive_layout.py") link --tourn $(q "$KIT_ROOT/data/$TOURN")" || die "archive_layout.py link failed on the box"
fi
if [ "$LORA_VIA" = "box" ]; then
  _log "LORA_VIA=box: the box fetches the anchors from HF itself (pinned commits, LFS sha256 checked)"
  rsh "python3 -B $(q "$KIT_ROOT/ops/fetch_artifacts.py") $(q "$KIT_ROOT/data/$TOURN")" || die "box-side fetch_artifacts.py failed - re-run (verified downloads are kept)"
fi
rsh "mkdir -p $(q "$KIT_ROOT/api") && cp -f $(q "$KIT_ROOT/data/$TOURN/api")/*-*-*-*-*.json $(q "$KIT_ROOT/api")/"
SPECS=()
if [ -n "$SHIP_SHA" ]; then
  CLONE="$(mktemp -d "${TMPDIR:-/tmp}/stage_repo.XXXXXX")"
  git clone -q --no-hardlinks --single-branch --branch "$BRANCH" "file://$REPO" "$CLONE/repo" || { rm -rf "$CLONE"; die "git clone of $BRANCH failed"; }
  git -C "$CLONE/repo" -c advice.detachedHead=false checkout -q --detach "$SHIP_SHA" || { rm -rf "$CLONE"; die "checkout of $SHIP_SHA failed"; }
  [ "$(git -C "$CLONE/repo" rev-parse HEAD)" = "$SHIP_SHA" ] || { rm -rf "$CLONE"; die "clone HEAD != $SHIP_SHA"; }
  rsh "mkdir -p $(q "$KIT_ROOT/repo")"
  push "$CLONE/repo/" "$KIT_ROOT/repo/" --delete || { rm -rf "$CLONE"; die "repo transfer failed"; }
  rm -rf "$CLONE"
  SPECS+=(--repo-spec "$KIT_ROOT/repo=$SHIP_SHA")
fi
if [ -n "$CELLS_TSV$PLAN_FILE" ]; then rsh "mkdir -p $(q "$KIT_ROOT/plan")"; fi
[ -z "$CELLS_TSV" ] || push "$CELLS_TSV" "$KIT_ROOT/plan/cells.tsv" || die "cells transfer failed"
[ -z "$PLAN_FILE" ] || push "$PLAN_FILE" "$KIT_ROOT/plan/PLAN.md" || die "plan transfer failed"
[ -z "$LEDGER_FILE" ] || push "$LEDGER_FILE" "$KIT_ROOT/plan/FABLE_LEDGER.md" || die "ledger transfer failed"
_log "transfers done in $(( $(date +%s) - T0 )) s"

# (4) box-side verification
BOX_LORA_FLAG=""; [ "$LORA_VIA" = "none" ] && BOX_LORA_FLAG="--no-lora"
QSPECS=""; for x in ${SPECS[@]+"${SPECS[@]}"}; do QSPECS="$QSPECS $(q "$x")"; done
rsh "python3 -B $(q "$KIT_ROOT/ops/verify_stage.py") --tourn $(q "$KIT_ROOT/data/$TOURN") --api-flat $(q "$KIT_ROOT/api")$QSPECS --ops $(q "$KIT_ROOT/ops") $BOX_LORA_FLAG $MAN_FLAG" \
  || die "box-side verification FAILED - see the FAIL lines above; re-run stage.sh (idempotent)"
_log "STAGED $TOURN${SHIP_SHA:+ + repo@$SHIP_SHA}${CELLS_TSV:+ + plan bundle} on $BOX"
_log "next on the box: build the evaluator (build_eval_image.sh), warm its cache (anchors.sh warm), calibrate (anchors.sh run), build your trainer image from $KIT_ROOT/repo, then: bash $KIT_ROOT/ops/queue.sh plan"
