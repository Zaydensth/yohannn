# hf_lib.sh - sourced by cell_run.sh and hf_preflight.sh (never run). The UPLOAD hand-off of a validator-shaped
# cell: the PRODUCTION uploader image on the box, or - HF_MODE=mock (DRY=1, CPU tests without docker) - the same
# upstream uploader.py (vendor/god, fetched by vendor/fetch_god_vendor.sh) run by the local python against the loopback
# mock hub (hfmock/). G.O.D trainer/runtime.py:790-871 (read at b49ba8644a0d) is the contract this file copies:
#   image   gradientsio/trainer-uploader:latest (trainer/constants.py:24); entrypoint python trainer/containers/uploader.py
#   env     HUGGINGFACE_TOKEN HUGGINGFACE_USERNAME (here: an --env-file made 0600, removed right after the run)
#           WANDB_LOGS_PATH=/app/checkpoints/wandb_logs/<task>_<hotkey> (here: <task>_replica - no hotkey anywhere)
#           LOCAL_FOLDER=/app/checkpoints/<task>/<repo>  MODEL=<task model>  TASK_ID  EXPECTED_REPO_NAME=<repo>
#           HF_REPO_SUBFOLDER=checkpoints (image task, runtime.py:1140)  -  no WANDB_TOKEN (image task, runtime.py:1141)
#   mounts  checkpoints:/app/checkpoints:rw  cache:/cache:rw  (here: the trainer's CHECKPOINTS_VOLUME and CACHE_DIR)
#   life    detach, wait, read the logs, exit code != 0 = upload failed; remove the container in `finally`
# The uploader DELETES LOCAL_FOLDER after a successful upload (uploader.py:237-242) - it only ever sees the trainer's
# named volume or a scratch copy, never a run dir (never bind-mount a workspace into a path something deletes).
# Needs from the caller: HERE PY _log; optional: HF_ENV_FILE HF_HUB_API UPLOADER_IMAGE CHECKPOINTS_VOLUME CACHE_DIR
#   UPLOAD_TIMEOUT_S ALLOW_UNREVIEWED_UPLOADER HF_MOCK_DIR HF_MOCK_USER HF_UPLOADER_REVIEWED_SHA256.
#
# The reviewed uploader: YOU read trainer/containers/uploader.py at your G.O.D pin (vendor/fetch_god_vendor.sh prints
# its sha256) and record that sha256 either in HF_UPLOADER_REVIEWED_SHA256 or as the first word of
# <kit>/reviewed_uploader.sha256. Before every upload the code inside the uploader IMAGE is hashed and compared with it;
# anything else is refused unless ALLOW_UNREVIEWED_UPLOADER=1 (what it uploads, where, public or not is its business).

HF_UPLOADER_REVIEWED_SHA256="${HF_UPLOADER_REVIEWED_SHA256:-}"
if [ -z "$HF_UPLOADER_REVIEWED_SHA256" ] && [ -f "$HERE/reviewed_uploader.sha256" ]; then
  HF_UPLOADER_REVIEWED_SHA256="$(grep -oE '^[0-9a-f]{64}' "$HERE/reviewed_uploader.sha256" | head -n1 || true)"
fi
UPLOADER_IMAGE="${UPLOADER_IMAGE:-gradientsio/trainer-uploader:latest}"
HF_ENV_FILE="${HF_ENV_FILE:-/root/.hf_env}"
HF_HUB_API="${HF_HUB_API:-https://huggingface.co}"
UPLOAD_TIMEOUT_S="${UPLOAD_TIMEOUT_S:-1800}"
ALLOW_UNREVIEWED_UPLOADER="${ALLOW_UNREVIEWED_UPLOADER:-0}"
HF_MODE="${HF_MODE:-real}"
HF_MOCK_USER="${HF_MOCK_USER:-dry-user}"
HF_MOCK_PID=""; HF_UP_ENV=""; HF_UP_CONTAINER=""
HF_EFFECTIVE_ENV_FILE="$HF_ENV_FILE"
HF_FLOW_PY="${HF_FLOW_PY:-$HERE/hf_flow.py}"

hf_flow() { "$PY" "$HF_FLOW_PY" "$@"; }

# ----------------------------------------------------------------------------------------------- mock hub (DRY / tests)
hf_mock_start() {  # hf_mock_start DIR - loopback mock hub over DIR + a MOCK token file; sets HF_HUB_API, HF_EFFECTIVE_ENV_FILE
  local dir="$1" i
  HF_MOCK_DIR="$dir"; mkdir -p "$dir"
  "$PY" "$HERE/hfmock/mock_hub.py" token --dir "$dir" --name "$HF_MOCK_USER" --token-file "$dir/mock_hf_env" >/dev/null || return 1
  rm -f "$dir/port"
  "$PY" "$HERE/hfmock/mock_hub.py" serve --dir "$dir" --port-file "$dir/port" 2>>"$dir/mock_hub.log" &
  HF_MOCK_PID=$!
  for i in $(seq 50); do [ -s "$dir/port" ] && break; sleep 0.1; done
  [ -s "$dir/port" ] || { _log "mock hub did not start (see $dir/mock_hub.log)"; return 1; }
  HF_HUB_API="http://127.0.0.1:$(cat "$dir/port")"
  HF_EFFECTIVE_ENV_FILE="$dir/mock_hf_env"
  export HF_HUB_API
  _log "[hf] MOCK hub (no network): $HF_HUB_API over $dir, account $HF_MOCK_USER (a mock token, never a real one)"
}
hf_mock_stop() { if [ -n "$HF_MOCK_PID" ]; then kill "$HF_MOCK_PID" 2>/dev/null; wait "$HF_MOCK_PID" 2>/dev/null; HF_MOCK_PID=""; fi; return 0; }
hf_cleanup() {  # trap helper: never leave a token file, a running uploader or (HF_DELETE_AFTER_SCORE=1) a live repo behind
  [ -n "$HF_UP_CONTAINER" ] && [ "$HF_MODE" = real ] && { docker rm -f "$HF_UP_CONTAINER" >/dev/null 2>&1 || true; }
  [ -n "$HF_UP_ENV" ] && rm -f "$HF_UP_ENV"
  HF_UP_ENV=""; HF_UP_CONTAINER=""
  if [ -n "${HF_REPO_LIVE:-}" ] && [ "${HF_DELETE_AFTER_SCORE:-1}" = "1" ]; then
    local out why when
    out="$(hf_flow delete --env-file "$HF_EFFECTIVE_ENV_FILE" --api "$HF_HUB_API" --repo "$HF_REPO_LIVE" --tries 2 2>/dev/null)"
    why="$(printf '%s\n' "$out" | sed -n "s/^HF_DELETE_WHY=//p" | tr -d "'")"
    when="$(printf '%s\n' "$out" | sed -n "s/^HF_DELETED_UTC=//p" | tr -d "'")"
    _log "[hf] exiting with $HF_REPO_LIVE still public -> delete: ${why:-no answer}"
    [ -n "${HF_LEDGER:-}" ] && hf_flow ledger --tsv "$HF_LEDGER" event=exit_delete "repo=$HF_REPO_LIVE" "deleted_utc=$when" \
      "status=$([ -n "$when" ] && echo deleted || echo DELETE_FAILED)" "note=${why:-}" >/dev/null 2>&1
    HF_REPO_LIVE=""
  fi
  hf_mock_stop
}

# ----------------------------------------------------------------------------------------------- token + account
hf_whoami() {  # sets HF_USER HF_WRITE HF_WHY; rc 0 = a usable write(-capable) token. Prints the ACCOUNT NAME only.
  local out rc
  HF_USER=""; HF_WRITE=""; HF_WHY=""
  out="$(hf_flow whoami --env-file "$HF_EFFECTIVE_ENV_FILE" --api "$HF_HUB_API" 2>&1)"; rc=$?
  eval "$(printf '%s\n' "$out" | grep -E '^HF_(USER|WRITE|WHY|WHOAMI_HTTP)=')"
  return "$rc"
}
hf_make_upload_env() {  # a 0600 env-file with HUGGINGFACE_TOKEN + HUGGINGFACE_USERNAME (the token never reaches argv)
  local tok
  tok="$(grep -oE 'hf_[A-Za-z0-9]{20,}' "$HF_EFFECTIVE_ENV_FILE" 2>/dev/null | head -n1)"
  [ -n "$tok" ] || return 1
  HF_UP_ENV="$(mktemp "${TMPDIR:-/tmp}/hfup.XXXXXX")" || return 1
  chmod 600 "$HF_UP_ENV"
  printf 'HUGGINGFACE_TOKEN=%s\nHUGGINGFACE_USERNAME=%s\n' "$tok" "$HF_USER" > "$HF_UP_ENV"   # printf is a builtin
  tok=""
}

hf_stamp_ok() {  # the hf_preflight.sh stamp is real-mode, this account, this uploader image, younger than the max age
  HF_STAMP_WHY=""
  local f="${HF_PREFLIGHT_STAMP:-}" mode acct img utc age
  [ -n "$f" ] && [ -f "$f" ] || { HF_STAMP_WHY="no stamp ${f:-<unset>}"; return 1; }
  mode="$(sed -n 's/^mode=//p' "$f" | tail -n1)"; acct="$(sed -n 's/^account=//p' "$f" | tail -n1)"
  img="$(sed -n 's/^uploader_id=//p' "$f" | tail -n1)"; utc="$(sed -n 's/^utc=//p' "$f" | tail -n1)"
  [ "$mode" = "real" ] || { HF_STAMP_WHY="stamp mode=$mode (a DRY preflight proves nothing about the real hub)"; return 1; }
  [ "$acct" = "$HF_USER" ] || { HF_STAMP_WHY="stamp account=$acct, the token is $HF_USER"; return 1; }
  [ "$img" = "$HF_UPLOADER_ID" ] || { HF_STAMP_WHY="stamp uploader ${img:0:19}, the image is now ${HF_UPLOADER_ID:0:19}"; return 1; }
  age="$("$PY" -c 'import datetime,sys
t=datetime.datetime.strptime(sys.argv[1],"%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
print(int((datetime.datetime.now(datetime.timezone.utc)-t).total_seconds()//3600))' "$utc" 2>/dev/null)" \
    || { HF_STAMP_WHY="stamp utc '$utc' unreadable"; return 1; }
  [ "$age" -lt "${HF_PREFLIGHT_MAX_AGE_H:-24}" ] || { HF_STAMP_WHY="stamp is ${age} h old (max ${HF_PREFLIGHT_MAX_AGE_H:-24} h)"; return 1; }
  HF_STAMP_WHY="stamp $utc account=$acct (${age} h old)"
  return 0
}

# ----------------------------------------------------------------------------------------------- uploader image review
hf_uploader_review() {  # sets HF_UPLOADER_ID HF_UPLOADER_DIGEST HF_UPLOADER_SHA; rc 0 = the reviewed code (or allowed)
  HF_UPLOADER_ID=""; HF_UPLOADER_DIGEST=""; HF_UPLOADER_SHA=""
  if [ "$HF_MODE" = mock ]; then
    HF_UPLOADER_ID="vendored"; HF_UPLOADER_DIGEST="vendor/god (fetched copy)"
    HF_UPLOADER_SHA="$("$PY" -c 'import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$HERE/vendor/god/trainer/containers/uploader.py")"
  else
    HF_UPLOADER_ID="$(docker image inspect -f '{{.Id}}' "$UPLOADER_IMAGE" 2>/dev/null)" || {
      _log "[hf] uploader image $UPLOADER_IMAGE is not on this box - pull it first: docker pull $UPLOADER_IMAGE (hf_preflight.sh does)"; return 1; }
    HF_UPLOADER_DIGEST="$(docker image inspect -f '{{join .RepoDigests ","}}' "$UPLOADER_IMAGE" 2>/dev/null || true)"
    HF_UPLOADER_SHA="$(docker run --rm --network none --entrypoint python "$UPLOADER_IMAGE" -c \
      'import hashlib;print(hashlib.sha256(open("/app/trainer/containers/uploader.py","rb").read()).hexdigest())' 2>/dev/null | tail -n1)"
  fi
  if [ -n "$HF_UPLOADER_REVIEWED_SHA256" ] && [ "$HF_UPLOADER_SHA" = "$HF_UPLOADER_REVIEWED_SHA256" ]; then return 0; fi
  if [ "$ALLOW_UNREVIEWED_UPLOADER" = "1" ]; then
    _log "[hf] WARNING: $UPLOADER_IMAGE runs uploader.py sha256=${HF_UPLOADER_SHA:-?}, not the reviewed ${HF_UPLOADER_REVIEWED_SHA256:-<none recorded>} - ALLOW_UNREVIEWED_UPLOADER=1 accepts it (read the new code: what it uploads, where, public or not)"
    return 0
  fi
  _log "[hf] REFUSE: $UPLOADER_IMAGE runs uploader.py sha256=${HF_UPLOADER_SHA:-<unreadable>}, not the reviewed ${HF_UPLOADER_REVIEWED_SHA256:-<none recorded>}. Read trainer/containers/uploader.py (vendor/fetch_god_vendor.sh), then record its sha256 in reviewed_uploader.sha256 / HF_UPLOADER_REVIEWED_SHA256 - or ALLOW_UNREVIEWED_UPLOADER=1"
  return 1
}

# ----------------------------------------------------------------------------------------------- the upload itself
hf_run_uploader() {  # hf_run_uploader ROOT TASK_ID REPO_NAME MODEL LOG NAME_HINT
  # ROOT = "volume" (the trainer's named volume CHECKPOINTS_VOLUME, production) or an absolute host dir holding
  # <task>/<repo>/ (a scratch copy, bind-mounted at /app/checkpoints). Sets HF_UP_RC HF_UP_START HF_UP_END.
  local root="$1" task="$2" repo="$3" model="$4" log="$5" hint="$6" rc
  HF_UP_RC=""; HF_UP_START="$(date -u +%FT%TZ)"
  hf_make_upload_env || { HF_UP_RC=99; _log "[hf] no token in $HF_EFFECTIVE_ENV_FILE"; return 99; }
  if [ "$HF_MODE" = mock ]; then
    [ "$root" != volume ] || { rm -f "$HF_UP_ENV"; HF_UP_ENV=""; HF_UP_RC=98; _log "[hf] mock mode needs a host dir, not the docker volume"; return 98; }
    # the same two variables the container gets from --env-file, read line by line (never sourced, never on argv)
    ( cd "$root" || exit 97
      while IFS='=' read -r k v; do case "$k" in HUGGINGFACE_TOKEN|HUGGINGFACE_USERNAME) export "$k=$v" ;; esac; done < "$HF_UP_ENV"
      unset WANDB_TOKEN PYTHONPATH HF_TOKEN HUGGING_FACE_HUB_TOKEN
      export HOME="$root" FAKEHUB_DIR="$HF_MOCK_DIR" WANDB_LOGS_PATH="/app/checkpoints/wandb_logs/${task}_replica" \
        LOCAL_FOLDER="$root/$task/$repo" MODEL="$model" TASK_ID="$task" EXPECTED_REPO_NAME="$repo" HF_REPO_SUBFOLDER=checkpoints
      exec "$PY" "$HERE/hfmock/vendored.py" "$HERE/vendor/god/trainer/containers/uploader.py" ) > "$log" 2>&1
    rc=$?
  else
    local mount_root
    if [ "$root" = volume ]; then mount_root="${CHECKPOINTS_VOLUME:-checkpoints}"; else mount_root="$root"; fi
    HF_UP_CONTAINER="kitup-${hint}-$$"
    docker rm -f "$HF_UP_CONTAINER" >/dev/null 2>&1 || true
    timeout --signal=TERM --kill-after=30 "${UPLOAD_TIMEOUT_S}s" docker run --name "$HF_UP_CONTAINER" \
      --env-file "$HF_UP_ENV" \
      -e "WANDB_LOGS_PATH=/app/checkpoints/wandb_logs/${task}_replica" \
      -e "LOCAL_FOLDER=/app/checkpoints/${task}/${repo}" -e "MODEL=${model}" -e "TASK_ID=${task}" \
      -e "EXPECTED_REPO_NAME=${repo}" -e "HF_REPO_SUBFOLDER=checkpoints" \
      -v "${mount_root}:/app/checkpoints" -v "${CACHE_DIR:?}:/cache" \
      "$UPLOADER_IMAGE" > "$log" 2>&1
    rc=$?
    docker rm -f "$HF_UP_CONTAINER" >/dev/null 2>&1 || true      # runtime.py:862-870: removed whatever happened
    HF_UP_CONTAINER=""
  fi
  rm -f "$HF_UP_ENV"; HF_UP_ENV=""
  HF_UP_END="$(date -u +%FT%TZ)"; HF_UP_RC="$rc"
  return "$rc"
}
