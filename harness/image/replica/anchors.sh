#!/usr/bin/env bash
# anchors.sh plan|warm|run|board - score PUBLIC shipped LoRAs with the evaluator replica (score_latent.sh) to calibrate it.
#
# Why: the official board of every archived task (api/<task>.json hotkey_details[].test_loss) is a known answer. Score
# the very bytes the validator scored - the repos named in hotkey_details[].repo, pinned at the commit that was scored
# and checked against the HF LFS sha256 (fetch_artifacts.py pin + fetch) - and compare: replica vs official per
# artifact (board.py delta_ln%) is your harness error. Do it BEFORE you trust any gap your own runs show; a replica that
# cannot reproduce the board cannot rank your arms against it either.
#
#   TOURN=tourn_<id>_<yyyymmdd> bash anchors.sh plan    the queue (task, family, base, images, labels, status)
#   TOURN=... bash anchors.sh warm                       fill the evaluator HF cache for every queued (family, base) pair -
#                                                        CPU/network only, no GPU lock (warm_cache.sh)
#   TOURN=... bash anchors.sh run                        score the queue: one score_latent.sh call per task, one container
#                                                        per artifact (production shape) -> $RESULTS_TSV, then board.py
#   TOURN=... bash anchors.sh board                      board.py over $RESULTS_TSV
#   DRY=1 ... anchors.sh run                             score_latent.sh in DRY mode (validates, stages, prints commands)
#
# Queue (built from the STAGED archive: artifacts/lora/MANIFEST.tsv + api/<task>.json): every task that holds a role
# artifact (MANIFEST role TOP1..TOPn / BOSS / OURS), in task-id order, optionally narrowed by TASKS (task8 prefixes).
#   SKIP    a base the evaluator cannot resolve (prepare_base needs EXACTLY one root .safetensors > 5 GiB in the task
#           repo, image_artifacts.py:89-94; latent_tool.py rootckpt on the recorded file list), or a gated base with no
#           token in HF_ENV_FILE. An unverifiable file list queues the task with a note: the evaluator decides.
#   DONE    every label of the task already has an OK row in $RESULTS_TSV (FORCE=1 re-scores)
# Labels are ROLE_<task8>_<hk8> (board.py joins them to the official board by the hk8 inside the label).
# SOURCE=local (default) scores the sha256-pinned copies under data/<tourn>/artifacts/lora/ through the local/<label>
# shim - the same path your own candidates take, so harness effects cancel in every gap. SOURCE=hf scores the HF repos
# themselves through the evaluator's real materialize_model + select_lora (it resolves HEAD at eval time; the TSV
# 'resolved' column records the revision). SOURCE=both: every anchor locally, plus ONCE PER FAMILY the same anchor from
# its HF repo under <label>__hf - the HF-vs-disk check of your local staging (board.py prints it).
#
# env: TOURN [required] KIT_ROOT [/root/kit] LATENT_ROOT [$KIT_ROOT/latent] SOURCE [local|hf|both] TASKS []
#      FORCE [0] DRY [0] LOCK_WAIT [0] HF_ENV_FILE [/root/.hf_env] MANIFEST_DIR [$KIT_ROOT/cache_manifests]
#      HF_API_BASE [https://huggingface.co] BOSS_HK8 / OURS_HK8 [] (roles of manifest rows without a role column)
#      RESULTS_TSV [$LATENT_ROOT/results_anchors.tsv] IMG_S / LOAD_S [] (seconds per image-model / per container: set
#      them from YOUR measurements to get a wall-time estimate in `plan`; unset = no estimate)
#      + everything score_latent.sh reads (GOD_SHA is required there)
set -euo pipefail
CMD="${1:-}"
case "$CMD" in plan|warm|run|board) ;; *) sed -n '2,/^set -euo/p' "$0" | sed '$d' >&2; exit 2 ;; esac
HERE="$(cd "$(dirname "$0")" && pwd)"
KIT_ROOT="${KIT_ROOT:-/root/kit}"
LATENT_ROOT="${LATENT_ROOT:-$KIT_ROOT/latent}"
TOURN="${TOURN:-}"
SOURCE="${SOURCE:-local}"
TASKS="${TASKS:-}"
FORCE="${FORCE:-0}"
DRY="${DRY:-0}"
LOCK_WAIT="${LOCK_WAIT:-0}"
HF_ENV_FILE="${HF_ENV_FILE:-/root/.hf_env}"
MANIFEST_DIR="${MANIFEST_DIR:-$KIT_ROOT/cache_manifests}"
HF_API_BASE="${HF_API_BASE:-https://huggingface.co}"
BOSS_HK8="${BOSS_HK8:-}"
OURS_HK8="${OURS_HK8:-}"
RESULTS_TSV="${RESULTS_TSV:-$LATENT_ROOT/results_anchors.tsv}"
IMG_S="${IMG_S:-}"
LOAD_S="${LOAD_S:-}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
export PYTHONDONTWRITEBYTECODE=1
_log() { printf '[anchors %s] %s\n' "$(date -u +%H:%M:%S)" "$*" >&2; }
die() { _log "REFUSE: $*"; exit 1; }
case "$SOURCE" in local|hf|both) ;; *) die "SOURCE must be local, hf or both" ;; esac
for v in FORCE DRY; do case "${!v}" in 0|1) ;; *) die "$v must be 0 or 1" ;; esac; done
[[ "$LOCK_WAIT" =~ ^[0-9]+$ ]] || die "LOCK_WAIT must be a non-negative integer"
for v in IMG_S LOAD_S; do [ -z "${!v}" ] || [[ "${!v}" =~ ^[0-9]+$ ]] || die "$v must be empty or a non-negative integer"; done
[[ "$TOURN" =~ ^tourn_[0-9a-f]+_[0-9]{8}$ ]] || die "TOURN must name the staged archive (tourn_<id>_<yyyymmdd>)"
TOURN_DIR="$KIT_ROOT/data/$TOURN"
[ -f "$TOURN_DIR/artifacts/lora/MANIFEST.tsv" ] || die "no $TOURN_DIR/artifacts/lora/MANIFEST.tsv - pin + fetch the anchors first (fetch_artifacts.py), then stage.sh"
HAS_TOKEN=0
if [ -f "$HF_ENV_FILE" ] && grep -qE 'hf_[A-Za-z0-9]{20,}' "$HF_ENV_FILE" 2>/dev/null; then HAS_TOKEN=1; fi

build_queue() {  # -> TSV on stdout: task family base n_images n_todo labels specs status note
  TOURN_DIR="$TOURN_DIR" RESULTS_TSV="$RESULTS_TSV" SOURCE="$SOURCE" FORCE="$FORCE" KIT="$HERE" TASKS="$TASKS" \
  HAS_TOKEN="$HAS_TOKEN" HF_ENV_FILE="$HF_ENV_FILE" BOSS_HK8="$BOSS_HK8" OURS_HK8="$OURS_HK8" \
  MANIFEST_DIR="$MANIFEST_DIR" HF_API_BASE="$HF_API_BASE" python3 - <<'PY'
import csv, json, os, sys, zipfile
from pathlib import Path

E = os.environ
sys.path.insert(0, E["KIT"])
import latent_tool  # kit-local: rootckpt's file-list rule

root = Path(E["TOURN_DIR"])
want_tasks = E["TASKS"].split()
hk_roles = {h: r for h, r in ((E["BOSS_HK8"], "BOSS"), (E["OURS_HK8"], "OURS")) if h}
done = set()
if Path(E["RESULTS_TSV"]).is_file() and E["FORCE"] != "1":
    for r in csv.DictReader(open(E["RESULTS_TSV"]), delimiter="\t"):
        if r.get("status") == "OK":
            done.add((r["task"], r["label"]))


def role_of(r):
    return (r.get("role") or "").strip() or hk_roles.get(r["hk8"], "")


def role_key(role):
    return (0, int(role[3:] or 0)) if role.startswith("TOP") else (1, 0) if role == "BOSS" else (2, 0)


by_task = {}
for r in csv.DictReader(open(root / "artifacts" / "lora" / "MANIFEST.tsv"), delimiter="\t"):
    role = role_of(r)
    if role and r.get("status", "").startswith("OK"):
        if not want_tasks or any(r["task_id"].startswith(t) for t in want_tasks):
            by_task.setdefault(r["task_id"], []).append(r)


def base_check(family, base):
    if family == "ideogram4":
        return None, "", "not-checked"
    try:
        files, sha, gated = latent_tool.repo_manifest(base, E["MANIFEST_DIR"], E["HF_API_BASE"])
    except latent_tool.ManifestUnavailable as e:
        return None, f"base layout unverified ({e}) - the evaluator decides", e.gated
    roots = [(p, s) for p, s in files if p.endswith(".safetensors") and "/" not in p and (s or 0) > latent_tool.GIB5]
    if len(roots) == 1:
        return None, "", gated
    return "SKIP", (f"{family}: prepare_base raises ValueError (image_artifacts.py:89-94) on {base}@{sha[:12]}: "
                    f"{len(roots)} root .safetensors > 5 GiB"), gated


def image_count(task):
    exts = (".png", ".jpg", ".jpeg", ".webp", ".bmp")  # image_test_data.py:56
    with zipfile.ZipFile(root / task / "test_data.zip") as z:
        return sum(1 for n in z.namelist() if n.lower().endswith(exts))


hf_families = set()
for task in sorted(by_task):
    arts = by_task[task]
    t8 = task[:8]
    api = json.loads((root / "api" / f"{task}.json").read_text())
    family, base = api["model_type"], api["model_id"]
    board = {h["hotkey"][:8]: h for h in api.get("hotkey_details") or []}
    labels, specs, notes = [], [], []
    for a in sorted(arts, key=lambda a: (role_key(role_of(a)), a["hk8"])):
        label = f"{role_of(a)}_{t8}_{a['hk8']}"
        repo = (board.get(a["hk8"]) or {}).get("repo") or ""
        if repo and repo != a["repo"]:
            notes.append(f"{label}: api repo {repo} != manifest repo {a['repo']}")
        if (task, label) not in done:
            if E["SOURCE"] in ("local", "both"):
                src = root / "artifacts" / "lora" / f"{t8}_{a['hk8']}.safetensors"
                if not src.is_file() or str(src.stat().st_size) != a["bytes"]:
                    notes.append(f"{label}: staged artifact missing or wrong size ({src})")
                    continue
                specs.append(f"{label}={src}")
            else:
                if not repo:
                    notes.append(f"{label}: no hotkey_details repo in the api json")
                    continue
                specs.append(f"{label}={repo}")
            labels.append(label)
        if E["SOURCE"] == "both" and family not in hf_families and repo and (task, label + "__hf") not in done:
            hf_families.add(family)
            specs.append(f"{label}__hf={repo}")
            labels.append(label + "__hf")
    bstatus, bnote, gated = base_check(family, base)
    if bnote:
        notes.insert(0, bnote)
    all_labels = [f"{role_of(a)}_{t8}_{a['hk8']}" for a in arts]
    if bstatus:
        status = bstatus
    elif gated not in ("false", "unknown", "not-checked") and E["HAS_TOKEN"] != "1":
        status = "SKIP"
        notes.insert(0, f"base {base} is gated (gated={gated}) and {E['HF_ENV_FILE']} holds no token")
    elif not labels and all((task, l) in done for l in all_labels):
        status = "DONE"
    elif not labels:
        status = "SKIP"
    else:
        status = "QUEUED"
    print("\t".join(str(x) for x in (task, family, base, image_count(task), len(labels), ",".join(labels) or "-",
                                    ",".join(specs) or "-", status, "; ".join(notes) or "-")))
PY
}

QUEUE="$(build_queue)" || die "cannot build the queue from $TOURN_DIR"

print_plan() {
  local total=0 task family base nimg ntodo labels specs status note est
  printf '%-8s %-10s %-44s %3s %-60s %-6s %s\n' task family base img "labels (to score)" status note
  while IFS='	' read -r task family base nimg ntodo labels specs status note; do
    [ -n "$task" ] || continue
    est=0
    if [ "$status" = "QUEUED" ] && [ -n "$IMG_S" ] && [ -n "$LOAD_S" ]; then est=$(( ntodo * (LOAD_S + nimg * IMG_S) )); total=$((total + est)); fi
    printf '%-8s %-10s %-44s %3s %-60s %-6s %s\n' "${task:0:8}" "$family" "$base" "$nimg" "$labels" "$status" \
      "$([ "$est" -gt 0 ] && printf '~%dmin ' $((est / 60)))$([ "$note" != "-" ] && printf '%s' "$note")"
  done <<< "$QUEUE"
  if [ -n "$IMG_S" ] && [ -n "$LOAD_S" ]; then
    _log "SOURCE=$SOURCE token=$([ "$HAS_TOKEN" = 1 ] && echo yes || echo no): estimated GPU wall ~$((total / 60)) min (your IMG_S/LOAD_S; first-time weight downloads excluded - run 'warm' first)"
  else
    _log "SOURCE=$SOURCE token=$([ "$HAS_TOKEN" = 1 ] && echo yes || echo no) (set IMG_S/LOAD_S from your own measurements for a wall-time estimate)"
  fi
}

case "$CMD" in
plan)
  print_plan
  ;;
warm)
  PAIRS=(); seen=" "
  while IFS='	' read -r task family base nimg ntodo labels specs status note; do
    [ "$status" = "QUEUED" ] || continue
    case "$seen" in *" $family|$base "*) continue ;; esac
    seen="$seen$family|$base "; PAIRS+=("$family" "$base")
  done <<< "$QUEUE"
  [ "${#PAIRS[@]}" -gt 0 ] || { _log "nothing queued - nothing to warm"; exit 0; }
  _log "warming: ${PAIRS[*]}"
  DRY="$DRY" KIT_ROOT="$KIT_ROOT" LATENT_ROOT="$LATENT_ROOT" HF_ENV_FILE="$HF_ENV_FILE" bash "$HERE/warm_cache.sh" "${PAIRS[@]}"
  ;;
run)
  print_plan
  [ "$DRY" = "1" ] || { mkdir -p "$LATENT_ROOT/anchors"; printf '%s\n' "$QUEUE" > "$LATENT_ROOT/anchors/queue_$TS.tsv"; }
  n_run=0; n_fail=0
  while IFS='	' read -r task family base nimg ntodo labels specs status note; do
    [ "$status" = "QUEUED" ] || continue
    IFS=',' read -r -a SPEC_ARR <<< "$specs"
    OUT_JSON="$LATENT_ROOT/anchors/${task:0:8}_${TS}_$$.json"
    _log "${task:0:8} $family: ${#SPEC_ARR[@]} artifact(s) -> $OUT_JSON"
    set +e
    TSV="$RESULTS_TSV" DRY="$DRY" LOCK_WAIT="$LOCK_WAIT" KIT_ROOT="$KIT_ROOT" LATENT_ROOT="$LATENT_ROOT" HF_ENV_FILE="$HF_ENV_FILE" \
      MANIFEST_DIR="$MANIFEST_DIR" HF_API_BASE="$HF_API_BASE" \
      bash "$HERE/score_latent.sh" "$TOURN_DIR/$task" "$family" "$base" "$OUT_JSON" "${SPEC_ARR[@]}" < /dev/null
    rc=$?
    set -e
    n_run=$((n_run + 1))
    if [ "$rc" != "0" ]; then
      n_fail=$((n_fail + 1))
      _log "score_latent.sh refused/failed (rc=$rc) on ${task:0:8} - STOPPING (a refusal is systemic: lock, image, cache, token...). Fix it and re-run: finished tasks are skipped."
      break
    fi
  done <<< "$QUEUE"
  _log "ran $n_run task(s), $n_fail stopped the queue; results: $RESULTS_TSV"
  if [ "$DRY" != "1" ] && [ -f "$RESULTS_TSV" ]; then
    python3 "$HERE/board.py" --tsv "$RESULTS_TSV" --api "$KIT_ROOT/api" --boss "$BOSS_HK8" --ours "$OURS_HK8" || true
  fi
  [ "$n_fail" = "0" ] || exit 1
  ;;
board)
  [ -f "$RESULTS_TSV" ] || die "no $RESULTS_TSV yet - run 'anchors.sh run' first"
  python3 "$HERE/board.py" --tsv "$RESULTS_TSV" --api "$KIT_ROOT/api" --boss "$BOSS_HK8" --ours "$OURS_HK8"
  ;;
esac
