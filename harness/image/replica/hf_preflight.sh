#!/usr/bin/env bash
# hf_preflight.sh - prove, ONCE per box (and again whenever the token, the account or the uploader image changes),
# that this box can do the validator's upload half end to end with the user's token. Run on the BOX:
#
#   bash $KIT_ROOT/ops/hf_preflight.sh           real: the token file, whoami-v2, the production uploader image (pulled
#                                                if missing, its uploader.py checked against the reviewed sha256), then
#                                                a DUMMY round trip: a 4 KiB public repo <prefix>-00000000-<rand6> made by the
#                                                production uploader container -> verified public (anonymous 200) and
#                                                byte-identical (sha256) -> downloaded anonymously -> deleted -> 404
#   DRY=1 bash hf_preflight.sh                   the same round trip on the loopback mock hub with the vendored
#                                                uploader.py: no network, no docker, no token used (a mock one)
#
# PASS writes the stamp $KIT_ROOT/locks/hf_preflight.ok (account, uploader image id + code sha256, UTC); cell_run.sh
# runs this script itself when the stamp is missing, older than HF_PREFLIGHT_MAX_AGE_H (24) or names another account /
# image. Every repo it creates goes into the HF ledger ($LATENT_ROOT/hf_flow.tsv) BEFORE the upload starts, so a
# killed preflight leaves a "pending" line that `python3 hf_flow.py sweep --tsv ...` deletes.
# The token: only $HF_ENV_FILE (default /root/.hf_env, one line HF_TOKEN=hf_..., mode 600). Never printed; only the
# ACCOUNT NAME from whoami-v2 is.
# Exit: 0 PASS, 1 FAIL (the reason is the last line), 2 usage.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
KIT_ROOT="${KIT_ROOT:-/root/kit}"
LATENT_ROOT="${LATENT_ROOT:-$KIT_ROOT/latent}"
CACHE_DIR="${CACHE_DIR:-$KIT_ROOT/cache}"
DRY="${DRY:-0}"
PREFLIGHT_PULL="${PREFLIGHT_PULL:-1}"
HF_LEDGER="${HF_LEDGER:-$LATENT_ROOT/hf_flow.tsv}"
STAMP="${HF_PREFLIGHT_STAMP:-$KIT_ROOT/locks/hf_preflight.ok}"
PY="${PY:-python3}"
PREFLIGHT_TASK="00000000-0000-4000-8000-000000000000"     # not a task: the dummy repo's name says nothing
_log() { printf '[hf_preflight %s] %s\n' "$(date -u +%FT%TZ)" "$*" >&2; }
fail() { _log "FAIL: $*"; exit 1; }
case "$DRY" in 0|1) ;; *) echo "DRY must be 0 or 1" >&2; exit 2 ;; esac
[ "$DRY" = "1" ] && HF_MODE=mock
# shellcheck source=hf_lib.sh
. "$HERE/hf_lib.sh"
trap 'hf_cleanup' EXIT
trap 'hf_cleanup; exit 130' INT
trap 'hf_cleanup; exit 143' TERM

TS="$(date -u +%Y%m%dT%H%M%SZ)"
WORK="$KIT_ROOT/hf_preflight/${TS}_$$"
mkdir -p "$WORK" "$(dirname "$STAMP")" "$(dirname "$HF_LEDGER")" || fail "cannot create $WORK"

# 1. the token file (both modes check the REAL file's shape when it exists; DRY never sends it anywhere)
if [ "$DRY" = "1" ]; then
  if [ -e "$HF_ENV_FILE" ]; then
    eval "$(hf_flow tokencheck --env-file "$HF_ENV_FILE" 2>/dev/null)"
    _log "token file (DRY: shape only, not used): $([ "${HF_TOKEN_OK:-0}" = 1 ] && echo PASS || echo FAIL) - $HF_TOKEN_WHY"
  else
    _log "token file: $HF_ENV_FILE absent (DRY: fine - the mock hub has its own mock token)"
  fi
  hf_mock_start "$WORK/mockhub" || fail "mock hub did not start"
else
  eval "$(hf_flow tokencheck --env-file "$HF_ENV_FILE" 2>/dev/null)"
  [ "${HF_TOKEN_OK:-0}" = "1" ] || fail "${HF_TOKEN_WHY:-token file check failed}"
  _log "PASS token-file  $HF_TOKEN_WHY"
fi

# 2. whoami-v2: the account name only
hf_whoami || fail "whoami-v2: ${HF_WHY:-no answer}"
_log "PASS whoami      account=$HF_USER (write: $([ "$HF_WRITE" = 1 ] && echo yes || echo "$HF_WRITE - proven below by the dummy round trip"))"

# 3. the production uploader image + its code
if [ "$HF_MODE" = real ] && ! docker image inspect "$UPLOADER_IMAGE" >/dev/null 2>&1; then
  [ "$PREFLIGHT_PULL" = "1" ] || fail "$UPLOADER_IMAGE is missing and PREFLIGHT_PULL=0"
  _log "pulling $UPLOADER_IMAGE (production uploader, trainer/constants.py:24)"
  docker pull "$UPLOADER_IMAGE" >&2 || fail "docker pull $UPLOADER_IMAGE failed"
fi
hf_uploader_review || fail "uploader image review"
_log "PASS uploader    image=$UPLOADER_IMAGE id=${HF_UPLOADER_ID:0:19} digest=${HF_UPLOADER_DIGEST:-?} uploader.py sha256=${HF_UPLOADER_SHA:0:16}"

# 4. the dummy round trip, through the same code a cell uses (hf_run_uploader)
eval "$(hf_flow newname --task-id "$PREFLIGHT_TASK")"
REPO="$HF_USER/$HF_REPO_NAME"
ROOT="$WORK/root"; DIRK="$ROOT/$PREFLIGHT_TASK/$HF_REPO_NAME"; COPY="$WORK/copy"
mkdir -p "$DIRK" "$COPY"
eval "$(hf_flow mkdummy --out "$DIRK/last.safetensors")"
cp -p "$DIRK/last.safetensors" "$COPY/last.safetensors"
hf_flow ledger --tsv "$HF_LEDGER" event=uploading cell=PREFLIGHT "attempt=$TS" "task_id=$PREFLIGHT_TASK" "repo=$REPO" \
  source=preflight "local_sha256=$DUMMY_SHA256" "note=mode=$HF_MODE" || fail "cannot write the HF ledger $HF_LEDGER"
T0=$(date +%s)
hf_run_uploader "$ROOT" "$PREFLIGHT_TASK" "$HF_REPO_NAME" "preflight/none" "$WORK/upload.log" "preflight"
UP_RC=$?
REASON=""
if [ "$UP_RC" != "0" ]; then
  REASON="the uploader exited $UP_RC: $(tail -n 3 "$WORK/upload.log" 2>/dev/null | tr '\n' ' ')"
else
  _log "PASS upload      $REPO (production uploader, ${HF_UP_START} -> ${HF_UP_END})"
  [ ! -e "$DIRK" ] && _log "PASS uploader-rm the uploader deleted its LOCAL_FOLDER, as production (uploader.py:237-242)"
  eval "$(hf_flow verify --env-file "$HF_EFFECTIVE_ENV_FILE" --api "$HF_HUB_API" --repo "$REPO" --local-dir "$COPY" 2>/dev/null)"
  if [ "${HF_VERIFY_OK:-0}" != "1" ]; then
    REASON="verify: ${HF_VERIFY_WHY:-?}"
  else
    _log "PASS public      private=$HF_PRIVATE anonymous=$HF_ANON_HTTP select_lora=$HF_SELECTED sha256=${HF_SELECTED_SHA256:0:16} == local"
    eval "$(hf_flow anonget --api "$HF_HUB_API" --repo "$REPO" --rev "$HF_REVISION" --file "$HF_SELECTED" 2>/dev/null)"
    if [ "${HF_ANON_SHA256:-}" != "$DUMMY_SHA256" ]; then
      REASON="anonymous download answered ${HF_ANON_HTTP:-?} sha256=${HF_ANON_SHA256:-none}, want $DUMMY_SHA256"
    else
      _log "PASS anon-read   an anonymous download of $HF_SELECTED is byte-identical (sha256 ${HF_ANON_SHA256:0:16})"
    fi
  fi
fi
eval "$(hf_flow delete --env-file "$HF_EFFECTIVE_ENV_FILE" --api "$HF_HUB_API" --repo "$REPO" 2>/dev/null)"
EXPO=$(( $(date +%s) - T0 ))
hf_flow ledger --tsv "$HF_LEDGER" event="$([ "${HF_DELETED:-0}" = 1 ] && echo deleted || echo delete_failed)" cell=PREFLIGHT \
  "attempt=$TS" "task_id=$PREFLIGHT_TASK" "repo=$REPO" source=preflight "revision=${HF_REVISION:-}" \
  "local_sha256=$DUMMY_SHA256" "hub_sha256=${HF_SELECTED_SHA256:-}" "upload_start_utc=${HF_UP_START:-}" \
  "upload_end_utc=${HF_UP_END:-}" "deleted_utc=${HF_DELETED_UTC:-}" "exposure_s=$EXPO" \
  "status=$([ -z "$REASON" ] && echo PASS || echo FAIL)" "note=mode=$HF_MODE; ${REASON:-ok}; delete: ${HF_DELETE_WHY:-?}" || true
if [ "${HF_DELETED:-0}" != "1" ]; then
  fail "the dummy repo $REPO could NOT be deleted (${HF_DELETE_WHY:-?}) - delete it by hand on huggingface.co, or: python3 $HERE/hf_flow.py sweep --tsv $HF_LEDGER"
fi
_log "PASS delete      $REPO (${HF_DELETE_WHY}); public for ${EXPO}s"
[ -z "$REASON" ] || fail "$REASON"
{
  printf 'utc=%s\nmode=%s\naccount=%s\nuploader_image=%s\nuploader_id=%s\nuploader_sha256=%s\nrepo=%s\nexposure_s=%s\n' \
    "$(date -u +%FT%TZ)" "$HF_MODE" "$HF_USER" "$UPLOADER_IMAGE" "$HF_UPLOADER_ID" "$HF_UPLOADER_SHA" "$REPO" "$EXPO"
} > "$STAMP.tmp" && mv -f "$STAMP.tmp" "$STAMP"
_log "PASS preflight   stamp $STAMP"
echo "HF PREFLIGHT PASS account=$HF_USER mode=$HF_MODE"
exit 0
