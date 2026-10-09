#!/usr/bin/env bash
# warm_cache.sh FAMILY BASE_REPO [FAMILY BASE_REPO ...] - CPU-only, network-only: fill $LATENT_ROOT/hf_cache with
# every weight the evaluator will load for these (family, base) pairs, using the evaluator's own code (warm_cache.py).
# Takes no GPU and no GPU lock, so it can run in a screen while the GPU trains or builds. A GATED base needs a token
# in HF_ENV_FILE whose account accepted the repo's terms (the evaluator's own download then simply fails otherwise).
#   bash warm_cache.sh <family> <owner/base-repo> [<family> <owner/base-repo> ...]   (family/base = the task's
#        model_type / model_id from api/<task>.json; text encoders + VAE come from image_flow_adapter.py FAMILIES)
#   DRY=1 bash warm_cache.sh ...
# GOD_SHA [required] selects the evaluator image god-image-eval:main-<sha7> (EVAL_IMAGE); KIT_ROOT [/root/kit].
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
KIT_ROOT="${KIT_ROOT:-/root/kit}"
LATENT_ROOT="${LATENT_ROOT:-$KIT_ROOT/latent}"
HF_CACHE="${HF_CACHE:-$LATENT_ROOT/hf_cache}"
HF_ENV_FILE="${HF_ENV_FILE:-/root/.hf_env}"
GOD_SHA="${GOD_SHA:-}"
EVAL_IMAGE="${EVAL_IMAGE:-god-image-eval:main-${GOD_SHA:0:7}}"
DRY="${DRY:-0}"
[ "$#" -ge 2 ] && [ $(( $# % 2 )) -eq 0 ] || { sed -n '2,9p' "$0" >&2; exit 2; }
[[ "$GOD_SHA" =~ ^[0-9a-f]{40}$ ]] || { echo "[warm_cache.sh] GOD_SHA must be the 40-hex commit your evaluator image was built from" >&2; exit 2; }
TOKEN=""
[ -f "$HF_ENV_FILE" ] && TOKEN="$(grep -oE 'hf_[A-Za-z0-9]{20,}' "$HF_ENV_FILE" | head -n1 || true)"
ENV_FILE="$(mktemp)"; chmod 600 "$ENV_FILE"; trap 'rm -f "$ENV_FILE"' EXIT
if [ -n "$TOKEN" ]; then printf 'HF_TOKEN=%s\nHUGGING_FACE_HUB_TOKEN=%s\nHUGGINGFACE_TOKEN=%s\n' "$TOKEN" "$TOKEN" "$TOKEN" > "$ENV_FILE"; fi
rc_all=0
while [ "$#" -ge 2 ]; do
  FAMILY="$1"; REPO="$2"; shift 2
  case "$FAMILY" in ideogram4|z-image|qwen-image|krea2|flux) ;; *) echo "[warm_cache.sh] unknown family $FAMILY" >&2; rc_all=1; continue ;; esac
  cmd=(docker run --rm --name "latent-warm-$(date -u +%H%M%S)-$$" --network bridge
       -v "$HF_CACHE:/root/.cache/huggingface/hub" -v "$HERE/warm_cache.py:/latent/warm_cache.py:ro"
       -e HF_HOME=/root/.cache/huggingface -e TRANSFORMERS_CACHE=/root/.cache/huggingface/hub
       -e HF_DATASETS_CACHE=/root/.cache/huggingface/datasets -e HUGGINGFACE_HUB_CACHE=/root/.cache/huggingface/hub
       -e HF_HUB_ENABLE_HF_TRANSFER=0 --env-file "$ENV_FILE" -w /app -e PYTHONPATH=/app
       --entrypoint python "$EVAL_IMAGE" /latent/warm_cache.py "$FAMILY" "$REPO")
  if [ "$DRY" = "1" ]; then printf '[DRY] '; printf '%q ' "${cmd[@]}"; printf '\n'; continue; fi
  T0=$(date +%s)
  "${cmd[@]}"; rc=$?
  echo "[warm_cache.sh] family=$FAMILY repo=$REPO rc=$rc seconds=$(( $(date +%s) - T0 ))"
  [ "$rc" = 0 ] || rc_all=1
done
[ "$DRY" = "1" ] || du -sh "$HF_CACHE"
exit "$rc_all"
