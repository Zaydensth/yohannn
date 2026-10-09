#!/usr/bin/env bash
# cell_run.sh CELL - ONE validator-shaped attempt of one cell of the cells file, on the GPU box:
#
#   downloader (production image, fills /cache) -> TRAIN (your trainer image, run with the validator's CLI and container
#   shape) -> copy the output folder out (local sha256) -> UPLOAD (production uploader image: public repo) -> VERIFY
#   (public, select_lora, hub sha256 == local) -> SCORE through HF (score_latent.sh with the repo id: the evaluator's own
#   materialize_model downloads it) -> FETCHCHECK (the bytes the evaluator scored == the upload) -> DELETE the repo
#   (HF_DELETE_AFTER_SCORE=1) -> one row in RESULTS_TSV.
#
# What it copies from production (gradients-ai/G.O.D, file:line read at b49ba8644a0d - re-check at your pin):
#   downloader   trainer/runtime.py:425-506 run_downloader_container: image gradientsio/trainer-downloader:latest
#                (trainer/constants.py:25), --task-id --model --task-type ImageTask --dataset <url> --model-type, no
#                --anonymize (runtime.py:949,961: only when the request carries baseline_stats; this runner assumes
#                none for image tasks - INFERRED), volume cache:/cache rw. The archived training_data.zip is served
#                from a loopback HTTP server (the presigned URL is long dead) and the container uses --network host to
#                reach it - not production's default bridge.
#   train        runtime.py:199-296 run_trainer_container_image: CLI --task-id --model --dataset-zip --model-type
#                --expected-repo-name --hours-to-complete [--trigger-word]; env TRANSFORMERS_CACHE=HF_HOME=/cache/hf_cache
#                + HUGGINGFACE_TOKEN/USERNAME (:58-68); volumes checkpoints:/app/checkpoints rw, cache:/cache ro;
#                shm 8g, memory 110g, 24 CPUs (:79-90, trainer/constants.py:34-35), --gpus, no-new-privileges,
#                cap-drop ALL, network internal_bridge (no route out, :71-76). --dataset-zip is the task's own
#                training_data URL from the api json (what production passes; the trainer cannot reach it - it must
#                read /cache/datasets/<task>_tourn.zip, trainer/training_paths.py:42-43). That URL is never printed.
#   timeout      runtime.py:927 + 1066-1092: timeout = max(1, int(hours*3600)); at the timeout the container is KILLED
#                and the run still counts as success -> the upload runs (kill -> upload, not kill -> forfeit).
#   upload       runtime.py:790-871 via hf_lib.sh (production uploader image; it deletes the volume folder afterwards).
#   eval         score_latent.sh (see its header).
#
# Cell row (cells.py; CELLS_TSV): cell enabled tourn task_id trainer_image hours env note. The data lives at
# $KIT_ROOT/data/<tourn>/<task_id>/{test_data,training_data}.zip and api json at $KIT_ROOT/data/<tourn>/api/<task_id>.json.
#
#   bash cell_run.sh CELL                       one attempt (queue.sh calls this)
#   DRY=1 bash cell_run.sh CELL                 print every docker command (no docker, no GPU, no lock, no row) and run
#                                               the HF half for real against the loopback mock hub with the vendored
#                                               upstream uploader + materialize_model (needs vendor/fetch_god_vendor.sh)
#   RESCORE=1 RESCORE_FROM=<run dir> bash cell_run.sh CELL
#                                               no training: upload a SCRATCH copy of <run dir>/checkpoint under a NEW
#                                               repo name, verify, score, fetchcheck, delete (the OPS-RERUN of a cell
#                                               whose training finished but whose HF half or scoring failed)
#
# env [defaults]: KIT_ROOT [/root/kit] LATENT_ROOT [$KIT_ROOT/latent] CACHE_DIR [$KIT_ROOT/cache]
#   CELLS_TSV [$KIT_ROOT/plan/cells.tsv] RESULTS_TSV [$LATENT_ROOT/results_cells.tsv] RUNS_DIR [$KIT_ROOT/runs]
#   HF_LEDGER [$LATENT_ROOT/hf_flow.tsv] LOCK_FILE [$KIT_ROOT/locks/gpu.lock] LOCK_WAIT [0] SCORE_LOCK_WAIT [21600]
#   GPU_DEVICE [0] CHECKPOINTS_VOLUME [checkpoints] DOWNLOADER_IMAGE [gradientsio/trainer-downloader:latest]
#   DOWNLOAD [always|never] DL_TIMEOUT_S [3600] POLL_S [10] HF_DELETE_AFTER_SCORE [1] HF_PREFLIGHT_MAX_AGE_H [24]
#   ALLOW_UNLABELED_IMAGE [0] (the trainer image must carry the label sn56.repo_sha = the lock's pin_sha)
#   ATTEMPT [UTC timestamp] DRY [0] HF_DRY_LEDGER [<run dir>/dry/hf_flow.dry.tsv] RESCORE [0] RESCORE_FROM []
#   PY [python3]; everything score_latent.sh reads
#   (GOD_SHA is required there) and hf_lib.sh reads (HF_ENV_FILE, UPLOADER_IMAGE, HF_UPLOADER_REVIEWED_SHA256, ...).
#
# Exit: 0 OK | 1 REFUSED before the GPU (ops: retry) | 2 usage | 3 FAIL_TRAIN (the trainer failed: a result, not an
#   ops fault) | 4 FAIL_HF (upload/verify/fetch/delete: ops, rescore) | 5 FAIL_SCORE (evaluator on a verified upload:
#   ops, rescore) | 6 PRECONDITION (cannot succeed as configured; never retried) | 130/143 interrupted (no row).
set -uo pipefail
CELL="${1:-}"
[ "$#" -eq 1 ] && [ -n "$CELL" ] || { sed -n '2,/^# Exit:/p' "$0" >&2; exit 2; }
HERE="$(cd "$(dirname "$0")" && pwd)"
KIT_ROOT="${KIT_ROOT:-/root/kit}"
LATENT_ROOT="${LATENT_ROOT:-$KIT_ROOT/latent}"
CACHE_DIR="${CACHE_DIR:-$KIT_ROOT/cache}"
CELLS_TSV="${CELLS_TSV:-$KIT_ROOT/plan/cells.tsv}"
RESULTS_TSV="${RESULTS_TSV:-$LATENT_ROOT/results_cells.tsv}"
RUNS_DIR="${RUNS_DIR:-$KIT_ROOT/runs}"
HF_LEDGER="${HF_LEDGER:-$LATENT_ROOT/hf_flow.tsv}"
LOCK_FILE="${LOCK_FILE:-$KIT_ROOT/locks/gpu.lock}"
LOCK_WAIT="${LOCK_WAIT:-0}"
SCORE_LOCK_WAIT="${SCORE_LOCK_WAIT:-21600}"
GPU_DEVICE="${GPU_DEVICE:-0}"
CHECKPOINTS_VOLUME="${CHECKPOINTS_VOLUME:-checkpoints}"
DOWNLOADER_IMAGE="${DOWNLOADER_IMAGE:-gradientsio/trainer-downloader:latest}"
DOWNLOAD="${DOWNLOAD:-always}"
DL_TIMEOUT_S="${DL_TIMEOUT_S:-3600}"
POLL_S="${POLL_S:-10}"
HF_DELETE_AFTER_SCORE="${HF_DELETE_AFTER_SCORE:-1}"
ALLOW_UNLABELED_IMAGE="${ALLOW_UNLABELED_IMAGE:-0}"
ATTEMPT="${ATTEMPT:-$(date -u +%Y%m%dT%H%M%SZ)}"
DRY="${DRY:-0}"
RESCORE="${RESCORE:-0}"
RESCORE_FROM="${RESCORE_FROM:-}"
PY="${PY:-python3}"
export PYTHONDONTWRITEBYTECODE=1

_log() { printf '[cell %s %s] %s\n' "$CELL" "$(date -u +%H:%M:%S)" "$*" >&2; }
for v in DRY RESCORE HF_DELETE_AFTER_SCORE ALLOW_UNLABELED_IMAGE; do
  case "${!v}" in 0|1) ;; *) _log "usage: $v must be 0 or 1"; exit 2 ;; esac
done
case "$DOWNLOAD" in always|never) ;; *) _log "usage: DOWNLOAD must be always or never"; exit 2 ;; esac
for v in LOCK_WAIT SCORE_LOCK_WAIT DL_TIMEOUT_S POLL_S GPU_DEVICE; do
  [[ "${!v}" =~ ^[0-9]+$ ]] || { _log "usage: $v must be a non-negative integer"; exit 2; }
done
[[ "$ATTEMPT" =~ ^[A-Za-z0-9._-]+$ ]] || { _log "usage: ATTEMPT must match [A-Za-z0-9._-]+"; exit 2; }

# ----------------------------------------------------------------------------------------------- row + exit helpers
FAMILY=""; TASK_ID=""; HOURS_USED=""; TRAINER_IMAGE=""; IMAGE_ID=""; RUN_DIR=""; REPO=""; HF_REVISION=""
LOCAL_SHA=""; HUB_SHA=""; FETCH_SHA=""; FINAL=""; TEXT_MEAN=""; NO_TEXT_MEAN=""; N_IMAGES=""
TRAIN_RC=""; TIMED_OUT=""; TRAIN_WALL=""
row() {  # row STATUS EXIT_CODE NOTE
  if [ "$DRY" = "1" ]; then _log "DRY: no results row (status=$1 exit=$2)"; return 0; fi
  "$PY" "$HERE/cells.py" result --tsv "$RESULTS_TSV" "cell=$CELL" "attempt=$ATTEMPT" "task_id=$TASK_ID" \
    "family=$FAMILY" "status=$1" "exit_code=$2" "final=$FINAL" "text_mean=$TEXT_MEAN" "no_text_mean=$NO_TEXT_MEAN" \
    "n_images=$N_IMAGES" "train_rc=$TRAIN_RC" "timed_out=$TIMED_OUT" "train_wall_s=$TRAIN_WALL" "hours=$HOURS_USED" \
    "trainer_image=$TRAINER_IMAGE" "image_id=$IMAGE_ID" "hf_repo=$REPO" "hf_revision=$HF_REVISION" \
    "local_sha256=$LOCAL_SHA" "hub_sha256=$HUB_SHA" "fetch_sha256=$FETCH_SHA" "run_dir=$RUN_DIR" "note=${3:-}" \
    || _log "WARNING: could not append the results row to $RESULTS_TSV"
}
finish() { row "$1" "$2" "${3:-}"; _log "END status=$1 exit=$2${3:+ - $3}"; exit "$2"; }
refuse()      { finish "REFUSED($1)" 1 "$2"; }
fail_train()  { finish "FAIL_TRAIN($1)" 3 "$2"; }
fail_hf()     { finish "FAIL_HF($1)" 4 "$2"; }
fail_score()  { finish "FAIL_SCORE($1)" 5 "$2"; }
precond()     { finish "PRECONDITION($1)" 6 "$2"; }

# ----------------------------------------------------------------------------------------------- 0. the row + the task
[ -f "$CELLS_TSV" ] || { _log "no cells file $CELLS_TSV"; exit 2; }
ROW_OUT="$("$PY" "$HERE/cells.py" row --cells "$CELLS_TSV" --cell "$CELL" 2>&1)" || { _log "$ROW_OUT"; exit 2; }
eval "$ROW_OUT"
TASK_ID="$CELL_TASK_ID"; TRAINER_IMAGE="$CELL_TRAINER_IMAGE"
TASK_DIR="$KIT_ROOT/data/$CELL_TOURN/$TASK_ID"
API_JSON="$KIT_ROOT/data/$CELL_TOURN/api/$TASK_ID.json"
RUN_DIR="$RUNS_DIR/$CELL/$ATTEMPT"
[ -e "$RUN_DIR" ] && { _log "usage: $RUN_DIR already exists (ATTEMPT must be new)"; exit 2; }
[ "$CELL_ENABLED" = "1" ] || _log "NOTE: cell $CELL has enabled=0 (running it by hand anyway)"
[ -f "$API_JSON" ] || refuse "no-api-json" "$API_JSON missing (stage.sh)"
for z in test_data.zip training_data.zip; do [ -f "$TASK_DIR/$z" ] || refuse "no-data" "$TASK_DIR/$z missing (stage.sh)"; done
TASK_OUT="$("$PY" - "$API_JSON" "$CELL_HOURS" <<'PY'
import json, shlex, sys
d = json.load(open(sys.argv[1]))
hours = d.get("hours_to_complete")
want = sys.argv[2]
used = float(hours) if want == "task" else float(want)
def kv(k, v):
    print(f"{k}={shlex.quote('' if v is None else str(v))}")
kv("MODEL_ID", d.get("model_id"))
kv("FAMILY", d.get("model_type"))
kv("TASK_HOURS", hours)
kv("HOURS_USED", str(used))                       # str(float): the form runtime.py:229 passes
kv("TRAIN_TIMEOUT_S", max(1, int(used * 3600)))   # runtime.py:927
kv("TRIGGER_WORD", d.get("trigger_word") or "")
kv("TRAINING_URL", d.get("training_data") or "")
PY
)" || refuse "bad-api-json" "cannot read model_id/model_type/hours from $API_JSON"
eval "$TASK_OUT"
case "$FAMILY" in ideogram4|z-image|qwen-image|krea2|flux) ;; *) precond "family" "unsupported model_type '$FAMILY'" ;; esac
[ -n "$MODEL_ID" ] || refuse "bad-api-json" "no model_id in $API_JSON"
[ -n "$TRAINING_URL" ] || { TRAINING_URL="https://example.invalid/$TASK_ID/training_data.zip"; _log "NOTE: the api json carries no training_data URL - passing a placeholder as --dataset-zip"; }
[ "$CELL_HOURS" = "task" ] || _log "NOTE: hours=$CELL_HOURS from the cells file, not the task's $TASK_HOURS (not production)"
ENV_ARGS=(); for tok in $CELL_ENV; do ENV_ARGS+=(-e "$tok"); done
[ -z "$CELL_ENV" ] || _log "NOTE: arm env ($CELL_ENV) passed to the trainer - not production (the validator sets none)"
_log "task=$TASK_ID family=$FAMILY model=$MODEL_ID hours=$HOURS_USED (timeout ${TRAIN_TIMEOUT_S}s) image=$TRAINER_IMAGE rescore=$RESCORE dry=$DRY"

# ----------------------------------------------------------------------------------------------- 1. HF readiness (before the GPU)
if [ "$DRY" = "1" ]; then
  HF_MODE=mock
  [ -f "$HERE/vendor/god/trainer/containers/uploader.py" ] && [ -f "$HERE/vendor/god/validator/evaluation/image_artifacts.py" ] \
    || { _log "PRECONDITION: DRY runs the vendored upstream uploader/materialize_model - fetch them first: GOD_SHA=<sha> bash vendor/fetch_god_vendor.sh"; exit 6; }
fi
HF_PREFLIGHT_STAMP="${HF_PREFLIGHT_STAMP:-$KIT_ROOT/locks/hf_preflight.ok}"; export HF_PREFLIGHT_STAMP
# shellcheck source=hf_lib.sh
. "$HERE/hf_lib.sh"
HF_REPO_LIVE=""
on_exit() {
  [ -n "${DL_SERVER_PID:-}" ] && kill "$DL_SERVER_PID" 2>/dev/null
  [ -n "${TRAIN_CONTAINER:-}" ] && [ "$DRY" != "1" ] && docker rm -f "$TRAIN_CONTAINER" >/dev/null 2>&1
  [ -n "${TRAIN_ENV:-}" ] && rm -f "$TRAIN_ENV"
  hf_cleanup
}
trap on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
mkdir -p "$RUN_DIR/hf" || refuse "run-dir" "cannot create $RUN_DIR"
if [ "$DRY" = "1" ]; then
  hf_mock_start "$RUN_DIR/dry/mockhub" || refuse "mock-hub" "the loopback mock hub did not start"
  HF_LEDGER="${HF_DRY_LEDGER:-$RUN_DIR/dry/hf_flow.dry.tsv}"
  hf_whoami || refuse "mock-whoami" "${HF_WHY:-}"
  hf_uploader_review || refuse "uploader-review" "vendored uploader.py does not match the reviewed sha256 (reviewed_uploader.sha256)"
else
  eval "$(hf_flow tokencheck --env-file "$HF_ENV_FILE" 2>/dev/null)"
  [ "${HF_TOKEN_OK:-0}" = "1" ] || refuse "hf-token" "${HF_TOKEN_WHY:-token file check failed}"
  hf_whoami || refuse "hf-whoami" "${HF_WHY:-}"
  hf_uploader_review || refuse "uploader-review" "see the [hf] REFUSE line"
  if ! hf_stamp_ok; then
    _log "HF preflight stamp not usable ($HF_STAMP_WHY) - running hf_preflight.sh"
    KIT_ROOT="$KIT_ROOT" LATENT_ROOT="$LATENT_ROOT" HF_LEDGER="$HF_LEDGER" bash "$HERE/hf_preflight.sh" >&2 \
      || refuse "hf-preflight" "hf_preflight.sh failed (its last line says why)"
    hf_stamp_ok || refuse "hf-preflight" "$HF_STAMP_WHY"
  fi
fi
eval "$(hf_flow newname --task-id "$TASK_ID")"
REPO_NAME="$HF_REPO_NAME"; REPO="$HF_USER/$REPO_NAME"
[ -n "$REPO_NAME" ] || refuse "repo-name" "hf_flow.py newname failed"
_log "HF account=$HF_USER repo=$REPO (neutral name; deleted after the score: $HF_DELETE_AFTER_SCORE)"

# ----------------------------------------------------------------------------------------------- 2. trainer image (not in RESCORE)
PIN_SHA="$(sed -n '1s/.*pin_sha=\([0-9a-f]\{40\}\).*/\1/p' "$CELLS_TSV")"
if [ "$RESCORE" = "1" ]; then
  [ -n "$RESCORE_FROM" ] && [ -d "$RESCORE_FROM/checkpoint" ] || refuse "rescore" "RESCORE_FROM=$RESCORE_FROM has no checkpoint/ dir"
elif [ "$DRY" != "1" ]; then
  IMAGE_ID="$(docker image inspect -f '{{.Id}}' "$TRAINER_IMAGE" 2>/dev/null)" || precond "no-image" "trainer image $TRAINER_IMAGE is not on this box (build it from the pinned commit, README)"
  LABEL_SHA="$(docker image inspect -f '{{index .Config.Labels "sn56.repo_sha"}}' "$TRAINER_IMAGE" 2>/dev/null)"
  [ "$LABEL_SHA" = "<no value>" ] && LABEL_SHA=""
  if [ -n "$PIN_SHA" ] && [ "$LABEL_SHA" != "$PIN_SHA" ]; then
    if [ -z "$LABEL_SHA" ] && [ "$ALLOW_UNLABELED_IMAGE" = "1" ]; then
      _log "WARNING: $TRAINER_IMAGE has no sn56.repo_sha label; the lock pins $PIN_SHA (ALLOW_UNLABELED_IMAGE=1)"
    else
      precond "image-sha" "$TRAINER_IMAGE is labelled sn56.repo_sha='${LABEL_SHA:-<none>}', the lock pins $PIN_SHA"
    fi
  fi
fi

# ----------------------------------------------------------------------------------------------- 3. downloader (no GPU)
dryprint() {  # dryprint WHAT CMD... - the presigned training_data URL is never printed
  local what="$1" x out=(); shift
  for x in "$@"; do if [ "$x" = "$TRAINING_URL" ]; then out+=("<training_data URL from the api json>"); else out+=("$x"); fi; done
  { printf '[DRY %s] ' "$what"; printf '%q ' "${out[@]}"; printf '\n'; } | tee -a "$RUN_DIR/dry_commands.txt" >&2
}
DL_SERVER_PID=""
if [ "$RESCORE" != "1" ] && [ "$DOWNLOAD" = "always" ]; then
  DL_CMD=(docker run --rm --name "downloader-$TASK_ID-${ATTEMPT}" --network host -v "$CACHE_DIR:/cache"
          --env-file "<0600: HUGGINGFACE_TOKEN HUGGINGFACE_USERNAME>" "$DOWNLOADER_IMAGE"
          --task-id "$TASK_ID" --model "$MODEL_ID" --task-type ImageTask
          --dataset "http://127.0.0.1:<port>/training_data.zip" --model-type "$FAMILY")
  if [ "$DRY" = "1" ]; then
    dryprint downloader "${DL_CMD[@]}"
  else
    mkdir -p "$RUN_DIR/dl_serve" "$CACHE_DIR"
    ln "$TASK_DIR/training_data.zip" "$RUN_DIR/dl_serve/training_data.zip" 2>/dev/null || cp "$TASK_DIR/training_data.zip" "$RUN_DIR/dl_serve/training_data.zip"
    "$PY" - "$RUN_DIR/dl_serve" "$RUN_DIR/dl_serve.port" <<'PY' 2>"$RUN_DIR/dl_serve.log" &
import functools, http.server, os, sys
d, portfile = sys.argv[1], sys.argv[2]
handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=d)
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
with open(portfile + ".tmp", "w") as fh:
    fh.write(str(srv.server_address[1]))
os.replace(portfile + ".tmp", portfile)
srv.serve_forever()
PY
    DL_SERVER_PID=$!
    for _ in $(seq 50); do [ -s "$RUN_DIR/dl_serve.port" ] && break; sleep 0.1; done
    [ -s "$RUN_DIR/dl_serve.port" ] || refuse "dl-server" "the loopback HTTP server did not start ($RUN_DIR/dl_serve.log)"
    DL_PORT="$(cat "$RUN_DIR/dl_serve.port")"
    DL_ENV="$(mktemp "${TMPDIR:-/tmp}/dlenv.XXXXXX")"; chmod 600 "$DL_ENV"
    tok="$(grep -oE 'hf_[A-Za-z0-9]{20,}' "$HF_EFFECTIVE_ENV_FILE" | head -n1)"
    printf 'HUGGINGFACE_TOKEN=%s\nHUGGINGFACE_USERNAME=%s\n' "$tok" "$HF_USER" > "$DL_ENV"; tok=""
    for i in "${!DL_CMD[@]}"; do
      case "${DL_CMD[$i]}" in
        "<0600: HUGGINGFACE_TOKEN HUGGINGFACE_USERNAME>") DL_CMD[$i]="$DL_ENV" ;;
        "http://127.0.0.1:<port>/training_data.zip") DL_CMD[$i]="http://127.0.0.1:$DL_PORT/training_data.zip" ;;
      esac
    done
    _log "downloader: $DOWNLOADER_IMAGE (production) -> $CACHE_DIR, dataset via 127.0.0.1:$DL_PORT"
    exec 7>"$KIT_ROOT/locks/cache.lock"
    flock -w "$DL_TIMEOUT_S" 7 || { rm -f "$DL_ENV"; refuse "cache-lock" "another downloader holds $KIT_ROOT/locks/cache.lock"; }
    timeout --signal=TERM --kill-after=30 "$DL_TIMEOUT_S" "${DL_CMD[@]}" > "$RUN_DIR/downloader.log" 2>&1; rc=$?
    flock -u 7; exec 7>&-
    rm -f "$DL_ENV"
    docker rm -f "downloader-$TASK_ID-${ATTEMPT}" >/dev/null 2>&1
    kill "$DL_SERVER_PID" 2>/dev/null; wait "$DL_SERVER_PID" 2>/dev/null; DL_SERVER_PID=""
    [ "$rc" = "0" ] || { tail -n 15 "$RUN_DIR/downloader.log" >&2; refuse "downloader" "the production downloader exited $rc ($RUN_DIR/downloader.log)"; }
    cmp -s "$TASK_DIR/training_data.zip" "$CACHE_DIR/datasets/${TASK_ID}_tourn.zip" \
      || refuse "downloader" "$CACHE_DIR/datasets/${TASK_ID}_tourn.zip is not byte-identical to the archived training_data.zip"
  fi
elif [ "$RESCORE" != "1" ]; then
  _log "DOWNLOAD=never: trusting $CACHE_DIR as it is (production always runs the downloader)"
fi

# ----------------------------------------------------------------------------------------------- 4. train (GPU lock held)
TRAIN_CONTAINER=""; TRAIN_ENV=""
CKPT="$RUN_DIR/checkpoint"
if [ "$RESCORE" = "1" ]; then
  { mkdir -p "$CKPT" && cp -Rp "$RESCORE_FROM/checkpoint/." "$CKPT/"; } || refuse "rescore" "cannot copy $RESCORE_FROM/checkpoint"
  _log "RESCORE: the checkpoint of $RESCORE_FROM, re-uploaded as $REPO"
else
  CNAME="image-trainer-$("$PY" -c 'import uuid;print(uuid.uuid4().hex)')"
  TRAIN_CLI=(--task-id "$TASK_ID" --model "$MODEL_ID" --dataset-zip "$TRAINING_URL" --model-type "$FAMILY"
             --expected-repo-name "$REPO_NAME" --hours-to-complete "$HOURS_USED")
  [ -n "$TRIGGER_WORD" ] && TRAIN_CLI+=(--trigger-word "$TRIGGER_WORD")
  CREATE=(docker create --name "$CNAME" -v "$CHECKPOINTS_VOLUME:/app/checkpoints" -v "$CACHE_DIR:/cache:ro"
          --shm-size 8g --memory 110g --cpus 24 --security-opt no-new-privileges --cap-drop ALL
          --gpus "device=$GPU_DEVICE" --network internal_bridge
          -e TRANSFORMERS_CACHE=/cache/hf_cache -e HF_HOME=/cache/hf_cache --env-file "<0600: HUGGINGFACE_TOKEN HUGGINGFACE_USERNAME>"
          ${ENV_ARGS[@]+"${ENV_ARGS[@]}"} "$TRAINER_IMAGE" "${TRAIN_CLI[@]}")
  if [ "$DRY" = "1" ]; then
    dryprint train "${CREATE[@]}"
    _log "DRY: docker start $CNAME; poll every ${POLL_S}s; docker kill at ${TRAIN_TIMEOUT_S}s; docker cp $CNAME:/app/checkpoints/$TASK_ID/$REPO_NAME/. $CKPT/"
    mkdir -p "$RUN_DIR/dry/volume/$TASK_ID/$REPO_NAME" "$CKPT"
    eval "$(hf_flow mkdummy --out "$RUN_DIR/dry/volume/$TASK_ID/$REPO_NAME/last.safetensors")"
    cp -p "$RUN_DIR/dry/volume/$TASK_ID/$REPO_NAME/last.safetensors" "$CKPT/last.safetensors"
    TRAIN_RC=0; TIMED_OUT=0; TRAIN_WALL=0
  else
    mkdir -p "$(dirname "$LOCK_FILE")"
    exec 9>"$LOCK_FILE"
    if [ "$LOCK_WAIT" -gt 0 ]; then flock -w "$LOCK_WAIT" 9 || refuse "gpu-lock" "GPU lock busy for ${LOCK_WAIT}s: $LOCK_FILE"
    else flock -n 9 || refuse "gpu-lock" "GPU lock busy: $LOCK_FILE"; fi
    gpu_apps="$(nvidia-smi --query-compute-apps=pid,process_name --format=csv,noheader 2>&1)" || refuse "nvidia-smi" "$gpu_apps"
    [ -z "$gpu_apps" ] || refuse "gpu-busy" "a process already uses the GPU: $(printf '%s' "$gpu_apps" | tr '\n' ';')"
    TRAIN_ENV="$(mktemp "${TMPDIR:-/tmp}/trainenv.XXXXXX")"; chmod 600 "$TRAIN_ENV"
    tok="$(grep -oE 'hf_[A-Za-z0-9]{20,}' "$HF_EFFECTIVE_ENV_FILE" | head -n1)"
    printf 'HUGGINGFACE_TOKEN=%s\nHUGGINGFACE_USERNAME=%s\n' "$tok" "$HF_USER" > "$TRAIN_ENV"; tok=""
    for i in "${!CREATE[@]}"; do [ "${CREATE[$i]}" = "<0600: HUGGINGFACE_TOKEN HUGGINGFACE_USERNAME>" ] && CREATE[$i]="$TRAIN_ENV"; done
    "${CREATE[@]}" > "$RUN_DIR/create.log" 2>&1 || refuse "docker-create" "$(tail -n 2 "$RUN_DIR/create.log" | tr '\n' ' ')"
    TRAIN_CONTAINER="$CNAME"
    T0=$(date +%s)
    docker start "$CNAME" > /dev/null 2>"$RUN_DIR/start.log" || refuse "docker-start" "$(tail -n 2 "$RUN_DIR/start.log" | tr '\n' ' ')"
    rm -f "$TRAIN_ENV"; TRAIN_ENV=""            # the container has read it; never left on disk
    _log "training $CNAME (timeout ${TRAIN_TIMEOUT_S}s, log $RUN_DIR/train.log)"
    docker logs -f "$CNAME" > "$RUN_DIR/train.log" 2>&1 &
    LOGPID=$!
    TIMED_OUT=0
    while :; do
      [ "$(docker inspect -f '{{.State.Running}}' "$CNAME" 2>/dev/null)" = "true" ] || break
      if [ $(( $(date +%s) - T0 )) -ge "$TRAIN_TIMEOUT_S" ]; then
        _log "timeout ${TRAIN_TIMEOUT_S}s reached: docker kill (production kills, then uploads)"
        docker kill "$CNAME" >/dev/null 2>&1; TIMED_OUT=1; break
      fi
      sleep "$POLL_S"
    done
    docker wait "$CNAME" >/dev/null 2>&1
    TRAIN_WALL=$(( $(date +%s) - T0 ))
    TRAIN_RC="$(docker inspect -f '{{.State.ExitCode}}' "$CNAME" 2>/dev/null || echo unknown)"
    kill "$LOGPID" 2>/dev/null; wait "$LOGPID" 2>/dev/null
    _log "trainer exit=$TRAIN_RC timed_out=$TIMED_OUT wall=${TRAIN_WALL}s"
    mkdir -p "$CKPT"
    CP_OK=0
    docker cp "$CNAME:/app/checkpoints/$TASK_ID/$REPO_NAME/." "$CKPT/" > "$RUN_DIR/cp.log" 2>&1 && CP_OK=1
    docker rm -f "$CNAME" >/dev/null 2>&1; TRAIN_CONTAINER=""
    flock -u 9; exec 9>&-
    if [ "$TRAIN_RC" != "0" ] && [ "$TIMED_OUT" != "1" ]; then
      fail_train "rc=$TRAIN_RC" "the trainer failed; production uploads nothing (runtime.py:1070-1084). log: $RUN_DIR/train.log"
    fi
    [ "$CP_OK" = "1" ] || fail_train "no-output" "no /app/checkpoints/$TASK_ID/$REPO_NAME after the run - production's uploader would fail on it ($RUN_DIR/cp.log)"
  fi
fi
hf_flow localsha --dir "$CKPT" > "$RUN_DIR/hf/local_manifest.tsv" || fail_train "local-sha" "cannot hash $CKPT"
[ -s "$RUN_DIR/hf/local_manifest.tsv" ] || fail_train "empty-output" "$CKPT holds no file"

# ----------------------------------------------------------------------------------------------- 5. upload, verify
UPROOT="volume"
if [ "$RESCORE" = "1" ]; then                      # a SCRATCH copy: the uploader deletes what it uploads
  UPROOT="$RUN_DIR/hf/upload_root"; mkdir -p "$UPROOT/$TASK_ID/$REPO_NAME" && cp -Rp "$CKPT/." "$UPROOT/$TASK_ID/$REPO_NAME/"
elif [ "$DRY" = "1" ]; then
  UPROOT="$RUN_DIR/dry/volume"
fi
hf_flow ledger --tsv "$HF_LEDGER" event=uploading "cell=$CELL" "attempt=$ATTEMPT" "task_id=$TASK_ID" "label=$CELL" \
  "repo=$REPO" "source=$([ "$RESCORE" = 1 ] && echo rescore || echo train)" "note=mode=$HF_MODE" \
  || fail_hf "ledger" "cannot write the HF ledger $HF_LEDGER"
HF_REPO_LIVE="$REPO"
hf_run_uploader "$UPROOT" "$TASK_ID" "$REPO_NAME" "$MODEL_ID" "$RUN_DIR/hf/upload.log" "$CELL"
UP_RC=$?
if [ "$UP_RC" != "0" ]; then
  hf_flow ledger --tsv "$HF_LEDGER" event=upload_failed "cell=$CELL" "attempt=$ATTEMPT" "repo=$REPO" "status=UPLOAD_FAILED" \
    "note=rc=$UP_RC" >/dev/null 2>&1
  fail_hf "upload" "the uploader exited $UP_RC: $(tail -n 3 "$RUN_DIR/hf/upload.log" 2>/dev/null | tr '\n' ' ')"
fi
eval "$(hf_flow verify --env-file "$HF_EFFECTIVE_ENV_FILE" --api "$HF_HUB_API" --repo "$REPO" --local-dir "$CKPT" 2>/dev/null)"
HF_REVISION="${HF_REVISION:-}"; HUB_SHA="${HF_SELECTED_SHA256:-}"
LOCAL_SHA="$(awk -F'\t' -v s="$HUB_SHA" '$3 == s {print $3; exit}' "$RUN_DIR/hf/local_manifest.tsv")"
[ "${HF_VERIFY_OK:-0}" = "1" ] || fail_hf "verify" "${HF_VERIFY_WHY:-no answer}"
hf_flow ledger --tsv "$HF_LEDGER" event=uploaded "cell=$CELL" "attempt=$ATTEMPT" "repo=$REPO" "revision=$HF_REVISION" \
  "selected=$HF_SELECTED" "local_sha256=$LOCAL_SHA" "hub_sha256=$HUB_SHA" "upload_start_utc=$HF_UP_START" \
  "upload_end_utc=$HF_UP_END" "status=VERIFIED" >/dev/null 2>&1
_log "uploaded + verified: $REPO@${HF_REVISION:0:12} public, select_lora=$HF_SELECTED, sha256 ${HUB_SHA:0:16} == local"

# ----------------------------------------------------------------------------------------------- 6. score through HF + fetchcheck
LABEL="$(printf '%s' "$CELL" | tr -c 'A-Za-z0-9._-' '_')"
OUT_JSON="$LATENT_ROOT/cells/$CELL/$ATTEMPT.json"
PART_LOG="${OUT_JSON%.json}.parts/$LABEL.log"
if [ "$DRY" = "1" ]; then
  _log "DRY: PER_REPO=1 LOCK_WAIT=$SCORE_LOCK_WAIT bash score_latent.sh $TASK_DIR $FAMILY $MODEL_ID $OUT_JSON $LABEL=$REPO"
  PART_LOG="$RUN_DIR/dry/eval_fetch.log"
  FAKEHUB_DIR="$HF_MOCK_DIR" HF_HUB_CACHE="$RUN_DIR/dry/hf_cache" "$PY" "$HERE/hfmock/eval_fetch.py" --repo "$REPO" \
    --loras-dir "$RUN_DIR/dry/loras" > "$PART_LOG" 2>&1 || fail_score "dry-fetch" "eval_fetch.py failed ($PART_LOG)"
  HF_CACHE_DIR="$RUN_DIR/dry/hf_cache"
else
  mkdir -p "$(dirname "$OUT_JSON")"
  PER_REPO=1 LOCK_WAIT="$SCORE_LOCK_WAIT" KIT_ROOT="$KIT_ROOT" LATENT_ROOT="$LATENT_ROOT" HF_ENV_FILE="$HF_ENV_FILE" \
    bash "$HERE/score_latent.sh" "$TASK_DIR" "$FAMILY" "$MODEL_ID" "$OUT_JSON" "$LABEL=$REPO" > "$RUN_DIR/score.log" 2>&1
  SRC=$?
  [ "$SRC" = "6" ] && precond "score" "score_latent.sh: $(grep 'PRECONDITION' "$RUN_DIR/score.log" | tail -n1)"
  [ "$SRC" = "0" ] || fail_score "score_latent-rc=$SRC" "$(tail -n 2 "$RUN_DIR/score.log" | tr '\n' ' ')"
  SCORE_OUT="$("$PY" - "${TSV:-$LATENT_ROOT/results_latent.tsv}" "$OUT_JSON" "$LABEL" <<'PY'
import csv, shlex, sys
rows = [r for r in csv.DictReader(open(sys.argv[1]), delimiter="\t") if r["out_json"] == sys.argv[2] and r["label"] == sys.argv[3]]
r = rows[-1] if rows else {}
for k in ("status", "final", "text_mean", "no_text_mean", "n_images"):
    print(f"S_{k.upper()}={shlex.quote(r.get(k, ''))}")
PY
)"
  eval "$SCORE_OUT"
  FINAL="$S_FINAL"; TEXT_MEAN="$S_TEXT_MEAN"; NO_TEXT_MEAN="$S_NO_TEXT_MEAN"; N_IMAGES="$S_N_IMAGES"
  [ "$S_STATUS" = "OK" ] || fail_score "${S_STATUS:-no-row}" "score_latent.sh row for $LABEL is not OK"
  HF_CACHE_DIR="${HF_CACHE:-$LATENT_ROOT/hf_cache}"
fi
eval "$(hf_flow fetchcheck --repo "$REPO" --part-log "$PART_LOG" --hf-cache "$HF_CACHE_DIR" --expect-sha256 "$HUB_SHA" --expect-revision "$HF_REVISION" 2>/dev/null)"
FETCH_SHA="${HF_FETCH_SHA256:-}"
[ "${HF_FETCH_OK:-0}" = "1" ] || fail_hf "fetch" "${HF_FETCH_WHY:-no answer}"
_log "scored through HF: final=${FINAL:-dry} - the evaluator fetched sha256 ${FETCH_SHA:0:16} == the upload"

# ----------------------------------------------------------------------------------------------- 7. delete (or keep) + row
if [ "$HF_DELETE_AFTER_SCORE" = "1" ]; then
  eval "$(hf_flow delete --env-file "$HF_EFFECTIVE_ENV_FILE" --api "$HF_HUB_API" --repo "$REPO" 2>/dev/null)"
  hf_flow ledger --tsv "$HF_LEDGER" event="$([ "${HF_DELETED:-0}" = 1 ] && echo deleted || echo delete_failed)" "cell=$CELL" \
    "attempt=$ATTEMPT" "repo=$REPO" "deleted_utc=${HF_DELETED_UTC:-}" "fetch_sha256=$FETCH_SHA" \
    "score_final_hf=$FINAL" "status=$([ "${HF_DELETED:-0}" = 1 ] && echo deleted || echo DELETE_FAILED)" "note=${HF_DELETE_WHY:-}" >/dev/null 2>&1
  [ "${HF_DELETED:-0}" = "1" ] || fail_hf "delete" "the repo $REPO is still public: ${HF_DELETE_WHY:-} (hf_flow.py sweep)"
  HF_REPO_LIVE=""
else
  hf_flow ledger --tsv "$HF_LEDGER" event=kept "cell=$CELL" "attempt=$ATTEMPT" "repo=$REPO" "status=KEPT" \
    "note=HF_DELETE_AFTER_SCORE=0" >/dev/null 2>&1
  HF_REPO_LIVE=""
  _log "repo $REPO left PUBLIC on purpose (HF_DELETE_AFTER_SCORE=0)"
fi
if [ "$DRY" = "1" ]; then
  _log "DRY HF flow $REPO: sha256 local == hub == fetched: $([ -n "$FETCH_SHA" ] && [ "$FETCH_SHA" = "$HUB_SHA" ] && echo yes || echo NO)"
fi
finish "OK" 0 "$([ "$TIMED_OUT" = 1 ] && echo 'trainer hit the timeout (killed, uploaded: production semantics)')"
