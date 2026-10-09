#!/usr/bin/env bash
# score_latent.sh TASK_DIR MODEL_TYPE BASE_REPO OUT_JSON MODEL...
#
# Score LoRAs on this box with the public G.O.D image evaluator (the image build_eval_image.sh builds at a commit YOU
# reviewed, GOD_SHA), through the run_latent_eval.py shim. Every file:line below was read at gradients-ai/G.O.D main
# b49ba8644a0d (2026-10-09): re-check them at the commit you pin, upstream moves often.
#
#   TASK_DIR    dir holding the task's held-out test_data.zip (dir basename = task id)
#   MODEL_TYPE  ideogram4 | z-image | qwen-image | krea2 | flux      (FAMILIES, validator/evaluation/image_flow_adapter.py:23-51)
#   BASE_REPO   the task's model_id (api/<task>.json). Production passes it as ORIGINAL_MODEL_REPO
#               (validator/evaluation/docker_evaluation.py:388). ideogram4 ignores it (image_artifacts.py:83-85 always
#               loads Comfy-Org/Ideogram-4); every other family loads the ONE root-level .safetensors > 5 GiB of it
#               (image_artifacts.py:86-95). That rule is checked HERE first, on the repo's recorded file list
#               (latent_tool.py rootckpt, MANIFEST_DIR): any other count makes prepare_base raise ValueError inside the
#               container (a "setup failed" that would read as a harness death), so it is a PRECONDITION, exit 6,
#               before the GPU lock. A GATED base (HF API field `gated` is not false) also needs a token in
#               HF_ENV_FILE whose account accepted the repo's terms - else exit 6 too.
#   OUT_JSON    absolute path under $LATENT_ROOT ending in .json; must not exist (results are never overwritten)
#   MODEL       /abs/path.safetensors | LABEL=/abs/path.safetensors   local artifact, staged read-only and
#                                                                     evaluated as repo "local/<LABEL>"
#               owner/repo | LABEL=owner/repo                         real HF repo, resolved by the REAL
#                                                                     materialize_model + select_lora
#
# Mirrors production (docker_evaluation.py:361-428 run_evaluation_basilica_image):
#   one container per repo (MODELS=<one repo>, :397-401); env ORIGINAL_MODEL_REPO, MODEL_TYPE,
#   TRANSFORMERS_ALLOW_TORCH_LOAD=true, PYTHONUNBUFFERED=1 + HF_CONTAINER_ENV_IMAGE (validator/evaluation/constants.py:
#   126-171: HF cache paths, HF_HUB_ENABLE_HF_TRANSFER=0, HF_TOKEN/HUGGING_FACE_HUB_TOKEN/HUGGINGFACE_TOKEN only when a
#   token exists); container size cpu=4 memory=64Gi (basilica.py:697-698, constants.py:59-60); the test split read as a
#   ZIP (read_dataset, image_test_data.py:70-98).
# Deliberately NOT production (each recorded in the TSV):
#   * the GPU: production image eval requests A100 (constants.py:39 BASILICA_GPU_MODELS; runpod.py:161). On another
#     GPU, FP8_COMPUTE=off (default) makes ComfyUI take the A100 fp8 decision (see run_latent_eval.py docstring),
#     proven per row (fp8_ops column). Other kernel numerics still differ between GPU models - measure your own
#     replica-vs-official offset (anchors.sh) before you trust a small gap.
#   * the token: production hands the evaluator a READ-ONLY token (constants.py:139-155); this script passes the
#     token in HF_ENV_FILE as-is. For public repos the result is the same.
#
# Env knobs (defaults in brackets):
#   GOD_SHA [required]      40-hex G.O.D commit the evaluator image was built from -> EVAL_IMAGE [god-image-eval:main-<sha7>],
#                           EXPECT_GOD_SHA [GOD_SHA]: the image label god.sha must equal it
#   PER_REPO [1]            1 = one container per model (production); 0 = all models in one container
#   FP8_COMPUTE [off]       off | native   -> LATENT_FP8_COMPUTE in the shim
#   IMAGE_EVAL_STRATA [16] IMAGE_EVAL_NOISES [16] IMAGE_EVAL_BATCH_SIZE [2]   the production defaults
#                           (evaluators/diffusion.py:41-42,209-211); always passed, recorded in the TSV
#   EVAL_TIMEOUT [auto]     seconds per CONTAINER (= per group of models run together). auto =
#                           1800 + 600 x images x models_in_that_container - deliberately generous; tighten it once
#                           you have measured your own box
#   EVAL_CPUS [4] EVAL_MEM [64g]   container limits (as production); 0 = no limit
#   LOCK_WAIT [0] s to wait for the GPU flock (0 = refuse if busy)
#   HF_ENV_FILE [/root/.hf_env]    token is parsed (regex hf_...), never sourced, never printed; passed via a 0600
#                           --env-file. Required for a gated base; optional otherwise.
#   GPU_DEVICE [0]          docker --gpus device=<n>
#   KIT_ROOT [/root/kit] LATENT_ROOT [$KIT_ROOT/latent] TSV [$LATENT_ROOT/results_latent.tsv]
#   LOCK_FILE [$KIT_ROOT/locks/gpu.lock] HF_CACHE [$LATENT_ROOT/hf_cache]
#   MANIFEST_DIR [$KIT_ROOT/cache_manifests] HF_API_BASE [https://huggingface.co]   base file lists (rootckpt)
#   BUSY_RE                 regex over `docker ps` "<name> <image>" lines that makes this script refuse (another
#                           trainer / downloader / uploader / evaluator running); default matches the container names
#                           cell_run.sh and hf_lib.sh use and the production/evaluator images
#   DRY [0]                 1 = validate + stage + print docker commands under $LATENT_ROOT/dry/, run nothing, no lock
#
# Outputs (STEM = OUT_JSON without .json):
#   OUT_JSON             merged evaluator JSON (exact evaluator shape: model_params_count + one key per repo)
#   STEM.log             full container log(s), one header per container
#   STEM.parts/          raw per-container JSON + per-container log
#   STEM.lora_in/        staged local artifacts (hardlinks; copy fallback)   STEM.manifest.tsv (sha256 each)
#   STEM.runs.tsv        container, MODELS, log, exit code, wall seconds     STEM.meta.json (summary)
#   TSV                  one row per model; failures are rows with status FAILED(...), never dropped
# Exit codes: 0 scored (every model has a row, OK or FAILED(...)); 1 refused (lock, image, cache dir, arguments,
#   a running trainer/evaluator...); 2 usage; 6 a PRECONDITION is not met: a base prepare_base cannot resolve (not
#   exactly one root checkpoint > 5 GiB) or a gated base without a token - a retry cannot fix either, so queue.sh
#   never counts rc 6 as a box-level fault.
set -euo pipefail

usage() { sed -n '2,/^# Exit codes/p' "$0" >&2; exit 2; }
[ "$#" -ge 5 ] || usage
TASK_DIR="${1%/}"; MODEL_TYPE="$2"; BASE_REPO="$3"; OUT_JSON="$4"; shift 4
MODEL_ARGS=("$@")

HERE="$(cd "$(dirname "$0")" && pwd)"
KIT_ROOT="${KIT_ROOT:-/root/kit}"
LATENT_ROOT="${LATENT_ROOT:-$KIT_ROOT/latent}"
GOD_SHA="${GOD_SHA:-}"
EVAL_IMAGE="${EVAL_IMAGE:-god-image-eval:main-${GOD_SHA:0:7}}"
EXPECT_GOD_SHA="${EXPECT_GOD_SHA:-$GOD_SHA}"
MANIFEST_DIR="${MANIFEST_DIR:-$KIT_ROOT/cache_manifests}"
HF_API_BASE="${HF_API_BASE:-https://huggingface.co}"
TSV="${TSV:-$LATENT_ROOT/results_latent.tsv}"
LOCK_FILE="${LOCK_FILE:-$KIT_ROOT/locks/gpu.lock}"
LOCK_WAIT="${LOCK_WAIT:-0}"
HF_CACHE="${HF_CACHE:-$LATENT_ROOT/hf_cache}"
HF_ENV_FILE="${HF_ENV_FILE:-/root/.hf_env}"
SHIM="${SHIM:-$HERE/run_latent_eval.py}"
TOOL="${TOOL:-$HERE/latent_tool.py}"
PER_REPO="${PER_REPO:-1}"
FP8_COMPUTE="${FP8_COMPUTE:-off}"
EVAL_TIMEOUT="${EVAL_TIMEOUT:-auto}"
EVAL_CPUS="${EVAL_CPUS:-4}"
EVAL_MEM="${EVAL_MEM:-64g}"
GPU_DEVICE="${GPU_DEVICE:-0}"
STRATA="${IMAGE_EVAL_STRATA:-16}"
NOISES="${IMAGE_EVAL_NOISES:-16}"
BATCH="${IMAGE_EVAL_BATCH_SIZE:-2}"
BUSY_RE="${BUSY_RE:-(^| )(image-trainer-|downloader-|kitup-|latent-)|god-image-eval:|gradientsio/}"
DRY="${DRY:-0}"

_log() { printf '[score_latent %s] %s\n' "$(date -u +%FT%TZ)" "$*" >&2; }
die() { _log "REFUSE: $*"; exit 1; }
precond() { _log "PRECONDITION (exit 6, gated base): $*"; exit 6; }
precond_base() { _log "PRECONDITION (exit 6, base resolution): $*"; exit 6; }

# ---------------------------------------------------------------------------------------------------------
# 0. argument validation (no side effects yet)
# ---------------------------------------------------------------------------------------------------------
[[ "$GOD_SHA" =~ ^[0-9a-f]{40}$ ]] || die "GOD_SHA must be the full 40-hex G.O.D commit your evaluator image was built from (build_eval_image.sh)"
case "$PER_REPO" in 0|1) ;; *) die "PER_REPO must be 0 or 1" ;; esac
case "$FP8_COMPUTE" in native|off) ;; *) die "FP8_COMPUTE must be native or off" ;; esac
case "$DRY" in 0|1) ;; *) die "DRY must be 0 or 1" ;; esac
for v in STRATA NOISES BATCH; do
  [[ "${!v}" =~ ^[1-9][0-9]*$ ]] || die "$v must be a positive integer, got '${!v}'"
done
[[ "$EVAL_TIMEOUT" == auto || "$EVAL_TIMEOUT" =~ ^[1-9][0-9]*$ ]] || die "EVAL_TIMEOUT must be auto or seconds"
[[ "$LOCK_WAIT" =~ ^[0-9]+$ ]] || die "LOCK_WAIT must be a non-negative integer"
[[ "$EVAL_CPUS" =~ ^[0-9]+([.][0-9]+)?$ ]] || die "EVAL_CPUS must be a number (0 = no limit)"
[[ "$EVAL_MEM" =~ ^(0|[1-9][0-9]*[kmg])$ ]] || die "EVAL_MEM must look like 64g (0 = no limit)"
[[ "$GPU_DEVICE" =~ ^[0-9]+$ ]] || die "GPU_DEVICE must be a GPU index"
case "$MODEL_TYPE" in
  ideogram4|z-image|qwen-image|krea2|flux) ;;
  *) die "unsupported MODEL_TYPE '$MODEL_TYPE' (FAMILIES in image_flow_adapter.py:23-51; sdxl is refused upstream)" ;;
esac
[ -n "$BASE_REPO" ] || die "BASE_REPO must be non-empty"
case "$OUT_JSON" in /*.json) ;; *) die "OUT_JSON must be an absolute path ending in .json: $OUT_JSON" ;; esac
[ -f "$TOOL" ] || die "helper missing: $TOOL"
[ -f "$SHIM" ] || die "shim missing: $SHIM"
case "$(python3 "$TOOL" realpath "$OUT_JSON")" in "$(python3 "$TOOL" realpath "$LATENT_ROOT")"/*) ;;
  *) die "OUT_JSON must live under $LATENT_ROOT: $OUT_JSON" ;; esac
TASK_ZIP="$TASK_DIR/test_data.zip"
[ -f "$TASK_ZIP" ] || die "no test_data.zip in $TASK_DIR"
unzip -tq "$TASK_ZIP" >/dev/null 2>&1 || die "test_data.zip fails unzip -t: $TASK_ZIP"
EXPECTED_IMAGES="$(python3 "$TOOL" zipinfo "$TASK_ZIP")" || die "test_data.zip is not evaluable: $TASK_ZIP"
TASK_ID="$(basename "$TASK_DIR")"
[[ "$TASK_ID" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] \
  || _log "WARNING: TASK_DIR basename '$TASK_ID' is not a task UUID"
[ -d "$HF_CACHE" ] || die "HF cache dir missing: $HF_CACHE (provision.sh creates it)"

# HF token: parse-only (never `source`), never printed. Production passes HF_READ_ONLY_TOKEN the same three ways.
HF_TOKEN_VALUE=""
if [ -f "$HF_ENV_FILE" ]; then
  HF_TOKEN_VALUE="$(grep -oE 'hf_[A-Za-z0-9]{20,}' "$HF_ENV_FILE" | head -n1 || true)"
fi
# the evaluator's base choice (image_artifacts.py:80-98) and the repo's gate, from its recorded file list - before any
# lock or GPU. ideogram4 never reads BASE_REPO for the diffusion model (image_artifacts.py:83-85).
ROOT_N=""; ROOT_FILE=""; GATED=""
if [ "$MODEL_TYPE" != "ideogram4" ]; then
  RC_ROOT=0
  ROOT_OUT="$(python3 "$TOOL" rootckpt --repo "$BASE_REPO" --family "$MODEL_TYPE" --manifest-dir "$MANIFEST_DIR" --api-base "$HF_API_BASE" 2>&1)" || RC_ROOT=$?
  eval "$(printf '%s\n' "$ROOT_OUT" | grep -E '^ROOT_N=' | tail -n1)" || true
  case "$RC_ROOT" in
    0) _log "base: $(printf '%s\n' "$ROOT_OUT" | tail -n1)" ;;
    1) precond_base "base $BASE_REPO cannot be resolved by the evaluator: $(printf '%s\n' "$ROOT_OUT" | tail -n1)" ;;
    *) _log "WARNING: base layout of $BASE_REPO unverified ($(printf '%s\n' "$ROOT_OUT" | tail -n1)) - the evaluator decides" ;;
  esac
  case "${GATED:-}" in
    ""|false|False|unknown|not-checked) ;;
    *) [ -n "$HF_TOKEN_VALUE" ] || precond "base $BASE_REPO is gated (gated=$GATED) and $HF_ENV_FILE holds no token: accept the repo's terms on huggingface.co with the account whose token you put there"
       _log "base $BASE_REPO is gated (gated=$GATED): the token in $HF_ENV_FILE must belong to an account that accepted its terms" ;;
  esac
fi

STEM="${OUT_JSON%.json}"
if [ "$DRY" = "1" ]; then   # a dry run never writes next to the real OUT_JSON nor into the real TSV
  DRYDIR="$LATENT_ROOT/dry/$(date -u +%Y%m%dT%H%M%SZ)_$$"
  STEM="$DRYDIR/$(basename "$STEM")"
  OUT_JSON="$STEM.json"
  TSV="$DRYDIR/results_latent.dry.tsv"
  _log "DRY: all outputs under $DRYDIR"
fi
LORA_IN="$STEM.lora_in"; PARTS="$STEM.parts"; MANIFEST="$STEM.manifest.tsv"; RUNS_FILE="$STEM.runs.tsv"
COMBINED_LOG="$STEM.log"
for p in "$OUT_JSON" "$LORA_IN" "$PARTS" "$MANIFEST" "$RUNS_FILE" "$COMBINED_LOG"; do
  [ ! -e "$p" ] || die "$p already exists; pick a new OUT_JSON (results are never overwritten)"
done

# MODEL args -> parallel arrays: label, kind (local|hf), repo id handed to the evaluator, origin
LABELS=(); KINDS=(); REPOS=(); ORIGINS=()
sanitize() {  # keep the TAIL when too long: board repos differ only in their trailing hotkey
  local s; s="$(printf '%s' "$1" | tr -c 'A-Za-z0-9._-' '_')"
  if [ "${#s}" -gt 100 ]; then s="${s: -100}"; fi
  printf '%s' "$s"
}
for item in "${MODEL_ARGS[@]}"; do
  label=""; spec="$item"
  if [[ "$item" =~ ^([A-Za-z0-9][A-Za-z0-9._-]*)=(.+)$ ]]; then label="${BASH_REMATCH[1]}"; spec="${BASH_REMATCH[2]}"; fi
  if [[ "$spec" == /* ]]; then
    [ -f "$spec" ] && [ -s "$spec" ] || die "local artifact missing/empty: $spec"
    case "$spec" in *.safetensors) ;; *) die "local artifact is not .safetensors: $spec" ;; esac
    python3 "$TOOL" stcheck "$spec" >/dev/null || die "not a valid safetensors file: $spec"
    if [ -z "$label" ]; then
      run="$(printf '%s' "$spec" | sed -n 's#^.*/runs/\([^/]*\)/.*$#\1#p')"
      [ -n "$run" ] || run="$(basename "$(dirname "$spec")")"
      stem="$(basename "$spec" .safetensors)"
      if [ "$stem" = "last" ]; then label="$run"; else label="${run}__${stem}"; fi
    fi
    label="$(sanitize "$label")"
    [[ "$label" =~ ^[A-Za-z0-9] ]] || label="L$label"
    LABELS+=("$label"); KINDS+=("local"); REPOS+=("local/$label"); ORIGINS+=("$spec")
  else
    [[ "$spec" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9._-]+$ ]] || die "neither /abs/path nor owner/repo: $item"
    [ -n "$label" ] || label="hf__${spec##*/}"
    label="$(sanitize "$label")"
    LABELS+=("$label"); KINDS+=("hf"); REPOS+=("$spec"); ORIGINS+=("$spec")
  fi
done
[ "${#REPOS[@]}" -ge 1 ] || die "no MODEL given"
dups="$(printf '%s\n' "${LABELS[@]}" | sort | uniq -d)"; [ -z "$dups" ] || die "duplicate labels: $dups"
dups="$(printf '%s\n' "${REPOS[@]}" | sort | uniq -d)"; [ -z "$dups" ] || die "duplicate repos: $dups"

# ---------------------------------------------------------------------------------------------------------
# 1. image identity + the weight the image will score with (code is authority: read it from the image)
# ---------------------------------------------------------------------------------------------------------
IMAGE_ID="$(docker image inspect -f '{{.Id}}' "$EVAL_IMAGE" 2>/dev/null)" || die "image $EVAL_IMAGE not found (build_eval_image.sh)"
IMG_SHA="$(docker image inspect -f '{{index .Config.Labels "god.sha"}}' "$EVAL_IMAGE")"
[ "$IMG_SHA" = "$EXPECT_GOD_SHA" ] || die "image label god.sha='$IMG_SHA' != EXPECT_GOD_SHA=$EXPECT_GOD_SHA"
WEIGHT="$(docker run --rm --network none --entrypoint python "$EVAL_IMAGE" -c \
  "import re;print(re.search(r'^DIFFUSION_TEXT_GUIDED_EVAL_WEIGHT\s*=\s*([0-9.]+)\s*$',open('/app/validator/evaluation/constants.py').read(),re.M).group(1))")" \
  || die "cannot read DIFFUSION_TEXT_GUIDED_EVAL_WEIGHT from the image"

# ---------------------------------------------------------------------------------------------------------
# 2. single-flight: GPU flock first, then no trainer/evaluator container and no GPU process
# ---------------------------------------------------------------------------------------------------------
if [ "$DRY" != "1" ]; then
  mkdir -p "$(dirname "$LOCK_FILE")"
  exec 9>"$LOCK_FILE"
  if [ "$LOCK_WAIT" -gt 0 ]; then
    _log "waiting up to ${LOCK_WAIT}s for $LOCK_FILE"
    flock -w "$LOCK_WAIT" 9 || die "GPU lock still busy after ${LOCK_WAIT}s: $LOCK_FILE"
  else
    flock -n 9 || die "GPU lock busy: $LOCK_FILE"
  fi
  running="$(docker ps --format '{{.Names}} {{.Image}}' | grep -E "$BUSY_RE" || true)"
  [ -z "$running" ] || die "another trainer/evaluator container is running: $(printf '%s' "$running" | tr '\n' ';')"
  gpu_apps="$(nvidia-smi --query-compute-apps=pid,process_name --format=csv,noheader 2>&1)" || die "nvidia-smi failed: $gpu_apps"
  [ -z "$gpu_apps" ] || die "a process is already using the GPU: $(printf '%s' "$gpu_apps" | tr '\n' ';')"
fi
_log "image=$EVAL_IMAGE id=$IMAGE_ID god.sha=$IMG_SHA weight=$WEIGHT task=$TASK_ID images=$EXPECTED_IMAGES"
_log "type=$MODEL_TYPE base=$BASE_REPO models=${#REPOS[@]} per_repo=$PER_REPO fp8_compute=$FP8_COMPUTE strata=$STRATA noises=$NOISES batch=$BATCH timeout=$EVAL_TIMEOUT cpus=$EVAL_CPUS mem=$EVAL_MEM hf_token=$([ -n "$HF_TOKEN_VALUE" ] && echo yes || echo no)${GATED:+ base_gated=$GATED}"

# ---------------------------------------------------------------------------------------------------------
# 3. stage local artifacts (hardlink, copy fallback; mounted read-only) + sha256 manifest
# ---------------------------------------------------------------------------------------------------------
mkdir -p "$(dirname "$OUT_JSON")" "$LORA_IN" "$PARTS"
printf 'label\tkind\tevaluator_repo\torigin\tstaged\tbytes\tsha256\n' > "$MANIFEST"
for i in "${!REPOS[@]}"; do
  if [ "${KINDS[$i]}" = "local" ]; then
    dst="$LORA_IN/${LABELS[$i]}.safetensors"
    read -r src_ino _ _ <<< "$(python3 "$TOOL" fileinfo "${ORIGINS[$i]}")"
    if ln "${ORIGINS[$i]}" "$dst" 2>/dev/null; then
      read -r dst_ino bytes sha <<< "$(python3 "$TOOL" fileinfo "$dst")"
      [ "$dst_ino" = "$src_ino" ] || die "hardlink inode mismatch: $dst"
    else
      cp -- "${ORIGINS[$i]}" "$dst"
      cmp -s -- "${ORIGINS[$i]}" "$dst" || die "staged copy differs from source: $dst"
      read -r _ bytes sha <<< "$(python3 "$TOOL" fileinfo "$dst")"
    fi
    printf '%s\tlocal\t%s\t%s\t%s\t%s\t%s\n' "${LABELS[$i]}" "${REPOS[$i]}" "${ORIGINS[$i]}" "$dst" \
      "$bytes" "$sha" >> "$MANIFEST"
  else
    printf '%s\thf\t%s\t%s\t-\t-\t-\n' "${LABELS[$i]}" "${REPOS[$i]}" "${ORIGINS[$i]}" >> "$MANIFEST"
  fi
done

# ---------------------------------------------------------------------------------------------------------
# 4. run the evaluator container(s); a signal to this script kills the running container
# ---------------------------------------------------------------------------------------------------------
CURRENT_CONTAINER=""
ENV_FILE=""
on_exit() {
  if [ -n "$CURRENT_CONTAINER" ]; then docker kill "$CURRENT_CONTAINER" >/dev/null 2>&1 || true; fi
  if [ -n "$ENV_FILE" ]; then rm -f "$ENV_FILE"; fi
}
trap on_exit EXIT
trap 'on_exit; exit 130' INT
trap 'on_exit; exit 143' TERM
trap 'on_exit; exit 129' HUP

TOKEN_ARGS=()
if [ -n "$HF_TOKEN_VALUE" ]; then
  if [ "$DRY" = "1" ]; then
    TOKEN_ARGS=(--env-file "<0600-tempfile:HF_TOKEN,HUGGING_FACE_HUB_TOKEN,HUGGINGFACE_TOKEN>")
  else
    ENV_FILE="$(mktemp)"; chmod 600 "$ENV_FILE"
    printf 'HF_TOKEN=%s\nHUGGING_FACE_HUB_TOKEN=%s\nHUGGINGFACE_TOKEN=%s\n' \
      "$HF_TOKEN_VALUE" "$HF_TOKEN_VALUE" "$HF_TOKEN_VALUE" > "$ENV_FILE"
    TOKEN_ARGS=(--env-file "$ENV_FILE")
  fi
fi
LIMIT_ARGS=()
if [ "$EVAL_CPUS" != "0" ]; then LIMIT_ARGS+=(--cpus "$EVAL_CPUS"); fi
if [ "$EVAL_MEM" != "0" ]; then LIMIT_ARGS+=(--memory "$EVAL_MEM"); fi

: > "$RUNS_FILE"
RUN_TAG="$(date -u +%H%M%S)-$$"
run_one() {  # $1 = part name, $2 = comma-separated MODELS, $3 = index, $4 = number of models in this container
  local part="$1" models="$2" log="$PARTS/$1.log" rc t0 wall pid timeout_s
  CURRENT_CONTAINER="latent-$RUN_TAG-$3"
  if [ "$EVAL_TIMEOUT" = "auto" ]; then timeout_s=$(( 1800 + 600 * EXPECTED_IMAGES * $4 )); else timeout_s="$EVAL_TIMEOUT"; fi
  local cmd=(
    timeout --signal=TERM --kill-after=60 "$timeout_s"
    docker run --rm --name "$CURRENT_CONTAINER" --gpus "device=$GPU_DEVICE" -w /app
    ${LIMIT_ARGS[@]+"${LIMIT_ARGS[@]}"}
    -v "$HF_CACHE:/root/.cache/huggingface/hub"
    -v "$TASK_ZIP:/data/test_data.zip:ro"
    -v "$LORA_IN:/lora_in:ro"
    -v "$PARTS:/out"
    -v "$SHIM:/app/run_latent_eval.py:ro"
    -e HF_HOME=/root/.cache/huggingface
    -e TRANSFORMERS_CACHE=/root/.cache/huggingface/hub
    -e HF_DATASETS_CACHE=/root/.cache/huggingface/datasets
    -e HUGGINGFACE_HUB_CACHE=/root/.cache/huggingface/hub
    -e HF_HUB_ENABLE_HF_TRANSFER=0
    ${TOKEN_ARGS[@]+"${TOKEN_ARGS[@]}"}
    -e TRANSFORMERS_ALLOW_TORCH_LOAD=true
    -e PYTHONUNBUFFERED=1
    -e "ORIGINAL_MODEL_REPO=$BASE_REPO"
    -e "MODEL_TYPE=$MODEL_TYPE"
    -e DATASET=/data/test_data.zip
    -e "MODELS=$models"
    -e "EVALUATION_RESULTS_PATH=/out/$part.json"
    -e "IMAGE_EVAL_STRATA=$STRATA"
    -e "IMAGE_EVAL_NOISES=$NOISES"
    -e "IMAGE_EVAL_BATCH_SIZE=$BATCH"
    -e "LATENT_FP8_COMPUTE=$FP8_COMPUTE"
    "$EVAL_IMAGE" python -m run_latent_eval
  )
  t0=$(date +%s)
  if [ "$DRY" = "1" ]; then
    { printf '[DRY] '; printf '%q ' "${cmd[@]}"; printf '> %q 2>&1\n' "$log"; } | tee -a "$STEM.dry_commands.txt" >&2
    rc="DRY"
  else
    _log "run $part: MODELS=$models timeout=${timeout_s}s log=$log"
    "${cmd[@]}" > "$log" 2>&1 &
    pid=$!
    set +e; wait "$pid"; rc=$?; set -e
    docker kill "$CURRENT_CONTAINER" >/dev/null 2>&1 || true   # no-op when --rm already removed it
  fi
  wall=$(( $(date +%s) - t0 ))
  CURRENT_CONTAINER=""
  printf '%s\t%s\t%s\t%s\t%s\n' "$part" "$models" "$log" "$rc" "$wall" >> "$RUNS_FILE"
  { printf '===== container part=%s MODELS=%s rc=%s wall=%ss =====\n' "$part" "$models" "$rc" "$wall"
    if [ -f "$log" ]; then cat "$log"; fi; } >> "$COMBINED_LOG"
  _log "done $part rc=$rc wall=${wall}s"
}

if [ "$PER_REPO" = "1" ]; then
  for i in "${!REPOS[@]}"; do run_one "${LABELS[$i]}" "${REPOS[$i]}" "$i" 1; done
else
  run_one "all" "$(IFS=,; printf '%s' "${REPOS[*]}")" 0 "${#REPOS[@]}"
fi
if [ "$DRY" != "1" ]; then flock -u 9 || true; fi

# ---------------------------------------------------------------------------------------------------------
# 5. merge JSON, final = W*mean(text)+(1-W)*mean(no_text) exactly as tasks.py:309-329, one TSV row per model
# ---------------------------------------------------------------------------------------------------------
python3 "$TOOL" collect --out-json "$OUT_JSON" --parts "$PARTS" --runs "$RUNS_FILE" --manifest "$MANIFEST" \
  --tsv "$TSV" --meta "$STEM.meta.json" --task "$TASK_ID" --image-id "$IMAGE_ID" --god-sha "$IMG_SHA" \
  --fp8 "$FP8_COMPUTE" --per-repo "$PER_REPO" --weight "$WEIGHT" --expected-images "$EXPECTED_IMAGES" \
  --strata "$STRATA" --noises "$NOISES" --batch "$BATCH"
_log "merged: $OUT_JSON ; rows appended to $TSV ; log: $COMBINED_LOG"
