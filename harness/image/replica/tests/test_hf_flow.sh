#!/usr/bin/env bash
# tests/test_hf_flow.sh - CPU-only test of the validator-shaped HF half and the cell/queue runners: hf_flow.py,
# hf_preflight.sh, cell_run.sh (DRY, real-mode with the docker stub, every failure class) and queue.sh, all against the
# LOOPBACK mock hub (hfmock/) running the REAL upstream uploader.py / materialize_model (vendor/god, fetched by
# vendor/fetch_god_vendor.sh). No GPU, no network beyond 127.0.0.1, no real token anywhere.
#
#   bash tests/test_hf_flow.sh        (~1-2 min)        KEEP=1 ... keep the work dir
# Exits 77 (skip) when vendor/god is not fetched yet.
set -uo pipefail
. "$(dirname "$0")/lib.sh"
UP="$KIT/vendor/god/trainer/containers/uploader.py"
if [ ! -f "$UP" ] || [ ! -f "$KIT/vendor/god/validator/evaluation/image_artifacts.py" ]; then
  echo "SKIP: vendor/god is not fetched - GOD_SHA=<sha> bash vendor/fetch_god_vendor.sh"; rm -rf "$WORK"; exit 77
fi
echo "== work dir: $WORK"
GOD=0123456789abcdef0123456789abcdef01234567
export STUB_GOD_SHA="$GOD"
REVIEWED="$(python3 -c 'import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$UP")"
export HF_UPLOADER_REVIEWED_SHA256="$REVIEWED"

# ------------------------------------------------------------------------------------------------- 0. fixture box + mock hub
SRC="$WORK/src"; mkdir -p "$SRC"; ARCHIVE="$(python3 "$KIT/tests/make_fixture.py" "$SRC")"; TOURN="$(basename "$ARCHIVE")"
T1=11111111-0000-4000-8000-000000000001
BOXK="$WORK/box/kit"; mkdir -p "$BOXK"/{ops,data,api,locks,logs,runs,plan,latent/hf_cache,cache/models,cache/datasets,cache_manifests}
cp -R "$ARCHIVE" "$BOXK/data/"; python3 "$KIT/archive_layout.py" link --tourn "$BOXK/data/$TOURN" >/dev/null
cp "$SRC"/cache_manifests/*.json "$BOXK/cache_manifests/"
HUB="$WORK/hub"; mkdir -p "$HUB"
python3 "$KIT/hfmock/mock_hub.py" token --dir "$HUB" --name test-user --token-file "$WORK/hf_env" >/dev/null
python3 "$KIT/hfmock/mock_hub.py" token --dir "$HUB" --name ro-user --role read --token-file "$WORK/hf_env_ro" >/dev/null
python3 "$KIT/hfmock/mock_hub.py" serve --dir "$HUB" --port-file "$WORK/hub.port" 2>"$WORK/hub.log" &
HUB_PID=$!; trap 'kill $HUB_PID 2>/dev/null' EXIT
for _ in $(seq 50); do [ -s "$WORK/hub.port" ] && break; sleep 0.1; done
API="http://127.0.0.1:$(cat "$WORK/hub.port")"
MOCK_TOKEN="$(sed -n 's/^HF_TOKEN=//p' "$WORK/hf_env")"
export STUB_FAKEHUB_DIR="$HUB"
BASE=(env PATH="$SPATH" KIT_ROOT="$BOXK" GOD_SHA="$GOD" HF_ENV_FILE="$WORK/hf_env" HF_HUB_API="$API" HF_API_BASE="http://127.0.0.1:9" POLL_S=1)
HFF=(python3 "$KIT/hf_flow.py")

# ------------------------------------------------------------------------------------------------- 1. hf_flow units
echo "== 1. hf_flow.py"
run "tokencheck: missing file" 1 "${HFF[@]}" tokencheck --env-file "$WORK/nope"
cp "$WORK/hf_env" "$WORK/hf_env_644"; chmod 644 "$WORK/hf_env_644"
run "tokencheck: group/other bits refused" 1 "${HFF[@]}" tokencheck --env-file "$WORK/hf_env_644"
cat "$WORK/hf_env" "$WORK/hf_env_ro" > "$WORK/hf_env_two"; chmod 600 "$WORK/hf_env_two"
run "tokencheck: two tokens refused" 1 "${HFF[@]}" tokencheck --env-file "$WORK/hf_env_two"
run "tokencheck: one token, 0600" 0 "${HFF[@]}" tokencheck --env-file "$WORK/hf_env"
run "whoami: write token" 0 "${HFF[@]}" whoami --env-file "$WORK/hf_env" --api "$API"
has "$LAST_LOG" "HF_USER=test-user" "whoami prints the account name only"
hasnt "$LAST_LOG" "$MOCK_TOKEN" "whoami never prints the token"
run "whoami: read-only token refused" 1 "${HFF[@]}" whoami --env-file "$WORK/hf_env_ro" --api "$API"
run "newname: default prefix" 0 "${HFF[@]}" newname --task-id "$T1"
grep -qE "^HF_REPO_NAME=lora-11111111-[a-z0-9]{6}$" "$LAST_LOG" && ok "newname shape lora-<task8>-<rand6>" || bad "newname shape" "$LAST_LOG"
run "newname: env prefix" 0 env HF_REPO_PREFIX=xyz "${HFF[@]}" newname --task-id "$T1"
has "$LAST_LOG" "HF_REPO_NAME=xyz-11111111-" "HF_REPO_PREFIX is used"
run "newname: bad prefix refused" 1 "${HFF[@]}" newname --task-id "$T1" --prefix "Bad Prefix"
L="$WORK/ledger.tsv"
"${HFF[@]}" ledger --tsv "$L" event=uploading repo=test-user/a cell=x >/dev/null
"${HFF[@]}" ledger --tsv "$L" event=kept repo=test-user/b cell=y >/dev/null
eq "$("${HFF[@]}" pending --tsv "$L" | tr '\n' ' ')" "test-user/a " "pending lists the never-deleted repo"
eq "$("${HFF[@]}" kept --tsv "$L" | tr '\n' ' ')" "test-user/b " "kept lists the repo left public on purpose"
run "endgate keep: lists, never fails" 0 "${HFF[@]}" endgate --tsv "$L" --policy keep --env-file "$WORK/hf_env" --api "$API"
run "endgate delete --no-sweep: a pending repo FAILS" 1 "${HFF[@]}" endgate --tsv "$L" --policy delete --no-sweep --env-file "$WORK/hf_env" --api "$API"
printf 'not\ta\tledger\n' > "$WORK/foreign.tsv"
run "endgate is fail-closed on a foreign ledger" 1 "${HFF[@]}" endgate --tsv "$WORK/foreign.tsv" --policy keep
run "ledger refuses to append to a foreign header" 1 "${HFF[@]}" ledger --tsv "$WORK/foreign.tsv" event=uploading repo=x/y

# ------------------------------------------------------------------------------------------------- 2. hf_preflight.sh
echo "== 2. hf_preflight.sh"
run "preflight DRY (mock hub, vendored uploader)" 0 env PATH="$SPATH" KIT_ROOT="$BOXK" DRY=1 HF_ENV_FILE="$WORK/hf_env" bash "$KIT/hf_preflight.sh"
has "$LAST_LOG" "HF PREFLIGHT PASS" "DRY preflight passes"
run "preflight real-mode (stub docker -> vendored uploader -> mock hub)" 0 "${BASE[@]}" bash "$KIT/hf_preflight.sh"
has "$LAST_LOG" "PASS delete" "the dummy repo was deleted"
has "$BOXK/locks/hf_preflight.ok" "mode=real" "stamp written"
run "preflight refuses an unreviewed uploader" 1 "${BASE[@]}" HF_UPLOADER_REVIEWED_SHA256=ffff bash "$KIT/hf_preflight.sh"

# ------------------------------------------------------------------------------------------------- 3. cell_run.sh
echo "== 3. cell_run.sh"
CELLS="$BOXK/plan/cells.tsv"
{ printf '# lock UNBOUND\ncell\tenabled\ttourn\ttask_id\ttrainer_image\thours\tenv\tnote\n'
  printf 'c1\t1\t%s\t%s\tmy-trainer:abc\ttask\t\tcontrol\n' "$TOURN" "$T1"
  printf 'c2\t1\t%s\t%s\tmy-trainer:abc\ttask\tMY_LEVER=1\tarm\n' "$TOURN" "$T1"; } > "$CELLS"
CR=("${BASE[@]}" CELLS_TSV="$CELLS")
run "DRY: commands printed, HF half on the mock hub" 0 env PATH="$SPATH" KIT_ROOT="$BOXK" CELLS_TSV="$CELLS" HF_ENV_FILE="$WORK/hf_env" DRY=1 bash "$KIT/cell_run.sh" c1
has "$LAST_LOG" "sha256 local == hub == fetched: yes" "DRY proves the HF half end to end"
hasnt "$LAST_LOG" "presigned" "DRY never prints the presigned training URL"
run "real mode: OK" 0 "${CR[@]}" ATTEMPT=a1 bash "$KIT/cell_run.sh" c1
RES="$BOXK/latent/results_cells.tsv"
eq "$(awk -F'\t' '$2=="c1" && $3=="a1" {print $6}' "$RES")" "OK" "results row status OK"
REPO1="$(awk -F'\t' '$2=="c1" && $3=="a1" {print $18}' "$RES")"
eq "$(awk -F'\t' '$2=="c1" && $3=="a1" {print ($20 == $21 && $21 == $22 && length($22) == 64) ? "same" : "differ"}' "$RES")" "same" "local == hub == fetched sha256"
run "the repo is gone after the score" 1 "${HFF[@]}" anonget --api "$API" --repo "$REPO1" --rev main --file checkpoints/last.safetensors
eq "$("${HFF[@]}" pending --tsv "$BOXK/latent/hf_flow.tsv" | grep -c .)" 0 "nothing left pending in the HF ledger"
CRE="$(grep -l '"--expected-repo-name"' "$STUB_STATE"/containers/*.json 2>/dev/null | head -n1)"
[ -z "$CRE" ] && ok "the trainer container was removed after the run" || bad "trainer container left behind: $CRE" "$LAST_LOG"
run "real mode with an arm env" 0 "${CR[@]}" ATTEMPT=a1 bash "$KIT/cell_run.sh" c2
has "$LAST_LOG" "NOTE: arm env (MY_LEVER=1) passed to the trainer - not production" "the arm env is announced as non-production"
run "FAIL_TRAIN: trainer exit 1 -> no upload" 3 "${CR[@]}" STUB_TRAIN_RC=1 ATTEMPT=f1 bash "$KIT/cell_run.sh" c1
eq "$(awk -F'\t' '$2=="c1" && $3=="f1" {print $6"|"$18}' "$RES" | cut -d'|' -f1)" "FAIL_TRAIN(rc=1)" "FAIL_TRAIN row"
run "timeout: killed, then uploaded and scored (production semantics)" 0 "${CR[@]}" STUB_TRAIN_SECONDS=30 ATTEMPT=t1 bash "$KIT/cell_run.sh" c1
eq "$(awk -F'\t' '$2=="c1" && $3=="t1" {print $6"|"$13"|"$12}' "$RES")" "OK|1|137" "OK row, timed_out=1, trainer rc 137"
run "no output folder -> FAIL_TRAIN(no-output)" 3 "${CR[@]}" STUB_TRAIN_NO_OUTPUT=1 ATTEMPT=n1 bash "$KIT/cell_run.sh" c1
run "upload failure -> FAIL_HF(upload), box-level" 4 "${CR[@]}" STUB_UPLOAD_RC=1 ATTEMPT=u1 bash "$KIT/cell_run.sh" c1
run "evaluator failure -> FAIL_SCORE, the live repo is cleaned up" 5 "${CR[@]}" STUB_EVAL_FAIL='*' ATTEMPT=e1 bash "$KIT/cell_run.sh" c1
eq "$("${HFF[@]}" pending --tsv "$BOXK/latent/hf_flow.tsv" | grep -c .)" 0 "the exit trap deleted the repo of the failed cell"
RD_E1="$(awk -F'\t' '$2=="c1" && $3=="e1" {print $23}' "$RES")"
run "RESCORE of that run's checkpoint (no training)" 0 "${CR[@]}" RESCORE=1 RESCORE_FROM="$RD_E1" ATTEMPT=r1 bash "$KIT/cell_run.sh" c1
eq "$(awk -F'\t' '$2=="c1" && $3=="r1" {print $6"|"$12}' "$RES")" "OK|" "RESCORE row OK, no trainer rc"
run "a missing trainer image = PRECONDITION" 6 "${CR[@]}" STUB_MISSING_IMAGES=my-trainer:abc ATTEMPT=m1 bash "$KIT/cell_run.sh" c1
run "an existing ATTEMPT dir is a usage error" 2 "${CR[@]}" ATTEMPT=a1 bash "$KIT/cell_run.sh" c1
run "an unknown cell is a usage error" 2 "${CR[@]}" bash "$KIT/cell_run.sh" nope

# ------------------------------------------------------------------------------------------------- 4. queue.sh
echo "== 4. queue.sh"
P="$BOXK/plan"
cat > "$P/PLAN.md" <<'MD'
# PLAN qtest
Status: **LOCKED**

FABLE-PLAN: FABLE 20260101-1200 PLAN=qtest POINT=lock VERDICT=GO BASIS=test

## RESULTS
MD
printf 'FABLE 20260101-1200 PLAN=qtest POINT=lock VERDICT=GO BASIS=test\n' > "$P/FABLE_LEDGER.md"
QRES="$BOXK/latent/results_q.tsv"
Q=("${BASE[@]}" CELLS_TSV="$CELLS" RESULTS_TSV="$QRES" SLUG=qtest PIN_DIR="$WORK/nopin" MIN_FREE_GB=0)
run "queue refuses an unbound cells file" 1 "${Q[@]}" bash "$KIT/queue.sh"
run "queue plan works without a lock" 0 "${Q[@]}" bash "$KIT/queue.sh" plan
run "lock the cells" 0 bash "$KIT/lock_cells.sh" --plan "$P/PLAN.md" --cells "$CELLS" --ledger "$P/FABLE_LEDGER.md" --slug qtest --fable 20260101-1200
run "queue run: two cells" 0 "${Q[@]}" bash "$KIT/queue.sh"
eq "$(awk -F'\t' 'NR>1 && $6=="OK"' "$QRES" | grep -c .)" 2 "two OK rows"
run "queue again: RESUME SKIP, nothing trained" 0 "${Q[@]}" bash "$KIT/queue.sh"
has "$LAST_LOG" "RESUME SKIP" "terminal rows are never re-trained"
eq "$(awk -F'\t' 'NR>1' "$QRES" | grep -c .)" 2 "still two rows"
run "FORCE_CELLS: one audited extra draw" 0 "${Q[@]}" FORCE_CELLS=c1 bash "$KIT/queue.sh"
has "$BOXK/latent/queue_reruns.tsv" "forced" "the forced draw is audited"
QRES2="$BOXK/latent/results_q2.tsv"
run "box-level failures: OPS-RERUN (rescore) then STOP" 1 "${Q[@]}" RESULTS_TSV="$QRES2" STUB_EVAL_FAIL='*' bash "$KIT/queue.sh"
has "$BOXK/latent/queue_reruns.tsv" "rescore" "the OPS-RERUN rescored the same checkpoint"
has "$BOXK/ALERT_queue.txt" "failed its OPS-RERUN" "the ALERT file names the cell"
touch "$BOXK/STOP_queue"
run "a STOP file refuses a new run" 1 "${Q[@]}" bash "$KIT/queue.sh"
rm -f "$BOXK/STOP_queue"
printf 'FABLE 20260102-0900 PLAN=qtest POINT=rescope VERDICT=NO-GO BASIS=test\n' >> "$P/FABLE_LEDGER.md"
run "a later NO-GO in the ledger stops the queue before any GPU work" 1 "${Q[@]}" bash "$KIT/queue.sh"

# ------------------------------------------------------------------------------------------------- 5. token hygiene
echo "== 5. token hygiene"
HITS="$(grep -rlF -- "$MOCK_TOKEN" "$WORK" 2>/dev/null | grep -vE "/hf_env(_two|_644)?$|/hub/accounts.json$" || true)"
[ -z "$HITS" ] && ok "the token appears in no file but the token files and the mock hub's accounts" || bad "token found in: $HITS" "-"

finish_tests
