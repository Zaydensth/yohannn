#!/usr/bin/env bash
# pod_selfstop.sh (kit copy; generic) — runs INSIDE a RunPod pod, detached (nohup setsid). At DEADLINE (epoch UTC) it
#   1. packs the evidence under /workspace/i5 (rows, logs, run dirs WITHOUT weights, no secrets) into one .tgz,
#   2. uploads it to a PUBLIC Hugging Face dataset repo (user policy 2026-10-21: private = task datasets only, 100 GB;
#      research evidence/artifacts = public, 7.8 TB; token from /workspace/i5/secrets/.hf_env, never printed),
#   3. stops THIS pod through the GraphQL podStop mutation with the pod-scoped RUNPOD_API_KEY (from PID 1's env),
#      falling back to runpodctl. STOP keeps /workspace (the pod volume disk); only the container disk is lost.
# Modes: pod_selfstop.sh arm <deadline_epoch> [repo]   (install the timer; logs to /workspace/i5/logs/selfstop.log)
#        pod_selfstop.sh test-upload [repo]             (pack + upload now, do NOT stop — verifies the evidence path)
#        pod_selfstop.sh stop-now [repo]                (pack + upload + stop immediately)
# Nothing here reads results or scores; it only moves files.
set -u
MODE="${1:-}"; shift || true
I5="${POD_WORK_ROOT:-/workspace/i5}"   # your pod work root (rows, logs, run dirs live under it)
LOG="$I5/logs/selfstop.log"; mkdir -p "$I5/logs" "$I5/export"
say() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "$LOG"; }
POD_ID="$(tr '\0' '\n' < /proc/1/environ | sed -n 's/^RUNPOD_POD_ID=//p' | head -1)"
[ -n "$POD_ID" ] || POD_ID="$(hostname)"

pack() {   # -> prints the tgz path
  local ts; ts="$(date -u +%Y%m%dT%H%M%SZ)"
  local out="$I5/export/evidence_${POD_ID}_${ts}.tgz"
  ( cd "$I5" && tar czf "$out" \
      --exclude='*.safetensors' --exclude='*.pt' --exclude='*.bin' --exclude='secrets' --exclude='relay' \
      --exclude='cache' --exclude='latent/hf_cache' --exclude='export' --exclude='venv' --exclude='images' \
      --exclude='kitx_*' --exclude='repo_*' --exclude='runs/dry' \
      $(ls -d latent_* logs pod_state runs 2>/dev/null) 2>>"$LOG" )
  printf '%s\n' "$out"
}

upload() {   # upload <tgz> <repo>
  local f="$1" repo="$2"
  local py="$I5/venv/up/bin/python"; [ -x "$py" ] || py=python3
  HF_ENV="$I5/secrets/.hf_env" "$py" - "$f" "$repo" "$POD_ID" <<'PY' 2>>"$LOG"
import os, sys
f, repo, pod = sys.argv[1:4]
tok = None
for ln in open(os.environ["HF_ENV"]):
    k, _, v = ln.strip().partition("=")
    if k.upper() == "HF_TOKEN": tok = v.strip()
from huggingface_hub import HfApi
api = HfApi(token=tok)
dest = f"pod_evidence/{pod}/{os.path.basename(f)}"
api.upload_file(path_or_fileobj=f, path_in_repo=dest, repo_id=repo, repo_type="dataset")
print(f"UPLOADED {repo} {dest} bytes={os.path.getsize(f)}")
PY
}

stop_pod() {
  local key; key="$(tr '\0' '\n' < /proc/1/environ | sed -n 's/^RUNPOD_API_KEY=//p' | head -1)"
  if [ -n "$key" ]; then
    local resp; resp="$(curl -s -m 30 -X POST https://api.runpod.io/graphql -H "Authorization: Bearer $key" \
      -H 'content-type: application/json' \
      --data "{\"query\":\"mutation { podStop(input:{podId:\\\"$POD_ID\\\"}) { id desiredStatus } }\"}")"
    say "podStop graphql: ${resp:0:200}"
    printf '%s' "$resp" | grep -q '"desiredStatus"' && return 0
  fi
  if command -v runpodctl >/dev/null 2>&1; then
    runpodctl config --apiKey "$key" >/dev/null 2>&1; runpodctl stop pod "$POD_ID" >>"$LOG" 2>&1 && say "runpodctl stop ok" && return 0
  fi
  say "STOP FAILED (no key / both methods failed)"; return 1
}

case "$MODE" in
  arm)
    DL="${1:?deadline epoch}"; REPO="${2:-<you>/<public-evidence-dataset>}"
    say "ARMED pod=$POD_ID deadline=$(date -u -d "@$DL" +%FT%TZ 2>/dev/null || echo "$DL") repo=$REPO"
    while [ "$(date -u +%s)" -lt "$DL" ]; do sleep 60; done
    say "DEADLINE reached: pack + upload + stop"
    f="$(pack)"; say "packed $f ($(stat -c %s "$f" 2>/dev/null) B)"; upload "$f" "$REPO" | tee -a "$LOG"
    stop_pod ;;
  test-upload)
    REPO="${1:-<you>/<public-evidence-dataset>}"; f="$(pack)"; say "packed $f ($(stat -c %s "$f" 2>/dev/null) B)"; upload "$f" "$REPO" | tee -a "$LOG" ;;
  stop-now)
    REPO="${1:-<you>/<public-evidence-dataset>}"; f="$(pack)"; say "packed $f"; upload "$f" "$REPO" | tee -a "$LOG"; stop_pod ;;
  *) echo "usage: $0 arm <deadline_epoch> [repo] | test-upload [repo] | stop-now [repo]"; exit 2 ;;
esac
