#!/usr/bin/env bash
# tests/test_kit.sh - CPU-only self-test of the image replica kit on a SYNTHETIC archive (tests/make_fixture.py: fake
# task ids, fake hotkeys, fake repos), with docker / nvidia-smi / flock / timeout replaced by stubs (tests/lib.sh).
# No GPU, no box, no network. Every GPU script is driven through its NON-dry path too, plus its refusal paths: a DRY
# that only proves a script agrees with its own assumptions is not a test.
#
#   bash tests/test_kit.sh            (~1 min)        KEEP=1 bash tests/test_kit.sh   keep the work dir
set -uo pipefail
. "$(dirname "$0")/lib.sh"
echo "== work dir: $WORK"
GOD=0123456789abcdef0123456789abcdef01234567       # a fake 40-hex pin (no such commit is needed: the stub labels the image)
export STUB_GOD_SHA="$GOD"

# ------------------------------------------------------------------------------------------------- 1. syntax
echo "== 1. syntax"
for f in "$KIT"/*.sh "$KIT"/tests/*.sh "$KIT"/vendor/*.sh; do run "bash -n ${f#$KIT/}" 0 bash -n "$f"; done
for f in "$KIT"/*.py "$KIT"/tests/*.py "$KIT"/hfmock/*.py "$KIT"/hfmock/*/*.py; do
  run "py_compile ${f#$KIT/}" 0 env PYTHONPYCACHEPREFIX="$WORK/pyc" python3 -m py_compile "$f"
done

# ------------------------------------------------------------------------------------------------- 2. fixture + latent_tool
echo "== 2. latent_tool.py"
SRC="$WORK/src"; mkdir -p "$SRC"
ARCHIVE="$(python3 "$KIT/tests/make_fixture.py" "$SRC")"
TOURN="$(basename "$ARCHIVE")"
T1=11111111-0000-4000-8000-000000000001; T2=22222222-0000-4000-8000-000000000002; T3=33333333-0000-4000-8000-000000000003
MDIR="$SRC/cache_manifests"
eq "$(python3 "$KIT/latent_tool.py" zipinfo "$ARCHIVE/tasks/R1_flux_11111111/test_data.zip")" 3 "zipinfo counts the images"
python3 - "$WORK/nocap.zip" <<'PY'
import sys, zipfile
with zipfile.ZipFile(sys.argv[1], "w") as z:
    z.writestr("a.png", b"x"); z.writestr("b.png", b"y"); z.writestr("b.txt", "caption")
PY
run "zipinfo refuses an image without a caption" 1 python3 "$KIT/latent_tool.py" zipinfo "$WORK/nocap.zip"
LORA1="$ARCHIVE/artifacts/lora/11111111_hkAAAAAA.safetensors"
eq "$(python3 "$KIT/latent_tool.py" stcheck "$LORA1")" 1 "stcheck reads the tensor count"
head -c 100 "$LORA1" > "$WORK/trunc.safetensors"
run "stcheck refuses a truncated file" 1 python3 "$KIT/latent_tool.py" stcheck "$WORK/trunc.safetensors"
run "rootckpt: one root checkpoint > 5 GiB resolves" 0 python3 "$KIT/latent_tool.py" rootckpt --repo example-org/base-flux-one-root --family flux --manifest-dir "$MDIR" --no-fetch
has "$LAST_LOG" "ROOT_N=1 ROOT_FILE=flux1.safetensors GATED=false" "rootckpt names the file and the gate"
run "rootckpt: three root checkpoints -> prepare_base would raise" 1 python3 "$KIT/latent_tool.py" rootckpt --repo example-org/base-three-roots --family z-image --manifest-dir "$MDIR" --no-fetch
has "$LAST_LOG" "ROOT_N=3" "rootckpt counts the roots"
run "rootckpt: gated repo reported" 0 python3 "$KIT/latent_tool.py" rootckpt --repo example-org/base-gated --family flux --manifest-dir "$MDIR" --no-fetch
has "$LAST_LOG" "GATED=auto" "rootckpt reports gated=auto"
run "rootckpt: ideogram4 never reads the base" 0 python3 "$KIT/latent_tool.py" rootckpt --repo any/thing --family ideogram4 --manifest-dir "$MDIR" --no-fetch
run "rootckpt: no recorded list + --no-fetch = unverifiable" 2 python3 "$KIT/latent_tool.py" rootckpt --repo example-org/unknown --family flux --manifest-dir "$MDIR" --no-fetch

# ------------------------------------------------------------------------------------------------- 3. archive_layout
echo "== 3. archive_layout.py"
eq "$(python3 "$KIT/archive_layout.py" detect --tourn "$ARCHIVE")" "LAYOUT=tasks" "detect: tasks layout"
run "map: every tasks/ dir maps to its task id" 0 python3 "$KIT/archive_layout.py" map --tourn "$ARCHIVE"
eq "$(grep -c . "$LAST_LOG")" 3 "map: three tasks"
cp -R "$ARCHIVE" "$WORK/linked"; LINKED="$WORK/linked"
run "link: uuid links made" 0 python3 "$KIT/archive_layout.py" link --tourn "$LINKED"
run "link again: idempotent" 0 python3 "$KIT/archive_layout.py" link --tourn "$LINKED"
run "check: every link resolves to its own dir" 0 python3 "$KIT/archive_layout.py" check --tourn "$LINKED"
rm "$LINKED/$T1"; ln -s "tasks/R1_ideogram4_22222222" "$LINKED/$T1"
run "link refuses a link that points elsewhere" 1 python3 "$KIT/archive_layout.py" link --tourn "$LINKED"
run "check fails on it" 1 python3 "$KIT/archive_layout.py" check --tourn "$LINKED"

# ------------------------------------------------------------------------------------------------- 4. verify_stage
echo "== 4. verify_stage.py"
run "view for the workstation check" 0 python3 "$KIT/archive_layout.py" view --tourn "$ARCHIVE" --out "$WORK/view"
VIEW="$WORK/view/$TOURN"
run "verify_stage PASS on the source (view)" 0 python3 "$KIT/verify_stage.py" --tourn "$VIEW" --ops "$KIT"
has "$LAST_LOG" "3/3 tasks of manifest.json present" "completeness against manifest.json"
cp -R "$ARCHIVE" "$WORK/broken"; printf 'garbage' > "$WORK/broken/tasks/R1_flux_11111111/training_data.zip"
python3 "$KIT/archive_layout.py" link --tourn "$WORK/broken" >/dev/null
run "verify_stage FAILs a corrupt zip" 1 python3 "$KIT/verify_stage.py" --tourn "$WORK/broken" --no-lora
rm -rf "$WORK/broken/tasks/Boss_z-image_33333333" "$WORK/broken/$T3"
run "verify_stage FAILs a planned task that never landed" 1 python3 "$KIT/verify_stage.py" --tourn "$WORK/broken" --no-lora
has "$LAST_LOG" "is in manifest.json but not in the archive" "completeness names the missing task"

# ------------------------------------------------------------------------------------------------- 5. cells + plan gate
echo "== 5. cells.py / plan_gate.py / lock_cells.sh"
P="$WORK/plan"; mkdir -p "$P"
cat > "$P/PLAN.md" <<'MD'
# PLAN test-ab
Status: **LOCKED**

FABLE-PLAN: FABLE 20260101-1200 PLAN=test-ab POINT=lock VERDICT=GO BASIS=test
Arms: ctl vs arm on one task; one change.

## RESULTS
MD
printf 'FABLE 20260101-1200 PLAN=test-ab POINT=lock VERDICT=GO BASIS=test\n' > "$P/FABLE_LEDGER.md"
{ printf '# lock UNBOUND\ncell\tenabled\ttourn\ttask_id\ttrainer_image\thours\tenv\tnote\n'
  printf 'ctl\t1\t%s\t%s\tmy-trainer:abc\ttask\t\tcontrol\n' "$TOURN" "$T1"
  printf 'arm\t1\t%s\t%s\tmy-trainer:abc\ttask\tMY_LEVER=1\tarm\n' "$TOURN" "$T1"; } > "$P/cells.tsv"
run "cells.py validates the test cells" 0 python3 "$KIT/cells.py" validate --cells "$P/cells.tsv"
run "cells.py validates the shipped example" 0 python3 "$KIT/cells.py" validate --cells "$KIT/cells.example.tsv"
sed 's/MY_LEVER=1/HF_TOKEN=x/' "$P/cells.tsv" > "$P/bad.tsv"
run "cells.py refuses a credential env" 1 python3 "$KIT/cells.py" validate --cells "$P/bad.tsv"
run "gate REFUSES an unbound cells file" 1 python3 "$KIT/plan_gate.py" gate --plan "$P/PLAN.md" --cells "$P/cells.tsv" --slug test-ab --ledger "$P/FABLE_LEDGER.md"
REPO_SRC="$WORK/miner"; mkdir -p "$REPO_SRC"
( cd "$REPO_SRC" && git init -q -b main && printf 'x\n' > a.txt && git add a.txt && git -c user.name=t -c user.email=t@example.invalid commit -qm init )
PIN="$(git -C "$REPO_SRC" rev-parse HEAD)"
run "lock_cells binds" 0 bash "$KIT/lock_cells.sh" --plan "$P/PLAN.md" --cells "$P/cells.tsv" --ledger "$P/FABLE_LEDGER.md" --slug test-ab --fable 20260101-1200 --pin-sha "$PIN" --repo "$REPO_SRC" --branch main
has "$P/cells.tsv" "pin_sha=$PIN" "line 1 carries the pin"
run "gate PASSES the bound file" 0 python3 "$KIT/plan_gate.py" gate --plan "$P/PLAN.md" --cells "$P/cells.tsv" --slug test-ab --ledger "$P/FABLE_LEDGER.md"
run "lock_cells again: same values, nothing to do" 0 bash "$KIT/lock_cells.sh" --plan "$P/PLAN.md" --cells "$P/cells.tsv" --ledger "$P/FABLE_LEDGER.md" --slug test-ab --fable 20260101-1200 --pin-sha "$PIN" --repo "$REPO_SRC" --branch main
cp "$P/cells.tsv" "$P/cells.bak"
sed 's/^arm\t1/arm\t0/' "$P/cells.bak" > "$P/cells.tsv"
run "switching a cell OFF keeps the lock" 0 python3 "$KIT/plan_gate.py" gate --plan "$P/PLAN.md" --cells "$P/cells.tsv" --slug test-ab --ledger "$P/FABLE_LEDGER.md"
sed 's/MY_LEVER=1/MY_LEVER=2/' "$P/cells.bak" > "$P/cells.tsv"
run "editing a row breaks the lock" 1 python3 "$KIT/plan_gate.py" gate --plan "$P/PLAN.md" --cells "$P/cells.tsv" --slug test-ab --ledger "$P/FABLE_LEDGER.md"
cp "$P/cells.bak" "$P/cells.tsv"
cp "$P/FABLE_LEDGER.md" "$P/ledger.bak"
printf 'FABLE 20260102-0900 PLAN=test-ab POINT=rescope VERDICT=REVISI BASIS=test\n' >> "$P/FABLE_LEDGER.md"
run "a later REVISI in the ledger ends the lock" 1 python3 "$KIT/plan_gate.py" gate --plan "$P/PLAN.md" --cells "$P/cells.tsv" --slug test-ab --ledger "$P/FABLE_LEDGER.md"
cp "$P/ledger.bak" "$P/FABLE_LEDGER.md"
cp "$P/PLAN.md" "$P/plan.bak"
printf 'Note added after the lock.\n' >> "$P/PLAN.md"
run "RESULTS may grow after the lock" 0 python3 "$KIT/plan_gate.py" gate --plan "$P/PLAN.md" --cells "$P/cells.tsv" --slug test-ab --ledger "$P/FABLE_LEDGER.md"
sed 's/^Arms:/Arms (changed):/' "$P/plan.bak" > "$P/PLAN.md"
run "editing the frozen text breaks the lock" 1 python3 "$KIT/plan_gate.py" gate --plan "$P/PLAN.md" --cells "$P/cells.tsv" --slug test-ab --ledger "$P/FABLE_LEDGER.md"
sed 's/VERDICT=GO BASIS/VERDICT=GO. BASIS/' "$P/plan.bak" > "$P/PLAN.md"
run "a malformed FABLE-PLAN line refuses (fail-closed)" 1 python3 "$KIT/plan_gate.py" lockcheck --plan "$P/PLAN.md" --slug test-ab --fable 20260101-1200
sed 's/VERDICT=GO/VERDICT=BYPASS-USER/' "$P/plan.bak" > "$P/PLAN.md"
run "BYPASS-USER does not bind by default" 1 python3 "$KIT/plan_gate.py" lockcheck --plan "$P/PLAN.md" --slug test-ab --fable 20260101-1200
run "BYPASS-USER binds with --allow-bypass" 0 python3 "$KIT/plan_gate.py" lockcheck --plan "$P/PLAN.md" --slug test-ab --fable 20260101-1200 --allow-bypass
sed 's/^# PLAN test-ab/# PLAN test-ab DRAFT/' "$P/plan.bak" > "$P/PLAN.md"
run "DRAFT in the frozen text refuses" 1 python3 "$KIT/plan_gate.py" lockcheck --plan "$P/PLAN.md" --slug test-ab --fable 20260101-1200
cp "$P/plan.bak" "$P/PLAN.md"
run "a pin that is not on the branch refuses" 1 bash "$KIT/lock_cells.sh" --plan "$P/PLAN.md" --cells "$P/cells.tsv" --ledger "$P/FABLE_LEDGER.md" --slug test-ab --fable 20260101-1200 --pin-sha 0000000000000000000000000000000000000000 --repo "$REPO_SRC" --branch main --rebind

# ------------------------------------------------------------------------------------------------- 6. stage.sh BOX=local
echo "== 6. stage.sh (BOX=local)"
BOXK="$WORK/box/kit"; mkdir -p "$BOXK"/{ops,data,api,locks,logs,runs,latent/hf_cache,cache/models,cache/datasets}
STG=(env PATH="$SPATH" KIT_ROOT="$BOXK" ARCHIVE="$ARCHIVE" REPO="$REPO_SRC" BRANCH=main CELLS_TSV="$P/cells.tsv" PLAN_FILE="$P/PLAN.md" LEDGER_FILE="$P/FABLE_LEDGER.md" MIN_FREE_GB=0)
run "stage.sh DRY" 0 "${STG[@]}" DRY=1 bash "$KIT/stage.sh" local
run "stage.sh BOX=local" 0 "${STG[@]}" bash "$KIT/stage.sh" local
eq "$(git -C "$BOXK/repo" rev-parse HEAD)" "$PIN" "the box checkout is the pinned commit"
[ -L "$BOXK/data/$TOURN/$T1" ] && ok "uuid links made on the box copy" || bad "uuid links on the box copy" "$LAST_LOG"
run "plan gate on the box (pin dir = the staged checkout)" 0 python3 "$BOXK/ops/plan_gate.py" gate --plan "$BOXK/plan/PLAN.md" --cells "$BOXK/plan/cells.tsv" --slug test-ab --ledger "$BOXK/plan/FABLE_LEDGER.md" --pin-dir "$BOXK/repo"
run "stage.sh again: idempotent" 0 "${STG[@]}" bash "$KIT/stage.sh" local
run "stage.sh refuses a busy box" 1 env STUB_PS="image-trainer-abc my-trainer:abc" "${STG[@]}" bash "$KIT/stage.sh" local

# ------------------------------------------------------------------------------------------------- 7. score_latent.sh
echo "== 7. score_latent.sh (stub evaluator)"
cp "$MDIR"/*.json "$BOXK/cache_manifests/" 2>/dev/null || { mkdir -p "$BOXK/cache_manifests"; cp "$MDIR"/*.json "$BOXK/cache_manifests/"; }
SL=(env PATH="$SPATH" KIT_ROOT="$BOXK" GOD_SHA="$GOD" HF_ENV_FILE="$WORK/no_token" HF_API_BASE="http://127.0.0.1:9")
TD1="$BOXK/data/$TOURN/$T1"; TD3="$BOXK/data/$TOURN/$T3"
L1="$BOXK/data/$TOURN/artifacts/lora/11111111_hkAAAAAA.safetensors"; L2="$BOXK/data/$TOURN/artifacts/lora/11111111_hkBBBBBB.safetensors"
run "score two local LoRAs (flux, one container each)" 0 "${SL[@]}" bash "$KIT/score_latent.sh" "$TD1" flux example-org/base-flux-one-root "$BOXK/latent/t1.json" "A=$L1" "B=$L2"
TSVL="$BOXK/latent/results_latent.tsv"
eq "$(awk -F'\t' 'NR>1 && $8=="OK"' "$TSVL" | grep -c .)" 2 "two OK rows"
python3 - "$TSVL" "$BOXK/latent/t1.json" <<'PY' > "$WORK/final_check.txt"
import csv, json, sys
rows = list(csv.DictReader(open(sys.argv[1]), delimiter="\t"))
d = json.load(open(sys.argv[2]))
for r in rows:
    e = d[r["evaluator_repo"]]["eval_loss"]
    t = sum(e["text_guided_losses"]) / len(e["text_guided_losses"]); n = sum(e["no_text_losses"]) / len(e["no_text_losses"])
    print("MATCH" if abs(0.5 * t + 0.5 * n - float(r["final"])) < 1e-12 else "MISMATCH", r["label"])
PY
eq "$(grep -c MATCH "$WORK/final_check.txt")" 2 "final = W*text + (1-W)*no_text re-derived from the evaluator JSON (W=0.5)"
hasnt "$WORK/final_check.txt" MISMATCH "no row disagrees with the formula"
run "OUT_JSON is never overwritten" 1 "${SL[@]}" bash "$KIT/score_latent.sh" "$TD1" flux example-org/base-flux-one-root "$BOXK/latent/t1.json" "A=$L1"
run "a base with three root checkpoints = PRECONDITION" 6 "${SL[@]}" bash "$KIT/score_latent.sh" "$TD3" z-image example-org/base-three-roots "$BOXK/latent/t3.json" "A=$L1"
run "a gated base without a token = PRECONDITION" 6 "${SL[@]}" bash "$KIT/score_latent.sh" "$TD1" flux example-org/base-gated "$BOXK/latent/t4.json" "A=$L1"
run "GOD_SHA unset refuses" 1 env PATH="$SPATH" KIT_ROOT="$BOXK" HF_API_BASE="http://127.0.0.1:9" bash "$KIT/score_latent.sh" "$TD1" flux example-org/base-flux-one-root "$BOXK/latent/t5.json" "A=$L1"
run "an image built from another commit refuses" 1 "${SL[@]}" STUB_GOD_SHA=1111111111111111111111111111111111111111 bash "$KIT/score_latent.sh" "$TD1" flux example-org/base-flux-one-root "$BOXK/latent/t6.json" "A=$L1"
run "fp8 guard: off -> emulated rows are OK" 0 "${SL[@]}" STUB_COMFY_QUANT=1 FP8_COMPUTE=off bash "$KIT/score_latent.sh" "$TD1" flux example-org/base-flux-one-root "$BOXK/latent/t7.json" "A=$L1"
eq "$(awk -F'\t' 'NR>1 && $24 ~ /t7.json/ {print $8"|"$26}' "$TSVL")" "OK|emulated" "row status + fp8_ops"
run "fp8 guard: off but native fp8 ops seen -> the row FAILS, never mixes numerics" 0 "${SL[@]}" STUB_COMFY_QUANT=1 STUB_FP8_NATIVE=1 FP8_COMPUTE=off bash "$KIT/score_latent.sh" "$TD1" flux example-org/base-flux-one-root "$BOXK/latent/t8.json" "A=$L1"
eq "$(awk -F'\t' 'NR>1 && $24 ~ /t8.json/ {print $8}' "$TSVL")" "FAILED(FP8_MODE:expected=off,seen=native)" "fp8 mismatch row status"
N_BEFORE="$(grep -c . "$TSVL")"
run "DRY validates and prints, writes no real row" 0 "${SL[@]}" DRY=1 bash "$KIT/score_latent.sh" "$TD1" flux example-org/base-flux-one-root "$BOXK/latent/t9.json" "A=$L1"
eq "$(grep -c . "$TSVL")" "$N_BEFORE" "DRY left the results TSV alone"

# ------------------------------------------------------------------------------------------------- 8. anchors.sh + board.py
echo "== 8. anchors.sh / board.py"
AN=(env PATH="$SPATH" KIT_ROOT="$BOXK" GOD_SHA="$GOD" HF_ENV_FILE="$WORK/no_token" HF_API_BASE="http://127.0.0.1:9" TOURN="$TOURN" BOSS_HK8=hkBBBBBB)
run "anchors.sh needs TOURN" 1 env PATH="$SPATH" KIT_ROOT="$BOXK" bash "$KIT/anchors.sh" plan
run "anchors.sh plan" 0 "${AN[@]}" bash "$KIT/anchors.sh" plan
has "$LAST_LOG" "SKIP" "the three-root base is skipped"
run "anchors.sh run" 0 "${AN[@]}" bash "$KIT/anchors.sh" run
has "$LAST_LOG" "harness calibration on anchors" "board prints the replica-vs-official calibration"
run "anchors.sh plan after run: DONE" 0 "${AN[@]}" bash "$KIT/anchors.sh" plan
has "$LAST_LOG" "DONE" "finished tasks are not re-scored"
run "anchors.sh warm (DRY)" 0 "${AN[@]}" DRY=1 FORCE=1 bash "$KIT/anchors.sh" warm

# ------------------------------------------------------------------------------------------------- 9. provision / build / warm refusals
echo "== 9. provision.sh / build_eval_image.sh / warm_cache.sh"
run "provision.sh DRY reports (not Linux here: FAIL os is expected)" 1 env PATH="$SPATH" DRY=1 KIT_ROOT="$WORK/prov" bash "$KIT/provision.sh"
has "$LAST_LOG" "summary:" "provision prints its summary"
[ ! -e "$WORK/prov" ] && ok "provision DRY created nothing" || bad "provision DRY created $WORK/prov" "$LAST_LOG"
run "build_eval_image refuses without GOD_SHA" 1 env KIT_ROOT="$BOXK" bash "$KIT/build_eval_image.sh"
run "build_eval_image refuses an unreviewed pin" 1 env KIT_ROOT="$BOXK" GOD_SHA="$GOD" REVIEWED_FILE="$WORK/none.txt" bash "$KIT/build_eval_image.sh"
printf '%s  # test\n' "$GOD" > "$WORK/reviewed.txt"
run "build_eval_image DRY with a reviewed pin" 0 env KIT_ROOT="$BOXK" GOD_SHA="$GOD" REVIEWED_FILE="$WORK/reviewed.txt" DRY=1 bash "$KIT/build_eval_image.sh"
run "warm_cache refuses without GOD_SHA" 2 env PATH="$SPATH" KIT_ROOT="$BOXK" bash "$KIT/warm_cache.sh" flux example-org/base-flux-one-root
run "warm_cache DRY" 0 env PATH="$SPATH" KIT_ROOT="$BOXK" GOD_SHA="$GOD" DRY=1 bash "$KIT/warm_cache.sh" flux example-org/base-flux-one-root

finish_tests
