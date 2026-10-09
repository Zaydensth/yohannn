#!/usr/bin/env bash
# build_eval_image.sh - build the public G.O.D image evaluator from source at a PINNED commit, on the box.
#
#   GOD_SHA=<40-hex> bash build_eval_image.sh        build + verify what is inside the image
#   GOD_SHA=<40-hex> DRY=1 bash build_eval_image.sh  print what would run
#
# You choose the pin. A commit nobody read must not produce a number anyone trusts, so a GOD_SHA that is not listed in
# REVIEWED_FILE (one 40-hex SHA per line, '#' comments allowed) is REFUSED unless ALLOW_UNREVIEWED_GOD=1. Review means:
# read validator/evaluation/ (evaluators/diffusion.py, image_artifacts.py, image_flow_adapter.py, image_test_data.py,
# constants.py) and validator/scoring/tasks.py (_calculate_weighted_loss_for_image_eval) at that commit, and diff them
# against the last commit you reviewed. Then append the SHA to REVIEWED_FILE yourself.
#
# Source of truth: gradients-ai/G.O.D ops/docker/validator-diffusion.dockerfile at GOD_SHA. Nothing is hardcoded here:
# the ComfyUI commit (ARG COMFYUI_COMMIT=...), the torch pin (torch==...) and the library pins in
# ops/docker/requirements/image-evaluator.txt are READ from the checkout and then checked inside the built image. At
# b49ba8644a0d (2026-10-09) they were ComfyUI 694815f4..., torch 2.9.1, diffusers 0.39.0, transformers 5.10.2,
# huggingface-hub 1.18.0, safetensors 0.8.0 (VERIFIED by reading the files at that commit).
# The image is tagged god-image-eval:main-<sha7> and LABELLED god.sha=<full sha>; score_latent.sh / warm_cache.sh read
# the same GOD_SHA and refuse any image whose label differs.
# NOTE: production runs a PREBUILT image (core/constants/docker.py VALIDATOR_DOCKER_IMAGE_DIFFUSION). Building from
# source at the same commit is INFERRED to match it; it is not byte-for-byte proof.
#
# env: GOD_SHA [required] KIT_ROOT [/root/kit] LATENT_ROOT [$KIT_ROOT/latent] GOD_DIR [$LATENT_ROOT/G.O.D]
#      TAG [god-image-eval:main-<sha7>] REVIEWED_FILE [<kit>/reviewed_god_shas.txt] ALLOW_UNREVIEWED_GOD [0] DRY [0]
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
KIT_ROOT="${KIT_ROOT:-/root/kit}"
LATENT_ROOT="${LATENT_ROOT:-$KIT_ROOT/latent}"
GOD_SHA="${GOD_SHA:-}"
GOD_DIR="${GOD_DIR:-$LATENT_ROOT/G.O.D}"
TAG="${TAG:-god-image-eval:main-${GOD_SHA:0:7}}"
REVIEWED_FILE="${REVIEWED_FILE:-$HERE/reviewed_god_shas.txt}"
DRY="${DRY:-0}"
LOG="$LATENT_ROOT/logs/build_eval_image_$(date -u +%Y%m%dT%H%M%SZ).log"
_log() { printf '[build_eval %s] %s\n' "$(date -u +%FT%TZ)" "$*" >&2; }
die() { _log "REFUSE: $*"; exit 1; }
run() { if [ "$DRY" = "1" ]; then printf '[DRY] %s\n' "$*" >&2; else "$@"; fi; }

[[ "$GOD_SHA" =~ ^[0-9a-f]{40}$ ]] || die "GOD_SHA must be a full 40-hex commit of gradients-ai/G.O.D (you choose it)"
if ! { [ -f "$REVIEWED_FILE" ] && grep -qxE "[[:space:]]*$GOD_SHA([[:space:]]+#.*)?" "$REVIEWED_FILE"; }; then
  [ "${ALLOW_UNREVIEWED_GOD:-0}" = "1" ] \
    || die "GOD_SHA $GOD_SHA is not listed in $REVIEWED_FILE - review validator/evaluation/ at that commit, append the SHA, or set ALLOW_UNREVIEWED_GOD=1"
  _log "WARNING: building an UNREVIEWED evaluator commit (ALLOW_UNREVIEWED_GOD=1)"
fi
if [ "$DRY" != "1" ]; then mkdir -p "$LATENT_ROOT/logs"; fi

# 1. pinned checkout (fetch the exact commit, never a branch tip)
if [ ! -d "$GOD_DIR/.git" ]; then
  run git init -q "$GOD_DIR"
  run git -C "$GOD_DIR" remote add origin https://github.com/gradients-ai/G.O.D
fi
run git -C "$GOD_DIR" fetch -q --depth 1 origin "$GOD_SHA"
run git -C "$GOD_DIR" -c advice.detachedHead=false checkout -q "$GOD_SHA"
DOCKERFILE="ops/docker/validator-diffusion.dockerfile"
REQS="ops/docker/requirements/image-evaluator.txt"
if [ "$DRY" = "1" ]; then
  run docker build --progress=plain -f "$DOCKERFILE" -t "$TAG" --label "god.sha=$GOD_SHA" "$GOD_DIR"
  exit 0
fi
head="$(git -C "$GOD_DIR" rev-parse HEAD)"
[ "$head" = "$GOD_SHA" ] || die "checkout is $head, want $GOD_SHA"
[ -z "$(git -C "$GOD_DIR" status --porcelain)" ] || { git -C "$GOD_DIR" status --porcelain >&2; die "$GOD_DIR is dirty"; }
EXPECT_COMFY="$(sed -n 's/^ARG COMFYUI_COMMIT=\([0-9a-f]\{40\}\).*/\1/p' "$GOD_DIR/$DOCKERFILE" | head -n1)"
EXPECT_TORCH="$(grep -oE 'torch==[0-9][0-9.]*' "$GOD_DIR/$DOCKERFILE" | head -n1 | cut -d= -f3)"
[ -n "$EXPECT_COMFY" ] || die "$DOCKERFILE has no 'ARG COMFYUI_COMMIT=<40-hex>' line - the evaluator changed shape; re-read it"
[ -n "$EXPECT_TORCH" ] || die "$DOCKERFILE has no torch==<version> pin - re-read it"
[ -f "$GOD_DIR/$REQS" ] || die "missing $REQS at $GOD_SHA - re-read the dockerfile"
_log "pins read from $GOD_SHA: ComfyUI $EXPECT_COMFY, torch $EXPECT_TORCH, $(grep -cE '^[A-Za-z0-9_.-]+==' "$GOD_DIR/$REQS") pinned requirement(s)"

# 2. build (needs network: github for ComfyUI, download.pytorch.org, pypi)
T0=$(date +%s)
_log "building $TAG from $GOD_DIR @ $GOD_SHA -> $LOG"
( cd "$GOD_DIR" && DOCKER_BUILDKIT=1 docker build --progress=plain -f "$DOCKERFILE" \
    -t "$TAG" --label "god.sha=$GOD_SHA" . ) > "$LOG" 2>&1 || { rc=$?; _log "BUILD FAILED rc=$rc - tail:"; tail -30 "$LOG" >&2; exit 1; }
_log "build ok in $(( $(date +%s) - T0 ))s"

# 3. verify what is inside (numbers are only comparable if these match the pins)
label="$(docker image inspect -f '{{index .Config.Labels "god.sha"}}' "$TAG")"
[ "$label" = "$GOD_SHA" ] || die "label god.sha=$label"
got="$(docker run --rm --network none --entrypoint sh "$TAG" -c 'cd /app/validator/evaluation/ComfyUI && git rev-parse HEAD')"
[ "$got" = "$EXPECT_COMFY" ] || die "ComfyUI in image is $got, the dockerfile pins $EXPECT_COMFY"
docker run --rm --network none --entrypoint sh "$TAG" -c '
  grep -n "^DIFFUSION_TEXT_GUIDED_EVAL_WEIGHT" /app/validator/evaluation/constants.py
  python -c "import torch,sys;print(\"python=\"+sys.version.split()[0],\"torch=\"+torch.__version__,\"cuda=\"+str(torch.version.cuda))"
' | tee -a "$LOG"
REQ_PINS="$(grep -E '^[A-Za-z0-9_.-]+==[^ ;#]+' "$GOD_DIR/$REQS" | sed 's/[[:space:]].*//' | tr '\n' ' ')"
docker run --rm --network none -e "REQ_PINS=$REQ_PINS" -e "EXPECT_TORCH=$EXPECT_TORCH" --entrypoint python "$TAG" -c "
import os, importlib.metadata as md, torch
bad = {}
for spec in os.environ['REQ_PINS'].split():
    name, want = spec.split('==', 1)
    try:
        have = md.version(name)
    except md.PackageNotFoundError:
        have = 'absent'
    if have != want:
        bad[name] = (have, want)
if torch.__version__.split('+')[0] != os.environ['EXPECT_TORCH']:
    bad['torch'] = (torch.__version__, os.environ['EXPECT_TORCH'])
print('library pins:', 'all match' if not bad else bad)
raise SystemExit(1 if bad else 0)
" | tee -a "$LOG" || die "library versions in the image drifted from $REQS / the dockerfile torch pin"
docker image inspect -f 'id={{.Id}} size={{.Size}} created={{.Created}}' "$TAG" | tee -a "$LOG"
_log "READY $TAG (god.sha=$GOD_SHA, comfy=$EXPECT_COMFY)"
