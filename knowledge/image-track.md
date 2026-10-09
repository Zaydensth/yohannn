# Gradients SN56 — Image track: how it works (public sources only)

**Snapshot.** Validator code: `gradients-ai/G.O.D`, branch `main`, commit `b49ba8644a0d705e9b035746eceaa05614742934`
(committed 2026-10-07 23:44 UTC). Public API `https://api.gradients.io` pulled **2026-10-09**.
Every `file:line` below refers to that commit. To re-pull, see [`sources.md`](sources.md).

**Labels.**
- **VERIFIED**: read in the code at the commit above, or observed in a live API/HF response on 2026-10-09.
- **INFERRED**: a reasonable conclusion that was not executed or tested here. Check it before you rely on it.

**Rule zero.** The docs teach the mechanics, but the code is the authority. Where `docs/miner.md` or `SKILL.md`
disagrees with the code, the code wins. The known disagreements are listed in §12. The image path changed
several times in Sept–Oct 2026 (§13), so **re-pull before every tournament**.

This file contains no participant identities, no scores and nobody's training recipe. It covers only how the
validator behaves.

---

## 1. Calendar and cadence

| Fact | Value | Source | Status |
|---|---|---|---|
| Image tournament start | **Monday 13:00 UTC**, weekly (`weekday()==0`, hour 13). It starts only within that hour, and a missed window waits a week | `validator/tournament/constants.py:21-22`, `validator/tournament/tournament_manager.py:1849-1909` | VERIFIED |
| Live check | R1 tasks of the latest completed image tournament were created on a Monday at 13:0x UTC | `/tournament/latest/details` → `/auditing/tasks/<id>` `created_at` | VERIFIED (2026-10-09) |
| Weekly completion target | Friday 14:00 UTC. It drives only a Discord reminder 30 min before; completion is a manual step | `constants.py:28-30`, `docs/miner.md:63-67` | VERIFIED |
| Registration lead time | Register with Fiber and post the miner IP ≥ 1 h before start | `docs/miner.md:97` | VERIFIED (doc) |
| Minimum field | 4 validated miners; otherwise it retries participant collection every 30 min | `constants.py:34`, `tournament_manager.py:983-986, 1176-1180` | VERIFIED |
| Entry fee | 0.4 TAO (400,000,000 RAO), deducted per coldkey **after** the repo passes validation | `constants.py:345`, `tournament_manager.py:971-981, 1148-1153`; live `GET /tournament/fees` | VERIFIED |
| Training attempts per task | `MAX_TRAINING_ATTEMPTS = 2` | `constants.py:41` | VERIFIED (constant; retry semantics not traced) |

---

## 2. Entry gates (all must pass, in this order)

Flow: `populate_tournament_participants`, `tournament_manager.py:961-1180`.

1. **Miner endpoint answers** `GET /training_repo/image` with `github_repo`, `commit_hash`, optional
   `github_token`. Empty repo or commit → skipped (`tournament_manager.py:1183-1209`). VERIFIED
2. **Token check**: a token that does not grant `GET api.github.com/repos/<owner>/<repo>` is silently dropped,
   so a private repo will then fail to clone (`validator/tournament/github_validation.py:180-205`). VERIFIED
3. **One entry per IP, per GitHub account (repo owner), and per coldkey**, applied in that order. Ties prefer the
   entry with a valid token, then the first one seen (`tournament_manager.py:1059-1078`; `github_validation.py:207-306`).
   VERIFIED.
   *Implication (INFERRED):* two independent competitors who answer from the **same public IP**, or who submit
   repos under the **same GitHub owner**, collide, and one of them is rejected. Use separate infrastructure and
   separate GitHub accounts.
4. **Commit hash must be a full 40-hex SHA** (`tournament_manager.py:1091-1096`). VERIFIED
5. **Anti-obfuscation**: runs the bundled binary `validator/tournament/obfuscation_detection/anti_obfuscation`
   (an ELF x86-64 Linux executable, checked with `file`) with `--repo <url> --commit <sha>`. Exit code 0 is a
   pass. A non-zero exit, an exception, or the **60-second timeout** excludes you (`github_validation.py:20-55`;
   path `constants.py:337`). VERIFIED. *Method:* run it yourself on a Linux x86-64 box against your exact commit
   before you submit.
6. **LICENSE + NOTICE** must match G.O.D's own files line by line, with only trailing whitespace stripped
   (`github_validation.py:128-160`). Accepted names: `LICENSE.md, LICENSE, license.md, license, License.md, License`
   and `NOTICE, NOTICE.txt, notice.txt, Notice.txt, notice, Notice`. **The check clones the repo without checking out
   your commit**, so it reads the **default branch** (`github_validation.py:72-77`). VERIFIED.
   *Method:* keep byte-identical copies on the default branch and never edit them.
7. Fee charged, then enrolled (`tournament_manager.py:1148-1160`). VERIFIED
8. **Before R1 trains**, hash dedup T0/T1 removes exact-commit or normalized-identical copies. They forfeit the
   fee and do not train (`tournament_manager.py:1243-1254`, `validator/tournament/dedup_gate.py:103-123`). VERIFIED.
   See §9.

Winning repos are re-uploaded to the public org `github.com/gradients-opensource`, so only submit code you are
willing to publish (`docs/miner.md:47`). The defending boss is trained from that backup repo (§3.4).

---

## 3. Tournament structure (image)

### 3.1 Round 1: a single group of three tasks
- Every non-boss participant sits in one group. The boss (`EMISSION_BURN_HOTKEY`) is **not** in R1
  (`tournament_manager.py:180-188, 1230-1233`). VERIFIED
- The group plays **3 tasks, one per family: Flux, Ideogram4, Z-Image** (`constants.py:94, 149-153`;
  `validator/tournament/task_creator.py:349-415`). VERIFIED
- R1 tasks are capped at **30 source pairs** (`constants.py:154`, `task_creator.py:369`), which means ≤ 3 held-out
  images and ≤ 0.75 h (§4). VERIFIED
- **Advancement = top 2 by average rank across the 3 tasks** (`constants.py:95`;
  `validator/tournament/round_results.py:1067-1128`, branch at `1145-1147`). The sort key is
  `(has_error, average_rank, summed_loss, hotkey)` (`round_results.py:1118-1121`). VERIFIED
  - `has_error` is true if you lack a valid score on **any** of the 3 tasks
    (`validator/tournament/models.py:242-257`). One failed task therefore puts you behind everyone who finished
    all three, whatever your other ranks. A missing task also counts as rank `len(ranked)+1`.
  - Ties on average rank are broken by the summed raw loss, then by the hotkey string.

### 3.2 Gates between R1 and R2 (they can pause the tournament)
- **T2 dedup (Claude pairwise judge)** runs over the R2 entrants. Any flag halts the tournament until a human
  approves (`tournament_manager.py:878-887`; `dedup_gate.py:129-229`). VERIFIED
- **Challenger code review ("cheat check")** of every non-boss R2 entrant. A flag halts until an operator
  eliminates or skips (`tournament_manager.py:888-920`; `validator/tournament/challenger_code_review.py:1-9`). The
  reviewer prompt targets undeclared external datasets, eval data, external pretrained models or adapters, and
  score manipulation (`validator/infrastructure/challenger_code_review_config.json`). VERIFIED

### 3.3 Round 2: one knockout task
- The two R1 qualifiers play **1 task**, with the family chosen **at random between Qwen-Image and Krea2**
  (`constants.py:157-160, 247`; `task_creator.py:114-119`). VERIFIED
- Winner: image is not a "paired" type, so the knockout falls back to `get_task_winner`, which gives the task to
  the **highest positive quality score**, i.e. the lower test loss. **There is no margin**
  (`round_results.py:422-431`; `validator/db/sql/submissions_and_scoring.py:495-508`). VERIFIED
- The R2 loser is the "3rd place" candidate for emissions (`docs/miner.md:496`). VERIFIED (doc)

### 3.4 Boss round: six tasks against the defending champion
- Distribution: **Krea2 ×2, Ideogram4 ×2, Qwen-Image ×1, Z-Image ×1** (`constants.py:163-169`;
  `task_creator.py:1144-1180`). VERIFIED
- The boss is represented by `EMISSION_BURN_HOTKEY` and trains the **previous winner's backup repo** (its latest
  commit), or the original repo if no backup exists (`validator/tournament/participants.py:130-174`;
  `tournament_manager.py:499-502`). VERIFIED. You are therefore racing the champion's *code*, retrained live on
  the same tasks.
- **Per-task win rule (image):** the challenger must beat the boss by a **relative 1%**:
  `challenger_loss <= boss_loss - abs(boss_loss) * 0.01`
  (`constants.py:263`; `validator/tournament/thresholds.py:301-312`; applied at `round_results.py:641-643, 741-750`).
  VERIFIED. The live `boss_round_performance[].threshold_used` for image tasks reads `0.01`.
- The per-sample paired bootstrap rule (55% win rate, nats dead zone) is **text only**
  (`PAIRED_BOSS_ROUND_TASK_TYPES`, `constants.py:319`). Image has **no draws** (`constants.py:321-333`). VERIFIED
- **Dethrone rule:** the challenger may lose **at most one** decided task, i.e. 5 of 6 (`required_wins = max(1, total-1)`,
  `round_results.py:359-369`). VERIFIED
- **Missing results** (`round_results.py:682-737`), all VERIFIED:
  - no valid results on a task → the boss gets it;
  - only one side trained successfully → that side gets it;
  - both trainings failed → the boss gets it;
  - both trained but only one evaluated → that side gets it;
  - both trained and both evaluations failed → the task is **skipped**, which reduces `total`.
- A boss-round task with no assigned node defers the round instead of scoring it (`round_results.py:645-667`). VERIFIED

### 3.5 Rounds that are mostly failures
If more than half the trainings of a task fail, the round **will not advance** until a human approves or resets
the trainings (`tournament_manager.py:128-135, 1637-1693`; `constants.py:340`). VERIFIED

---

## 4. How an image task is generated

`validator/tasks/synthetics/diffusion.py:704-758` and `validator/tasks/synthetics/constants.py`.

| Item | Value | Source | Status |
|---|---|---|---|
| Families in the pool | flux, z-image, qwen-image, ideogram4, krea2 (SDXL is rejected by eval) | `validator/tasks/synthetics/scheduler.py:57-63`; `validator/evaluation/docker_evaluation.py:379-380` | VERIFIED |
| Base model | drawn from a content-service pool per family; read it from the task's `model_id` | `scheduler.py:109-125`; `task_creator.py:479-483` | VERIFIED |
| Dataset categories and weights | style 0.10, person 0.10, logo 0.20, social 0.20, design 0.20, product 0.20 | `synthetics/constants.py:44-51`; picker `diffusion.py:646-649` | VERIFIED |
| Category visible in `ds` | `ds` = `<category or style name>_<trigger>_<uuid>` (e.g. `logo_…`, `product_…`, or a style name) | `diffusion.py:465-467, 740` | VERIFIED |
| Trigger word | person, logo, social, design and product sets carry a trigger (a name) that **must appear in every prompt**. Style tasks have none | `diffusion.py:182-198, 575-643`; passed as `--trigger-word` only when present (`trainer/runtime.py:232-233`) | VERIFIED |
| Image generator | FAL: `fal-ai/nano-banana-2` or `openai/gpt-image-2` (random); person and product variants use `fal-ai/nano-banana-2/edit` with a reference image | `synthetics/constants.py:81-86`; `diffusion.py:470-479, 591-602, 635-643` | VERIFIED |
| Prompt LLM | via FAL OpenRouter router (`google/gemini-2.5-flash`) | `synthetics/constants.py:77-80` | VERIFIED |
| Ideogram4 captions | every caption is rewritten into **Ideogram JSON-prompt format** (canonical key order; `aspect_ratio` and `bbox` removed) | `diffusion.py:493-547, 550-572, 724-725` | VERIFIED |
| Generated resolution | target 1024×1024 (`MIN/MAX_IMAGE_WIDTH/HEIGHT = 1024`) | `synthetics/constants.py:64-67` | VERIFIED (requested size; actual returned size not measured) |
| Pairs per task | uniform random 10–50; **R1 capped at 30** | `synthetics/constants.py:61-62`; `diffusion.py:712-717`; `constants.py:154` | VERIFIED |
| Train / test split | shuffle, then the first `ceil(10% × N)` pairs become the held-out test zip and the rest become the train zip given to miners | `validator/tasks/datasets/preparation.py:104-153`; `validator/tasks/requests.py:71-78` | VERIFIED |
| Training hours | `ceil((0.5 + (clamp(N,10,50)-10)/40 × 0.5) × 4) / 4`, plus **+0.5 h for Qwen-Image** | `diffusion.py:688-701, 719, 733-735`; `synthetics/constants.py:70-72` | VERIFIED |
| Augmentation / model-prep for image | disabled, so the base is passed by repo id and is not anonymised | `validator/tasks/prep/constants.py:8, 24`; `trainer/runtime.py:949, 1012-1015` | VERIFIED |
| GPU for training | **1×H100** for every image task | `validator/tournament/gpu_requirements.py:45-46` | VERIFIED |

Pairs → split → budget, derived from the formulas above:

| N pairs | held-out test images | train images | hours (non-Qwen) | hours (Qwen-Image) |
|---|---|---|---|---|
| 10 | 1 | 9 | 0.50 | 1.00 |
| 11–20 | 2 | 9–18 | 0.75 | 1.25 |
| 21–30 | 3 | 18–27 | 0.75 | 1.25 |
| 31–40 | 4 | 27–36 | 1.00 | 1.50 |
| 41–50 | 5 | 36–45 | 1.00 | 1.50 |

Live check (VERIFIED 2026-10-09): every image task in the latest completed tournament had `hours_to_complete`
consistent with this table for its `image_text_pairs` count.

**INFERRED consequence:** the held-out set is tiny (1–5 images), so a task's ranking can swing on a single image.

---

## 5. How the validator builds and runs your trainer

All in `trainer/`. Every step is VERIFIED unless marked otherwise.

1. **Clone** your repo and `git checkout <commit_hash>` (`trainer/host.py:67-104`).
2. **Downloader container** (`gradientsio/trainer-downloader:latest`) writes into the shared `cache` volume
   (`trainer/runtime.py:425-506`; `trainer/containers/downloader.py:557-624`):
   - dataset zip → `/cache/datasets/{task_id}_tourn.zip` (`trainer/training_paths.py:42-43`);
   - base model → `/cache/models/{model_id with / → --}`. A FLUX repo with exactly one root `.safetensors` is
     fetched as a standalone file (`downloader.py:122-145, 294-344`). `get_image_base_model_path` returns the
     single file if the folder holds exactly one (`training_paths.py:16-23`);
   - extras per family into `/cache/hf_cache`:
     - Z-Image: `ostris/zimage_turbo_training_adapter` (v2);
     - Qwen-Image: `ostris/accuracy_recovery_adapters` (`qwen_image_torchao_uint3`);
     - Ideogram4: `Qwen/Qwen3-VL-8B-Instruct` and `ostris/ideogram_4_unconditional_lora` (r16);
     - Krea2: `Qwen/Qwen3-VL-4B-Instruct`;
     - all families: CLIP tokenizers and the T5-xxl tokenizer files
     (`downloader.py:30-31, 561-624`).
3. **Dockerfile choice** (`trainer/runtime.py:896-914`; `trainer/constants.py:2-12`):
   - **Flux tasks** build `ops/docker/standalone-image-trainer.dockerfile`, or the legacy
     `dockerfiles/standalone-image-trainer.dockerfile`.
   - **All other families** build `ops/docker/standalone-image-toolkit-trainer.dockerfile`, or the legacy
     `dockerfiles/…toolkit…`.
   - A missing file raises `FileNotFoundError`, so that task fails.
   - **G.O.D main itself ships only the toolkit Dockerfile** (`ls ops/docker` at this commit), and
     `docs/miner.md:244-252` mentions only the toolkit file. See §12.
4. **Build**: docker-py `client.api.build(path=<repo root>, dockerfile=…, nocache=True)`, **30-minute timeout**.
   The image is deleted after the task (`runtime.py:93-164`; `constants.py:41`).
   **INFERRED:** docker-py's low-level build does not drive BuildKit. Test your Dockerfile with
   `DOCKER_BUILDKIT=0 docker build --no-cache …` and avoid BuildKit-only syntax. A cached layer never helps you here.
5. **Run** (`runtime.py:199-296`):
   - CLI args: `--task-id --model --dataset-zip --model-type --expected-repo-name --hours-to-complete [--trigger-word]`.
     `--dataset-zip` is the original S3 URL. Read the pre-downloaded cache path instead
     (`docs/miner.md:649`).
   - Environment: `TRANSFORMERS_CACHE=/cache/hf_cache`, `HF_HOME=/cache/hf_cache`, plus the trainer host's
     HF variables (`runtime.py:58-68, 235-239`). `BASELINE_STATS_PATH` appears only if model-prep ran, which it
     does not for image.
   - Mounts: `checkpoints` volume → `/app/checkpoints/` **rw**; `cache` volume → `/cache` **ro**.
   - Resources: the assigned GPU ids only; RAM `110g × #GPU`; `24 × #GPU` CPUs; `shm_size` 8g (16g at ≥4 GPUs);
     `no-new-privileges`; `cap_drop=ALL`; network = Docker bridge created with `internal=True`, so there is no
     internet (`runtime.py:71-90, 250-280`; `constants.py:34-35`).
6. **Wall clock**: the clock is `hours_to_complete × 3600` s and starts **when the container starts**, so the
   download and build time are not charged to it. On timeout the container is **killed** and the output is
   **still uploaded** (`runtime.py:927, 1061-1092`). A **non-zero exit code fails the task with no upload**
   (`runtime.py:1076-1085`).
7. **Upload** (`gradientsio/trainer-uploader:latest`; `trainer/containers/uploader.py:155-242`;
   `runtime.py:1138-1158`):
   - Source folder: `/app/checkpoints/{task_id}/{expected_repo_name}` (`training_paths.py:13-14`).
   - **Trap:** if that folder contains a **sub-directory holding any `.safetensors`**, only that sub-directory is
     uploaded, and the first such directory `os.listdir` returns wins (`uploader.py:136-152, 179-181`).
   - An empty or metadata-only folder is an error (`uploader.py:80-99, 183-184`).
   - Destination: public HF repo `gradients-io-tournaments/{expected_repo_name}`, into subfolder **`checkpoints/`**
     for image tasks. `delete_patterns=["*"]`; optimizer, scheduler and RNG state files are ignored
     (`runtime.py:1140`; `uploader.py:190-216`; `core/constants/credentials.py:14`).
8. Scoring looks the repo up at `gradients-io-tournaments/<expected_name>`. A missing repo scores
   "Model repo not found on HuggingFace" (`validator/scoring/tasks.py:612-622`).

The local example runner `ops/examples/training/run_image_task.sh` does **not** mirror production. It mounts the
cache rw, uses `--network none`, `--memory=32g` and `--cpus=8`, and always builds the toolkit Dockerfile
(`run_image_task.sh:33, 51-67`). VERIFIED. Rebuild your own harness from §5 instead.

---

## 6. Evaluation metric (current: latent flow-matching velocity MSE)

Introduced by PR #1369 ("add latent space eval", 2026-09-21), the latest change to the evaluator.
Code: `validator/evaluation/evaluators/diffusion.py`, `denoising_mse.py`, `image_denoising.py`,
`image_flow_adapter.py`, `image_artifacts.py`, `image_test_data.py`. All VERIFIED unless marked.

**Pipeline per candidate repo:**

1. **Test images**: from the held-out zip. Each image is resized so its **long side = 1024** (LANCZOS) and
   centre-cropped to multiples of 16. **Every image needs a non-empty caption**
   (`image_test_data.py:59-67, 101-112`).
2. **Encode once with the base family VAE** (posterior mean, fp32, never patched by your LoRA). Z-Image uses the
   diffusers `AutoencoderKL` from the task repo's `vae/` subfolder at the pinned revision; the other families use
   the Comfy VAE in the table below (`diffusion.py:45-67, 98-106`; `image_encoder.py`; `image_flow_adapter.py:54-70, 98-107`).
3. **Cases**: **16 noise levels × 16 noises = 256 cases per image** (`DEFAULT_STRATA=16`, `DEFAULT_NOISES=16`,
   `diffusion.py:41-42`). Sigmas are the band midpoints `(b+0.5)/16`. Each noise seed is
   `sha256("42:<image_hash>:<stratum>:<noise_idx>")`, so the cases are identical for every competitor
   (`denoising_mse.py:12-39`). `noisy = (1-σ)·x0 + σ·ε`; target velocity `= ε − x0`.
4. **Loss**: `mean((v_pred − (ε − x0))²)` over latent elements, where `v_pred = (noisy − denoised)/σ`
   (`denoising_mse.py:42-52`). A non-finite loss raises an error.
5. **Two conditions**: `text` (the caption) and `no_text` (the empty string `""`), both run through **your**
   LoRA-patched text encoder. CFG = **1.0**, no negative conditioning (`diffusion.py:125-149`). Flux conditioning
   carries `guidance=3.5`. Qwen-Image applies `ModelSamplingAuraFlow` shift 3.0. The model must be unit-noise
   rectified flow or eval refuses (`image_flow_adapter.py:109-128`).
6. **Repeatability guard**: the first case is re-run, and if it is not equal within `rtol 1e-5, atol 1e-7` the
   evaluation fails (`diffusion.py:160-169`).
7. **Per-task score** = `0.5 × mean(text losses) + 0.5 × mean(no_text losses)`. **Lower is better.**
   (`validator/scoring/tasks.py:309-329`; `validator/evaluation/constants.py:6`).

**Family contracts** (`image_flow_adapter.py:23-51`; base selection `image_artifacts.py:80-98`):

| family | text encoder(s) | VAE | base weights used by eval | extra |
|---|---|---|---|---|
| flux | `comfyanonymous/flux_text_encoders`: t5xxl_fp16 + clip_l | `rayonlabs/FLUX.1-dev` ae | the task `model_id` repo's **single root-level `.safetensors` > 5 GiB** | guidance 3.5 |
| z-image | `Comfy-Org/z_image_turbo` qwen_3_4b | diffusers VAE from the task repo | same rule as flux | — |
| qwen-image | `Comfy-Org/Qwen-Image_ComfyUI` qwen_2.5_vl_7b fp8_scaled | qwen_image_vae | same rule | shift 3.0 |
| krea2 | `Comfy-Org/Krea-2` qwen3vl_4b fp8_scaled | qwen_image_vae | same rule | — |
| ideogram4 | `Comfy-Org/Ideogram-4` qwen3vl_8b fp8_scaled | flux2-vae | **always** `Comfy-Org/Ideogram-4/diffusion_models/ideogram4_fp8_scaled.safetensors` | — |

**Which checkpoint is scored** (`image_artifacts.py:36-46`): among files under `checkpoints/` (or `checkpoint/`)
ending in `.safetensors`:
1. exactly one `last.safetensors` → that file;
2. otherwise the **highest** `[-_]<number>.safetensors`;
3. otherwise the only file, if there is exactly one;
4. otherwise the evaluation fails ("No unambiguous LoRA checkpoint").

**LoRA loading is strict** (`image_flow_adapter.py:130-154`). Comfy `LoraLoader` loads at strength 1.0 for both
the model and the text encoder. **Any** "lora key not loaded" / "NOT LOADED" log line, or zero model patches, makes
the evaluation fail with "Incomplete LoRA application". Which key naming schemes load is decided by ComfyUI at the
pinned commit `694815f498295080a0e15a1502edc9dba841b110` (`ops/docker/validator-diffusion.dockerfile:4`).
*Method:* run the evaluator image on your own artifact before trusting a key format (INFERRED that this is the
only reliable check).

**Where and how it runs**: image `gradientsio/image-evaluator:basilica` (`core/constants/docker.py:2`), on
Basilica `A100` GPUs with ≥ 80 GB, **one deployment per submitted repo** (`docker_evaluation.py:361-428`;
`validator/evaluation/constants.py:39-40`). Eval-side stack: Python 3.11, torch 2.9.1/cu128
(`validator-diffusion.dockerfile:1-20`). Training runs on H100 (§4), while eval runs on A100.

**Gated base**: `krea/Krea-2-Raw` reports `gated: "auto"` on the HF API (VERIFIED 2026-10-09). The
`gradients-io-tournaments/*` bases and `Comfy-Org/Ideogram-4` / `Comfy-Org/Krea-2` report `gated: false`. To
reproduce a krea2 eval locally you need an HF token that has requested access (INFERRED from `auto` gating).

**Local re-evaluation tool**: `python -m ops.validator_ops.run_evaluation --task_id <TASK_ID> [--models <HF_REPO>]`
after building `ops/docker/validator-diffusion.dockerfile` (`docs/miner.md:566-575`). VERIFIED (doc + file exists).

---

## 7. Ranking within a task and the scores written to the API

`validator/scoring/tasks.py:115-241`, constants `validator/scoring/constants.py:4, 11-12`. VERIFIED.
- Invalid submissions are scored first:
  - `is_finetune=False` (repo == base model, `scoring/tasks.py:420-431`) → "Non-finetuned submission";
  - NaN loss → "Invalid test loss";
  - any evaluation exception → "Evaluation failed: …".
- Valid submissions are sorted by `test_loss` ascending. Rank 1 gets `FIRST_PLACE_SCORE = 3`.
  - With **> 8 valid miners**, the bottom 25% get `SCORE_PENALTY = -1`.
  - Everyone else gets 0.
  - Failed submissions also get -1 when at least one valid submission exists.
- `score_reason` strings in the API: `Ranked 1st by test_loss`, `Ranked below top 1 by test_loss`,
  `Bottom 25% ranked by test_loss`, `Evaluation failed: …`, `Non-finetuned submission`,
  `Model repo not found on HuggingFace`, `No expected repo name found`.

---

## 8. Common ways to score zero (from the code paths above)

| Failure | Where it bites | Source |
|---|---|---|
| Flux Dockerfile path missing | build fails before training → no upload | `runtime.py:896-914` |
| Build > 30 min, or the build uses BuildKit-only features (INFERRED) | no image → task failed | `runtime.py:93-147` |
| Container exits non-zero, even after writing a good checkpoint | **no upload** | `runtime.py:1076-1085` |
| Writing to `/cache` (read-only) or needing internet | crash | `runtime.py:264-277` |
| Extra sub-folder containing `.safetensors` inside the output dir | only that sub-folder is uploaded | `uploader.py:136-152, 179-181` |
| Ambiguous checkpoint names under `checkpoints/` | eval error | `image_artifacts.py:36-46` |
| Any LoRA key Comfy does not map | eval error | `image_flow_adapter.py:130-154` |
| Non-finite prediction, or a non-repeatable forward pass | eval error | `denoising_mse.py:50-51`; `diffusion.py:160-169` |
| One of the three R1 tasks fails | `has_error` → sorted behind every full finisher | `models.py:255-257`; `round_results.py:1118-1121` |
| Not handling every family in the tournament | that task fails | `docs/miner.md:648` |

---

## 9. Duplicate and "same submission" control

`validator/infrastructure/repo_dedup.py`, `validator/tournament/dedup_gate.py`, `validator/infrastructure/repo_dedup_config.json`.
All VERIFIED.

- **T0**: identical commit hash.
- **T1**: identical source after stripping whitespace, comments and ordering. T0 and T1 run **before R1** and
  auto-eliminate; the fee is forfeited (`repo_dedup.py:6-10`; `tournament_manager.py:1243-1254`).
- **T2**: a Claude pairwise judge (model set in `constants.py:137`) runs at R1→R2 and halts for human review
  (`dedup_gate.py:129-229`). Under the judge's rubric (`repo_dedup_config.json`, `system_prompt`):
  - The rubric counts as **cosmetic**: renames, reformatting, dead code, **parameter tuning of existing knobs
    however many**, re-parametrising a knob (constant ↔ lookup table), and "differentiators" that trigger only on
    inputs never present at training time (unset env vars, absent baseline-stats fields, ignored config keys).
  - It counts as **genuine** only a *new mechanism that executes at training time* (for example an LR finder,
    model soup, a new scheduling or packing scheme, early stopping, a different checkpoint-selection method, or a
    true bug fix).
  - When in doubt it leans "duplicate". A duplicate of the **boss** is dropped and the boss is kept
    (`repo_dedup.py:184-193`).
- **Pre-submit self-check (public tool):**
  `python -m ops.tools.tournament.dedup_check <repo_a_url> <repo_b_url> --hash-a <sha> --hash-b <sha>`
  (needs `ANTHROPIC_API_KEY`; `docs/miner.md:622-635`; file `ops/tools/tournament/dedup_check.py` exists).
  Run it against the current boss backup repo and against any public baseline you started from.

---

## 10. Emissions (short)

`validator/scoring/constants.py`, `docs/miner.md:474-500`. VERIFIED.
- Image anchor base weight 0.25, cap 0.50 (`scoring/constants.py:28, 35`). A participation-driven balancer moves
  weight between track types, and the types always sum to 1.0.
- Top 3 ranks of a tournament are paid with decay base 0.25 (about 76/19/5), or 80/20 when no clean 3rd exists.
  Image 3rd = loser of R2 (`scoring/constants.py:24-25`; `docs/miner.md:488-500`).
- Every active participant gets 0.0001 (`scoring/constants.py:23`). Runner-up shares last about one cycle
  (`RUNNER_UP_EMISSION_DAYS = 7`, `tournament/constants.py:14`).
- A champion's allocation is boosted when boss-round performance exceeds 0.10: the excess × 2.0, capped
  (`scoring/constants.py:53-54`).

---

## 11. Public API: what you can read without a key

Paths below are the live ones on `https://api.gradients.io` (`/openapi.json` lists them). Validator-internal
`/v1/tournaments/*` analytics routes return 404 publicly. VERIFIED 2026-10-09.

| Endpoint | Use | Shape (field names only) |
|---|---|---|
| `GET /tournament/latest/details` | latest text/image/environment tournament | `{text,image,environment}` → `tournament_id, status, base_winner_hotkey, winner_hotkey, participants[{hotkey, eliminated_in_round_id, final_position}], rounds[{round_number, round_type(group/knockout), is_final_round, status, participants, tasks[{task_id, group_id, pair_id, winner, participant_scores[{hotkey, quality_score, test_loss, synth_loss, score_reason}], task_type}]}], final_scores, boss_round_performance[{task_id, boss_score, challenger_score, threshold_used, performance_difference, challenger_won, paired_comparison, decision_reason}]` |
| `GET /tournament/{tournament_id}/details` | the same for a given tournament | same |
| `GET /auditing/tasks/{task_id}` | full task record | `model_id, model_type, ds, hours_to_complete, trigger_word, image_text_pairs[{image_url,text_url}], training_data, test_data, created_at, started_at, termination_at, completed_at, trained_model_repository, training_repo_backup, hotkey_details[{hotkey, rank, test_loss, synth_loss, quality_score, repo, score_reason, submission_id, offer_response}], miner_scores` |
| `GET /auditing/tasks` | recent tasks (list of 100 at pull time) | task records |
| `GET /auditing/tasks/hotkey/{hotkey}` | tasks for one hotkey | task records |
| `GET /auditing/scores-url` | pointer to a scores dump | — |
| `GET /tournament/fees` | fees in RAO | `{text_tournament_fee_rao, image_tournament_fee_rao, environment_tournament_fee_rao}` |
| `GET /tournament/balance/{coldkey}` | your tournament balance | — |
| `GET /v1/performance/last-boss-battle` | per-type boss-battle differences | `{image_tournament_id, image_performance_differences, …}` |
| `GET /v1/performance/latest-tournament-weights` | current weight split | `{burn_data, image_top_miners, …}` |

Facts about the payloads (VERIFIED 2026-10-09):
- Per-miner results live in **`hotkey_details`**. `miner_scores` was `null` on all 10 image tasks of the latest tournament.
- **`test_data` is a presigned S3 URL with `X-Amz-Expires=604800` (7 days)**, seen on every image task of the
  latest tournament. It is the real held-out set, so download it inside the window if you want it for offline
  replay. Treat these URLs as bearer links and never commit or publish them.
- `image_text_pairs` lists all generated pairs (train and test).
- `hotkey_details[].repo` is the uploaded HF repo under `gradients-io-tournaments/`. That is where you can fetch
  any competitor's scored LoRA for forensics.
- `termination_at = created_at + hours_to_complete` (10/10 tasks). Each miner's training clock starts later, when its container starts (§5.6).

---

## 12. Docs vs code: disagreements (the code wins)

1. **Flux Dockerfile.** `docs/miner.md:244-252` says image needs only the toolkit Dockerfile. The code builds
   `ops/docker/standalone-image-trainer.dockerfile` (or `dockerfiles/standalone-image-trainer.dockerfile`) for
   **flux** tasks (`trainer/runtime.py:898-901`, `trainer/constants.py:2, 6`), and G.O.D main does not contain
   that file. VERIFIED. *Method:* ship both paths.
2. **Start day.** `SKILL.md:343-345` says text/image tournaments start on Thursdays. The code says Monday
   (`constants.py:19-22`, `tournament_manager.py:1898-1909`). The live R1 tasks were created on a Monday. VERIFIED.
3. **Clone URL.** `docs/miner.md:141` (and `DEFAULT_PARTICIPANT_REPO`, `constants.py:125`) use
   `github.com/rayonlabs/G.O.D`. On 2026-10-09 that URL **301-redirects to `gradients-ai/G.O.D`**. VERIFIED. Use
   the `gradients-ai` path.
4. **Local runner** `ops/examples/training/run_image_task.sh` differs from production in its mounts, network,
   resources and Dockerfile (§5). VERIFIED.

---

## 13. Volatility: recent commits touching the image path

From `GET api.github.com/repos/gradients-ai/G.O.D/commits?path=…` (VERIFIED 2026-10-09):

| date | commit | title | touches |
|---|---|---|---|
| 2026-09-27 | `17a2723` | 3 task round 1 image (#1387) | R1 format, R1 30-pair cap, docs |
| 2026-09-22 | `0791bb6` | Drop the forced pre-boss model… (#1385) | tournament constants, docs |
| 2026-09-21 | `61c4d7d` | add latent space eval (#1369) | **image metric** |
| 2026-09-21 | `8bf2960` | eval logging (#1384) | evaluator logging |
| 2026-09-21 | `a29ae09` | Skip dup gate (#1382) | dedup gate |
| 2026-09-15 | `377ed38` | Enhance model preparation and publishing logic (#1374) | trainer runtime |
| 2026-08-17 | `10aeda4` | Fix trainer reporting SUCCESS before upload on training timeout | timeout → upload path |
| 2026-08-10 | `a91bd6f` | add docker build timeouts (#1348) | 30-min build cap |

The image format and metric changed twice in the two weeks before this snapshot. Diff `validator/tournament/`,
`validator/evaluation/`, `validator/tasks/synthetics/` and `trainer/` against the previous pull before each Monday
(the method is in `sources.md`).
