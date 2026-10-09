# Image validator replica — the box kit

Run the SN56 **image** pipeline on your own GPU box the way the validator runs it, so that a number you measure is a
number the tournament would have produced:

```
downloader (production image) -> TRAIN (your trainer image, the validator's CLI, mounts, limits, no-internet bridge,
timeout = kill-then-upload) -> UPLOAD (production uploader image: public HF repo) -> VERIFY (public, select_lora,
sha256) -> SCORE through HF (the public G.O.D image evaluator, built from source at a commit YOU reviewed) ->
FETCHCHECK (the evaluator scored exactly the uploaded bytes) -> DELETE the repo -> one result row
```

plus calibration against official boards (anchors), a lock-gated sequential queue, staging with integrity checks on
both sides, and CPU tests that drive every script through its real path with stubs.

**Labels used below.** VERIFIED = run in this package's CPU tests (docker replaced by `tests/docker_stub.py`, the
Hugging Face Hub by the loopback mock in `hfmock/`), or read in `gradients-ai/G.O.D` main at `b49ba8644a0d`
(2026-10-09). INFERRED = not run on a real GPU box in this form. **This kit, as shipped, has not run on a GPU box:
treat your first box session as its integration test** (run `DRY=1` first, then one cell, then the queue).

## What it is not

- **No training recipe.** It runs YOUR trainer image; it knows nothing about hyper-parameters, schedules or models.
- **No measured numbers.** Every threshold and estimate that depends on hardware or on a recipe is a parameter you
  fill from your own measurements (`IMG_S` / `LOAD_S`, `EVAL_TIMEOUT`, `MIN_FREE_GB`, ...).
- **No upstream code.** The three G.O.D files the DRY runs and tests execute are fetched at setup
  (`vendor/fetch_god_vendor.sh`, see `vendor/god/VENDOR.md`); the evaluator is built from G.O.D source on the box.
- **No real data.** Tests build a synthetic archive (`tests/make_fixture.py`): fake task ids, fake hotkeys, fake repos.

## Prerequisites

| where | what |
|---|---|
| box | Linux, 1x H100 (image tasks train on `H100_1X`: `validator/tournament/gpu_requirements.py:45-46`), NVIDIA driver, root; `provision.sh` installs/checks docker, nvidia-container-toolkit, `internal_bridge`, tools, dirs |
| box | a Hugging Face **write** token in `/root/.hf_env` (one line `HF_TOKEN=hf_...`, mode 600) - created by you, never pasted into a chat, a command line or a repo |
| workstation | bash, python3 >= 3.8, git, rsync, ssh to the box; your miner repo; your private tournament archive (`../archive/`) |
| both | the G.O.D commit you reviewed (`GOD_SHA`, 40 hex) |

## Run order

```bash
# --- workstation -----------------------------------------------------------------------------------------------
python3 ../archive/hf_archive_tournament.py fetch tourn_<id>_<yyyymmdd>      # within 7 days of the tournament
python3 fetch_artifacts.py pin <archives>/tourn_<id>_<yyyymmdd> --top 2 --boss <hk8>   # pins the public anchors
GOD_SHA=<sha> bash vendor/fetch_god_vendor.sh                                 # upstream files for DRY + tests
#   your review decisions live in THIS (workstation) copy of the kit and travel with it - stage.sh mirrors the kit
#   with --delete, so a file created only on the box would vanish on the next stage:
#   READ vendor/god/trainer/containers/uploader.py, then:   sha256sum it | cut -d' ' -f1 > reviewed_uploader.sha256
#   READ validator/evaluation/ at GOD_SHA, then:            echo '<GOD_SHA>  # <date> <what you read>' >> reviewed_god_shas.txt
bash tests/test_kit.sh && bash tests/test_hf_flow.sh                          # CPU tests, ~2 min, no network

# --- box (root) ------------------------------------------------------------------------------------------------
bash provision.sh                                                             # idempotent; never reboots
umask 077; cat > /root/.hf_env                                                # paste HF_TOKEN=hf_..., Ctrl-D
python3 $KIT_ROOT/ops/hf_flow.py tokencheck && python3 $KIT_ROOT/ops/hf_flow.py whoami

# --- workstation: stage ----------------------------------------------------------------------------------------
ARCHIVE=<archives>/tourn_<id>_<yyyymmdd> REPO=<miner repo> BRANCH=<branch> bash stage.sh root@<box>

# --- box -------------------------------------------------------------------------------------------------------
cd $KIT_ROOT/ops
bash hf_preflight.sh                                   # dummy public repo round trip through the production uploader
GOD_SHA=<sha> bash build_eval_image.sh                 # builds + checks ComfyUI/torch/library pins read from the dockerfile
GOD_SHA=<sha> TOURN=tourn_<id>_<yyyymmdd> bash anchors.sh warm   # CPU/network only: fills the evaluator HF cache
GOD_SHA=<sha> TOURN=tourn_<id>_<yyyymmdd> bash anchors.sh run    # replica vs official board -> your harness error

# --- workstation: lock (no GPU work without a locked plan) -----------------------------------------------------
#   the strategist's line "FABLE-PLAN: FABLE <stamp> PLAN=<slug> POINT=lock VERDICT=GO ..." in PLAN.md and the ledger
bash lock_cells.sh --plan PLAN.md --cells cells.tsv --ledger FABLE_LEDGER.md --slug <slug> --fable <stamp> \
     --pin-sha <trainer commit> --repo <miner repo> --branch <branch>
CELLS_TSV=cells.tsv PLAN_FILE=PLAN.md LEDGER_FILE=FABLE_LEDGER.md ARCHIVE=... REPO=... BRANCH=... bash stage.sh root@<box>

# --- box: trainer image + queue --------------------------------------------------------------------------------
cd $KIT_ROOT/repo && DOCKER_BUILDKIT=0 docker build --no-cache -f <dockerfile> -t <tag> --label sn56.repo_sha=$(git rev-parse HEAD) .
cd $KIT_ROOT/ops && SLUG=<slug> GOD_SHA=<sha> bash queue.sh plan
screen -S queue env SLUG=<slug> GOD_SHA=<sha> bash queue.sh      # STOP: touch $KIT_ROOT/STOP_queue ; now: kill -TERM <pid>
```

The trainer image build mirrors production's `client.api.build(..., nocache=True)` (`trainer/runtime.py:93-146`); that
this is the daemon's classic builder is INFERRED, hence `DOCKER_BUILDKIT=0`. Time it: production kills the build at 30
minutes (`trainer/constants.py:41`).

## Files

| file | what |
|---|---|
| `provision.sh` | idempotent box bootstrap: tools, docker, NVIDIA toolkit (opt-in apt repo), `internal_bridge` (Internal=true, `trainer/runtime.py:71-76`), dirs, GPU lock file, HF token + gated-repo probes (`GATED_REPOS`); never reboots, never prunes |
| `stage.sh` | workstation -> box: kit, archive (+ uuid links), flat api copy, ONE miner checkout at the pinned commit, plan bundle; `verify_stage.py` before and after; `BOX=local` self-test |
| `verify_stage.py` | read-only integrity check: zip CRC, evaluable test split (`image_test_data.py:70-112`), api json, anchor sha256, completeness vs `manifest.json`, checkout HEAD, kit syntax |
| `archive_layout.py` | maps `tasks/<round>_<family>_<task8>/` to `<task_id>` links without moving a byte |
| `build_eval_image.sh` | builds the public evaluator from G.O.D source at a REVIEWED `GOD_SHA`; checks the label, ComfyUI commit and library pins read from that commit |
| `warm_cache.sh` + `warm_cache.py` | runs the evaluator's own resolution code (no GPU) to fill the HF cache |
| `score_latent.sh` + `run_latent_eval.py` + `latent_tool.py` | the replica scorer: one container per repo (production shape), local LoRAs via a `local/<name>` shim, the A100 fp8 decision on other GPUs (`FP8_COMPUTE=off`), final = W*text + (1-W)*no_text exactly as `validator/scoring/tasks.py:309-329`, base-resolution and gated-base preconditions, one TSV row per model (failures are rows) |
| `fetch_artifacts.py` | pins public LoRAs at the commit that was SCORED (newest commit <= the task's `updated_at`) and downloads them with size + LFS sha256 checks |
| `anchors.sh` + `board.py` | calibrate: score the pinned anchors, compare with the official `test_loss` (delta per artefact), gaps vs TOP1 / BOSS / OURS |
| `hf_flow.py` + `hf_lib.sh` + `hf_preflight.sh` | the HF half: token file checks (0600, one token, never printed), whoami, neutral repo names, the production uploader with a reviewed-sha256 check, public + sha256 verify, fetchcheck, delete, an append-only ledger with a fail-closed end gate |
| `cells.py` + `cells.example.tsv` | the cells (queue) file format and validator (TEMPLATE rows) |
| `cell_run.sh` | ONE validator-shaped attempt of one cell (the flow at the top); `DRY=1`; `RESCORE=1` |
| `queue.sh` | the sequential queue: plan gate, file order, resume, one OPS-RERUN per cell, stop on two consecutive box-level failures, STOP file / TERM, HF end gate |
| `plan_gate.py` + `lock_cells.sh` | TEMPLATE of "no experiment without a locked plan": bind a cells file to a LOCKED plan (frozen-text hash, GO line, ledger, rows hash, pinned commit) and re-check it before GPU work |
| `hfmock/` | loopback-only fake HF hub + fake `huggingface_hub` so the REAL upstream uploader / materialize_model run in DRY and tests |
| `vendor/` | `fetch_god_vendor.sh` + `god/VENDOR.md` (what is fetched, hashes at b49ba8644a0d, how production uses it) |
| `tests/` | `test_kit.sh` (kit, ~120 checks), `test_hf_flow.sh` (HF half + cell runner + queue, ~65 checks; skips with exit 77 until vendor is fetched), `docker_stub.py`, `flock_stub.py`, `make_fixture.py`, `lib.sh` |

## Box layout (`KIT_ROOT`, default `/root/kit`)

```
ops/        this kit              data/<tourn>/   archives        api/      flat task jsons      repo/   miner checkout
plan/       cells.tsv PLAN.md FABLE_LEDGER.md     runs/<cell>/<attempt>/  per-attempt logs + checkpoint copy
latent/     hf_cache/ results_*.tsv hf_flow.tsv (HF ledger) anchors/ cells/      cache/   models/ datasets/ hf_cache/
locks/      gpu.lock cache.lock queue.lock hf_preflight.ok                         cache_manifests/  recorded HF file lists
```

## Results

- `latent/results_cells.tsv` - one row per cell attempt: status (`OK`, `FAIL_TRAIN(..)`, `FAIL_HF(..)`, `FAIL_SCORE(..)`,
  `PRECONDITION(..)`, `REFUSED(..)`), final / text / no_text means, trainer exit + timeout flag, the repo, its revision
  and the three sha256 (local == hub == fetched), the run dir.
- `latent/results_latent.tsv` - one row per scored model (score_latent.sh), `latent/results_anchors.tsv` - anchors.
- `latent/hf_flow.tsv` - every repo created, verified, kept or deleted, with UTC times (the public-exposure record).

`cell_run.sh` exit codes: 0 OK - 1 REFUSED before the GPU (retry) - 2 usage - 3 FAIL_TRAIN (a result) - 4 FAIL_HF and
5 FAIL_SCORE (ops: the queue rescores the same checkpoint once) - 6 PRECONDITION (never retried) - 130/143 interrupted.

## Known limits (be honest about them)

- Not yet run on a GPU box in this form (see the top). The CPU tests prove the control flow, the HF half (against the
  real upstream uploader / LoRA-selection code) and the failure paths - not GPU behaviour, not the production images.
- The downloader gets the archived `training_data.zip` from a loopback HTTP server (the presigned URL is long dead), with
  `--network host`; production uses the default bridge and the real URL. That the production downloader accepts a plain
  `http://` URL is INFERRED.
- The checkpoint is copied out of the stopped trainer container with `docker cp` (INFERRED to work on a named volume
  on your docker version - the first real cell proves it).
- The evaluator here gets whatever token `/root/.hf_env` holds; production gives it a read-only token. Same result for
  public repos.
- Production evaluates image tasks on A100 (`validator/evaluation/constants.py:39`). On an H100, `FP8_COMPUTE=off`
  reproduces ComfyUI's fp8 *decision* only; other kernel numerics differ. Measure your replica's error with `anchors.sh`.
- Production runs a prebuilt evaluator image; building it from source at the same commit is INFERRED equivalent.
- The arm `env` column of a cell passes env to the trainer - production sets none. A lever that only exists behind an
  env var never runs in the tournament; ship your adopted levers as defaults.
- The queue assumes one long-lived box (one GPU, one docker daemon).
