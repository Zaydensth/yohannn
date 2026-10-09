# Gradients SN56: the text tournament track (public sources only)

> **Pulled:** 2026-10-09, about 05:45 UTC.
> **Code:** `gradients-ai/G.O.D`, branch `main`, at commit `b49ba8644a0d705e9b035746eceaa05614742934`
> (committed 2026-10-07T23:44Z, "Fix/70b runpod eval path (#1393)").
> **Live API:** `https://api.gradients.io` (probed 2026-10-09).
>
> In this file, `path:line` means a path in G.O.D at that commit.
> **VERIFIED** means the claim was read in code or docs, or seen in a live API response, on the pull date.
> **INFERRED** means a reasonable conclusion that has not been tested.
> **Code beats docs.** Section 12 lists the places where the two disagree.
>
> This file holds no participant data: no hotkeys, no scores, no rankings, no recipes.
> The numbers here are validator constants, or simple arithmetic on those constants.

---

## 0. Summary

1. **Text tasks come in five types.** The `TaskType` enum lists `InstructTextTask`, `DpoTask`,
   `GrpoTask`, `ChatTask` and `EnvTask` (`core/models/task_models.py:37-43`). Text tournaments
   use the first four. `ChatTask` appears only as the continuous-SFT task in the boss round. VERIFIED.
2. **A text tournament has three rounds.** VERIFIED, both in code and in the live
   `/tournament/latest/details` response on 2026-10-09:
   - **R1:** one group containing every entrant. It plays 3 instruct tasks on models of 4B parameters or fewer.
   - **R2:** a one-task knockout between the top 2 from R1.
   - **R3:** the boss round, the R2 winner against the defending champion's code. It has 6 tasks:
     3 instruct (one of them on a 30–72B model), 1 DPO, 1 GRPO and 1 continuous-SFT chat task.
3. **You submit code, not models.** The validator clones your repo at a pinned commit, builds your
   Dockerfile with no cache, and runs it on its own GPUs for `hours_to_complete`. It then uploads
   whatever is at `/app/checkpoints/{task_id}/{expected_repo_name}` and evaluates it. VERIFIED.
4. **A non-zero exit code means no upload, so the task fails.** A wall-clock timeout kills the
   container but still uploads what exists. VERIFIED, `trainer/runtime.py:1070-1092,1138-1160`.
5. **Ranking metric by task type:**
   - Instruct, chat and DPO rank on held-out loss (lower is better).
   - GRPO ranks on a reward score (higher is better). That score is normalised across the repos
     evaluated together, minus a KL penalty. VERIFIED.
6. **Head-to-head instruct, DPO and chat tasks are decided per held-out example, not on the mean.**
   This applies to knockout and boss-round tasks. VERIFIED, `validator/tournament/thresholds.py`.
7. **To dethrone the champion,** you must win at least (tasks − 1) of the boss-round tasks **and**
   win or draw every continuous-SFT task. VERIFIED, `validator/tournament/round_results.py:313-420`.
8. **The base model you receive can differ from its Hugging Face original.** It may be deliberately
   perturbed (50% chance, for bases under 35B). Its identity is hidden behind a salted hash
   directory, with `_name_or_path` scrubbed from the config. VERIFIED, sections 3.4 and 5.4.
9. **Duplicates are eliminated.** R1 removes hash-identical code, including copies of the
   champion's code. Before R2, Claude judges each pair of entrants and a human approves the
   eliminations. VERIFIED, section 11.

---

## 1. Task types and what each trains on

| Type | Where it appears in a text tournament | Dataset payload (`--dataset-type`) | Ranking direction |
|---|---|---|---|
| `InstructTextTask` | R1 (all 3 tasks), R2 (75% chance), boss (3 tasks) | instruction/input/output column names | lower loss is better |
| `DpoTask` | R2 (20% chance), boss (1 task) | prompt/chosen/rejected | lower loss is better |
| `GrpoTask` | R2 (5% chance), boss (1 task) | prompt field plus generated reward functions and weights | higher score is better |
| `ChatTask` | boss only, as continuous-SFT | ShareGPT-style conversation columns, `chat_template="tokenizer_default"` | lower loss is better |
| `EnvTask` | environment tournament, not text | `environment_names` | higher score is better |

Sources (all VERIFIED):
- Enum: `core/models/task_models.py:37-43`.
- Payload schemas: `docs/miner.md:317-325`.
- Ranking direction: `validator/scoring/tasks.py:142-176` and `validator/tournament/round_results.py:1193`.
- R2 probabilities: `validator/tasks/synthetics/constants.py:9-18` and `validator/tournament/task_creator.py:734-790`.
  The split is instruct 0.75, DPO 0.20, GRPO 0.05 (GRPO is the remainder, `1 - 0.75 - 0.20`).

**GRPO tasks** draw 2 to 4 generic reward functions from templates. Their weights are random
draws from U(0.1, 10), normalised to sum to 1 (`validator/tasks/synthetics/scheduler.py:513-545`,
`validator/tasks/synthetics/constants.py:110-111`). The reward-function code is passed inside
`--dataset-type` (`docs/miner.md:327`). GRPO datasets need at least 20,000 rows
(`validator/tasks/datasets/constants.py:49`, `validator/tasks/synthetics/scheduler.py:566`). VERIFIED.

---

## 2. Tournament structure

### 2.1 Calendar

- **Weekly start window.** Text tournaments start on **Monday during the 11:00 UTC hour**
  (`validator/tournament/constants.py:19-20`; weekday 0 = Monday, `tournament_manager.py:1898-1912`).
  A new tournament starts only inside that hour, and only when the previous one of the same
  type has completed. A missed window waits a full week (`tournament_manager.py:1849-1895`,
  `docs/miner.md:59-61`). VERIFIED.
- **Expected finish.** Every tournament is expected to finish by **Friday 14:00 UTC**. Completion
  is a manual step that follows a human review of the winning code (`constants.py:24-30`,
  `docs/miner.md:63-67`). VERIFIED.
- **`TOURNAMENT_INTERVAL_HOURS = 120` does not schedule anything.** It is used only in analytics
  endpoints (`constants.py:10`; it is referenced only in `validator/endpoints/tournament_analytics.py:441,464,500`). VERIFIED.
- **Minimum field.** A text tournament needs 4 validated miners. If fewer validate, the validator
  waits 30 minutes and retries (`constants.py:34`, `tournament_manager.py:1169-1180`). VERIFIED.

### 2.2 Round table

| Round | Format | Tasks | Model pool | KL on instruct | Base perturbation ("augmentation") |
|---|---|---|---|---|---|
| R1 | One group, every entrant | 3 × instruct | 0.1–4B. Exactly one of the 3 uses a 2026-release model of 4B or fewer from `core/oversampled_later_models.json`. | **off** | possible (50%) |
| R2 | Knockout pair (R1's top 2) | 1, drawn as instruct 75% / DPO 20% / GRPO 5% | 0.1–12B | instruct: 20% chance, coefficient U(0.1, 1.5) | possible |
| R3 (boss) | R2 winner vs the defending champion's code | 6: 3 instruct + 1 DPO + 1 GRPO + 1 continuous-SFT chat | One instruct slot is always 30–72B. The other slots are 0.1–12B. Continuous-SFT uses the lineage's carried model. | instruct: 20% chance | possible, except on the 30–72B slot and the continuous-SFT task |

Sources (all VERIFIED):
- Round organisation: `validator/tournament/tournament_manager.py:180-204` and `:524-598`.
- R1: `validator/tournament/task_creator.py:587-627`. The model range is set at `:595`, KL is turned off at `:620`, and the oversampled slot is chosen at `:599-605` and `:673-684`.
- R2 and other knockouts: `task_creator.py:734-790`. The default model range is set at `validator/tasks/synthetics/scheduler.py:87-88`.
- Boss: `task_creator.py:923-1018` and `constants.py:174-205`.
- KL draw: `scheduler.py:74-84` and `synthetics/constants.py:20-22`.
- Augmentation: section 3.4.

**How the rounds chain together.** R1 always advances exactly `SMALL_TOURNAMENT_ADVANCE = 2`
(`constants.py:94-95`, `round_results.py:1122`). Two winners form one knockout pair. The single
R2 winner is then paired with the champion placeholder, which makes the next round the final
(`tournament_manager.py:535-555`). VERIFIED.

**What "the boss" is.** The defending champion takes part through its *code*. The participant
uses the last winner's repo, a re-uploaded backup copy if one exists, at its commit. Its trainer
runs on the same fresh boss-round tasks as the challenger (`validator/tournament/participants.py:130-176`).
So the challenger is retraining against the champion's training code on the same tasks.
VERIFIED. INFERRED consequence: there is no fixed old model to beat; the comparison is code against code, trained this week.

**Large-instruct slot.** The last instruct slot of the boss round always draws from 30–72B. It is
never augmented, because republishing a 70B model would overrun the prep timeout
(`task_creator.py:977-1003`, `constants.py:180-185`). VERIFIED.

**Continuous-SFT task.** This is the `ChatTask` with `TrainingStartPoint.CONTINUOUS_SFT`
(`constants.py:190-238`, `scheduler.py:809-859`). VERIFIED:
- **Lineage.** There is one lineage today, `qwen3-14b`, seeded from `Qwen/Qwen3-14B`.
- **Starting model.** Each tournament trains the next data chunk, starting from that lineage's
  previous winner, or from the seed on the first run.
- **Data.** The content service supplies fresh train/test URLs every time.
- **Fixed settings.** It always runs on 4×H100, with no augmentation and
  `chat_template="tokenizer_default"`. Evaluation pins the tokenizer and chat template to the
  seed model (`validator/evaluation/evaluators/instruct_text.py:452-455`).
- **LoRA bases are flattened.** If the carried base is a LoRA adapter, model-prep publishes a flat
  merged copy, and training and eval use that copy (`trainer/model_prep/README.md`, section "LoRA continuation bases").

### 2.3 Training attempts and failure gates

- **Retries.** Training is attempted at most `MAX_TRAINING_ATTEMPTS = 2` times per task and
  hotkey, then marked failed (`constants.py:41`, `validator/tournament/orchestrator.py:486-496`). VERIFIED.
  INFERRED: these are scheduler-level retries, for example when a GPU or container fails to start.
  They are not a second chance for code that exits non-zero.
- **Majority-failure gate.** If more than 50% of a task's trainings fail, the round is held until
  a human approves (`constants.py:340`, `tournament_manager.py:128-135,1637-1693`). VERIFIED.

---

## 3. How models and datasets are drawn

### 3.1 Models
- **Pool and size bands.** Models come from the content service `GET /models/random`, filtered
  by parameter count (`validator/tasks/synthetics/scheduler.py:87-106`,
  `validator/infrastructure/service_constants.py:36-37`). The size bands are listed in section 2.2. VERIFIED.
- **Oversampled 2026 models.** The pool in `core/oversampled_later_models.json` is described in
  its own comment as ungated, safetensors-only, no `auto_map`, text-only and ≤4B. That pool is
  used only for the one R1 slot. `FINAL_ROUND_OVERSAMPLED_TASKS = 0` turns off the boss-round
  slot (`constants.py:186-188`). VERIFIED.
- **No custom code.** Eval and model-prep load miner repos with `trust_remote_code=False`
  (`constants.py:196-197`, `validator/evaluation/model_checks.py:51,71`). VERIFIED.

### 3.2 Datasets
- **Three size bins, sampled round-robin.** Instruct and DPO datasets come in equal turns from the
  bins 8k–40k, 40k–90k and 90k–175k rows (`validator/tasks/datasets/constants.py:12-22`,
  `scheduler.py:133-212`). The content service ignores row-range parameters, so the validator
  filters by row count on its own side (`scheduler.py:154-156`). VERIFIED.
- **Hard row limits.** A dataset must have 8,000–175,000 rows in total, or task prep fails
  (`validator/tasks/datasets/preparation.py:606-615`). VERIFIED.
- **Rejected datasets.**
  - **Degenerate history (last 90 days):** the best historical test_loss is below 0.01
    (below 0.05 for DPO), or the best instruct loss is above 2.0, or the best DPO loss falls in
    [0.68, 0.71], which is chance level around ln 2 (`scheduler.py:335-380`,
    `synthetics/constants.py:97-106`).
  - **Near-duplicates:** model-prep measured a near-duplicate rate of 0.10 or more in the last
    180 days (`scheduler.py:383-403`, `constants.py:56-58`). VERIFIED.
- **Held-out split.** A task without an explicit test file gets a held-out set of 10% of rows,
  capped at 1,000 rows, using `train_test_split(shuffle=True, seed=42)` (`preparation.py:90-101`,
  `datasets/constants.py:4-5`). Continuous-SFT tasks get separate train and test files from the
  content service, so this cap does not apply to them (`preparation.py:507-525`, `scheduler.py:822-835`). VERIFIED.

### 3.3 YaRN context extension
- **YaRN is wired but switched off:** `YARN_EXTENSION_PROBABILITY = 0.0`, with factors [2, 4]
  (`validator/tasks/prep/constants.py:19-20`). VERIFIED. When it is on, the extended value is
  written into the base model you receive (`model_checks.py:130-131`).

### 3.4 Base-model augmentation (the base you get may be perturbed)
- **Probability.** For instruct, DPO and GRPO tasks, the validator flips a 50% coin for whether
  to republish a perturbed copy of the base. Bases of 35B or more are skipped
  (`validator/tasks/prep/constants.py:23-29`, `augmentation.py:34-58`, `scheduler.py:444-457`). VERIFIED.
- **Perturbation types and intensity ranges** (`prep/constants.py:31-50`). VERIFIED.

  | Type | Weight | Intensity range |
  |---|---|---|
  | Gaussian noise | 0.20 | 0.01–0.3 |
  | Weight scaling | 0.40 | 0.5–1.5 |
  | Magnitude pruning | 0.25 | 0.25–0.50 |
  | Layer re-init | 0.15 | 0.01–0.10 |

- **Scope** is one of: a single layer (0.10), a layer-type group (0.15), multiple layers (0.35),
  or all layers (0.40).
- **What this means for a trainer.** INFERRED: the starting point may be damaged, so a trainer
  should not assume the base is a clean pretrained checkpoint. The evaluator compares your model
  only against other submissions trained from the same base. Augmentation is not applied to
  continuous-SFT tasks or to the forced 30–72B slot (section 2.2).

---

## 4. Time and GPU budget

### 4.1 `hours_to_complete`, before model-prep
`validator/tasks/synthetics/scheduler.py:235-284`, with constants from `validator/tasks/datasets/constants.py:24-48`. VERIFIED.

```
per_gpu_tok_s = 989e12 * 0.15 / (6 * N_params)               # H100 bf16 peak × assumed MFU 15%
train_s       = 2.0 * tokens_per_epoch * type_mult / (per_gpu_tok_s * gpus)   # target 2 epochs
hours         = max(0.75, ceil((train_s/3600 + 0.75) * 4) / 4)                # 0.75 h overhead, 15-min steps
hours         = min(hours, 5.0)
```

- **Inputs before prep.** `tokens_per_epoch = rows × 400`, and `N_params` defaults to 8e9 if
  unknown. `type_mult` is 1.0 for instruct and chat, and 1.4 for DPO.
- **GRPO is step-budgeted, not token-budgeted.** Its hours depend only on model size: 1.5 h for
  ≤4B, 2.5 h for ≤12B, 4.0 h for ≤40B, then 6.0 h, which the 5.0 h cap clips
  (`datasets/constants.py:43-48`, `scheduler.py:269-273`). VERIFIED.
- **After model-prep, hours are recomputed** from the measured token count. The effective count
  is at least `rows × 64`, and the throughput used is the measured value, clamped to 0.33×–3×
  the analytic estimate (`scheduler.py:287-323`, `datasets/constants.py:31-35`). VERIFIED.
- **Worked example** (arithmetic only, using the constants above): an 8B instruct task on 40k
  rows, without KL, gets 2×H100. 2 × 16M tokens at about 3,090 tok/s/GPU × 2 GPUs ≈ 1.44 h,
  plus 0.75 h ≈ 2.19 h, which rounds up to **2.25 h**. The post-prep resize can change this.
- **Live cross-check.** On 2026-10-09, `GET /auditing/tasks` showed `hours_to_complete` values
  that were all multiples of 0.25 h. GRPO tasks showed 1.5 h and 2.5 h by model size, and a 70B
  instruct task showed 5.0 h. All of this is consistent with the formula. VERIFIED.

### 4.2 GPUs per task
`validator/tournament/gpu_requirements.py:16-105`. VERIFIED.

```
eff_params_B = params_B × (3 if DPO, 2 if GRPO, 1 otherwise) × (2 if instruct uses KL)
GPUs = 1×H100 if eff ≤ 4 ; 2× if ≤ 12 ; 4× if ≤ 40 ; else 8×
continuous-SFT: always 4×H100
```

**Container limits:** 110 GB RAM and 24 CPUs per GPU (`trainer/constants.py:34-35`,
`trainer/runtime.py:79-90`). Shared memory is 16 GB with 4 or more GPUs, otherwise 8 GB
(`runtime.py:364`). VERIFIED.

---

## 5. Runtime contract: what the validator does with your repo

### 5.1 Build
- **Dockerfile location.** One of `ops/docker/standalone-text-trainer.dockerfile` or the legacy
  `dockerfiles/standalone-text-trainer.dockerfile`. The build context is the repo root
  (`trainer/constants.py:1-12`, `trainer/runtime.py:896-915,973-986`). VERIFIED.
- **Build settings.** The build calls `docker` `client.api.build(..., nocache=True)` with a
  **30-minute timeout** (`runtime.py:93-147`, `trainer/constants.py:41`). VERIFIED.
  INFERRED: docker-py's low-level build API drives the classic builder, not BuildKit. Before you
  submit, test your Dockerfile with `DOCKER_BUILDKIT=0 docker build --no-cache`.
- **G.O.D `main` no longer ships a reference text trainer.** `dockerfiles/standalone-text-trainer.dockerfile`
  and `scripts/text_trainer.py` were removed in "Reorg (#1225)" on 2026-06-28. No
  `ops/docker/standalone-text-trainer.dockerfile` exists in history: the GitHub commits API
  returned 0 commits for that path on 2026-10-09. You must write your own. VERIFIED.

### 5.2 Run
`trainer/runtime.py:299-408`. VERIFIED.
- **CLI arguments:** `--task-id --model --dataset --dataset-type <json> --task-type --file-format s3 --expected-repo-name --hours-to-complete`.
- **Mounts:** `/cache` is read-only and `/app/checkpoints/` is read-write.
- **Container hardening:** `security_opt=no-new-privileges` and `cap_drop=ALL`.
- **Network:** an **internal bridge network**, so treat it as having no internet access (`runtime.py:71-77,389`).
- **Environment variables you may receive** (`runtime.py:322-337`, `docs/miner.md:343-359`):
  - `BASELINE_STATS_PATH`: optional JSON of dataset and model stats.
  - `WANDB_*`: offline-mode logging under `/app/checkpoints/wandb_logs`.
  - `MINER_DATASETS_DIR` and `MINER_DATASETS`: whitelisted requested datasets, at most 2 (`core/datasets/whitelist.py`).
  - `USE_KL=1` and `KL_COEF=<float>`: for KL instruct tasks.

### 5.3 Paths
`docs/miner.md:361-374`, `trainer/training_paths.py:13-14,45-46`. VERIFIED.

| What | Path |
|---|---|
| Final model (this is what gets uploaded) | `/app/checkpoints/{task_id}/{expected_repo_name}` |
| Training data | `/cache/datasets/{task_id}_train_data.json` |
| Cached base model | `/cache/models/<dir>`. When model-prep ran, the directory name is a salted hash, not the HF id (see 5.4). |

**Expected repo name** has the form `tournament-{tournament_id}-{task_id}-{hotkey[:8]}`
(`validator/tournament/tournament_manager.py:451`). It lives under the HF org
`gradients-io-tournaments` (`validator/infrastructure/service_constants.py:6`,
`validator/scoring/tasks.py:612`). VERIFIED.

### 5.4 The model's identity is hidden
When model-prep has run, `--model` points to `get_anonymous_model_dir(model_id)`, which is
`sha256(salt + model_id)[:16]` with a salt held only by the validator. `_name_or_path` is also
removed from `config.json` (`trainer/model_artifacts.py:7-33`, `runtime.py:1012-1015`). Model-prep
is enabled for every text task type (`validator/tasks/prep/constants.py:7-17`). VERIFIED.
INFERRED: detect the architecture from `config.json` and the tokenizer files, not from the model name.

### 5.5 Exit-code and timeout semantics (the most important runtime rule)
`trainer/runtime.py:1066-1092,1138-1160`. VERIFIED.
- **Exit 0:** the output directory is uploaded and then evaluated.
- **Exit ≠ 0:** `success=False`, so **nothing is uploaded** and the task scores as failed.
- **Wall-clock timeout** (`hours_to_complete × 3600` s, `runtime.py:927`): the container is
  killed and **whatever is already in the output directory is uploaded**.
- **What this means for a trainer.** INFERRED: keep a complete, loadable model in the output
  path as early as possible and replace it atomically. Never exit non-zero after a usable model
  has been written.

---

## 6. Artifact contract: what counts as a valid submission

`validator/evaluation/evaluators/instruct_text.py:466-479` and `validator/evaluation/model_checks.py`. VERIFIED.

- **LoRA adapter.** If the repo contains `adapter_config.json`, it is loaded with PEFT on top of
  the original model and counted as a finetune (`model_checks.py:150-180`).
- **Full model.** It must match the base on these config fields, or it is scored as
  "Non-finetuned submission": `architectures, hidden_size, n_layer, intermediate_size, head_dim,
  hidden_act, model_type, num_hidden_layers, num_attention_heads, num_key_value_heads`
  (`model_checks.py:92-123`).
- **`max_position_embeddings` must not be smaller than the base's.** A larger value is allowed
  (`model_checks.py:125-147`, `docs/miner.md:436`). The reason: a smaller value shrinks the eval
  `sequence_len` (`validator/evaluation/common.py:546-549`), which scores you on a different
  subset. In the boss round, unpairable vectors count as a loss (`round_results.py:259-269`).
- **The tokenizer comes from the base model, not from your repo.** For continuous-SFT it comes
  from the lineage seed (`instruct_text.py:452-455`). VERIFIED. INFERRED: a changed tokenizer or
  chat template in your repo does not change how eval tokenises the held-out set.
- **Missing repo.** If the expected HF repo is missing, the result is "Model repo not found on
  HuggingFace" (`validator/scoring/tasks.py:612-622`). VERIFIED.

---

## 7. Evaluation by task type

### 7.1 Instruct and chat (cross-entropy)
`instruct_text.py`, `validator/assets/test_axolotl.yml`, `validator/evaluation/common.py:530-551`. VERIFIED.
- **Configuration.**
  - Axolotl eval with `sequence_len: 4096` as the starting value, batch size 1, no sample packing.
  - If the submission's `max_position_embeddings` is below 2×4096, then
    `sequence_len = ceil(max_pos/2)`.
  - Rows longer than `sequence_len` are dropped. If nothing survives, eval retries with larger
    lengths, up to the model's limits (`instruct_text.py:80-145`).
- **Per-example losses.** When the validator requests them (`docker_evaluation.py:232-233`), eval
  also emits a per-example mean CE vector over label tokens, with batch size 1, so it matches the
  scalar exactly (`instruct_text.py:170-233`, `validator/evaluation/constants.py:26-34`).
- **KL tasks.** `test_loss = eval_loss + KL_COEF × mean_token KL(finetuned ‖ base)`, computed over
  label tokens (`instruct_text.py:243-289,369-385`). The per-example vector stays **raw CE**; the
  KL enters as a separate scalar check (section 8.3).

### 7.2 DPO
`validator/evaluation/evaluators/dpo.py`. VERIFIED.
- TRL `DPOTrainer.evaluate` with `beta = 0.1` (`dpo.py:300-305`, `constants.py:20`).
- Per-pair loss is `-logsigmoid(0.1 × logits)` (`dpo.py:236-267`).
- A loss of about ln 2 (0.6931) is logged as "random predictions" (`dpo.py:324-325`).

### 7.3 GRPO
`validator/evaluation/evaluators/grpo.py`, `validator/evaluation/result_processing.py`. VERIFIED.
- **Generation.** TRL `GRPOTrainer.evaluate` with `beta=0.0` and 2 generations per prompt
  (`grpo.py:202-228`). The per-function rewards are captured with their weights (`grpo.py:233-261`).
- **Cross-repo normalisation.** All repos for the task are **evaluated in one batch**
  (`validator/lifecycle/tasks.py:343-346`). Each reward is then normalised across those repos:
  shifted if negative, then divided by the maximum. The normalised rewards are weighted and summed.
- **KL penalty.** The final score is `weighted_sum − 0.5 × KL` (`result_processing.py:7-86`,
  `constants.py:21`).
- **Special case.** With exactly 2 repos and a negative reward, the two get 0.25 and 0.75 for that reward.
- INFERRED: a GRPO score is relative to the other models in the same batch. Absolute values are
  not comparable across tasks.

---

## 8. Ranking and winning

### 8.1 Per-task score (`quality_score`)
`validator/scoring/tasks.py:115-241`. VERIFIED.
- **Excluded results.** Results that failed, were non-finetuned, or have a NaN loss are left out of the ranking.
- **Rank 1** gets `quality_score = 3` (`FIRST_PLACE_SCORE`) and the reason `"Ranked 1st by test_loss"`.
- **Everyone else** gets 0 with the reason `"Ranked below top 1 by …"`.
- **More than 8 valid results:** the bottom 25% get −1 (`"Bottom 25% ranked by …"`).
- **Failed submissions** get −1 whenever at least one valid result exists.

### 8.2 R1 group: average rank over 3 tasks
`round_results.py:1067-1128`, `validator/tournament/models.py:242-257`. VERIFIED.
- **Sort order.** Standings are sorted by `(has_error, average_rank, summed_loss, hotkey)`.
- **`has_error`** is true if you are missing a valid result on **any** of the 3 tasks. A missing
  task ranks you below everyone who completed all three, whatever your other ranks.
- **Who advances:** the top 2.

### 8.3 Knockout and boss tasks: paired per-example comparison
`validator/tournament/thresholds.py`, `round_results.py:219-310,422-510`. VERIFIED.
Applies to instruct, DPO and chat (`PAIRED_BOSS_ROUND_TASK_TYPES`, `constants.py:319`).

- **Knockout (R2).**
  - Each example is a win, a loss, or a tie (inside a 0.01-nat dead zone).
  - The model with more example wins advances. If the win counts are equal, the lower mean loss advances.
  - The sample winner must also not be worse on the ranking scalar, which includes KL. Otherwise
    the mean-loss winner advances (`round_results.py:476-491`, `thresholds.py:231-259`).
  - GRPO knockouts use the mean (`round_results.py:430-431`).
- **Boss tasks.** The challenger takes a task only if **both** of these lower bounds clear their
  bar, at a one-sided 99% bootstrap (10,000 resamples, fixed seed):
  - the **win rate over decided examples** must reach at least 0.55;
  - the **mean gap** must reach at least `max(0.01 nats, 0.01 × |boss mean loss|)`.

  In addition, the challenger's scalar loss with KL must be ≤ the boss's.
  Sources: `thresholds.py:16-149,203-228,262-298`, `constants.py:258-313`.
  - **Draw.** If every example falls inside the dead zone, the task is a draw. It is left out of
    the tally, and one decider task of the same type is added, once (`round_results.py:513-554`,
    `task_creator.py:1021-1085`).
  - **Missing vectors** fall back to the 1% margin. **Vectors that cannot be paired** (different
    lengths or fingerprints) count as a boss win (`round_results.py:249-269`).
- **GRPO boss task.** The challenger must reach `≥ boss + 0.01 × |boss|` (`thresholds.py:301-312`).

### 8.4 Dethrone rule
`round_results.py:313-420`. VERIFIED.
- **Wins needed:** `required = min(max(1, max(decided, built_size) − 1), decided)`. With 6 built
  tasks and no draws, that is **5 of 6**.
- **Continuous-SFT gate:** in addition, every continuous-SFT lineage must be won or drawn by the
  challenger. A loss, or no result, blocks the dethrone.

### 8.5 Emission boost when the winner changes
- **Performance difference.** The median over boss tasks of `(boss − challenger)/|challenger|` for
  loss tasks, or `(challenger − boss)/|boss|` for GRPO (`validator/tournament/performance_calculator.py:197-227,365-386`).
- **Boost.** `2.0 × (diff − 0.10)` when the difference is above 0.10 (`validator/scoring/weights.py:77-87`).
- More detail is in `sn56-mechanics.md`. VERIFIED.

---

## 9. Reading results from the public API

All examples use placeholders. Responses contain hotkeys: **do not paste raw responses into public files.**

```bash
# One task, with every scored submission (the useful one)
curl -s https://api.gradients.io/auditing/tasks/<TASK_ID> | jq '.hotkey_details'
# Recent tasks (paginated: ?limit=&page=)
curl -s "https://api.gradients.io/auditing/tasks?limit=50"
# Latest completed tournament per track: rounds, tasks, boss_round_performance
curl -s https://api.gradients.io/tournament/latest/details | jq '.text.rounds[] | {round_number, round_type, is_final_round, n: (.tasks|length)}'
# Last boss battle, per task: boss_score, challenger_score, threshold_used, performance_difference, challenger_won
curl -s https://api.gradients.io/v1/performance/last-boss-battle
```

Observed live on 2026-10-09 (VERIFIED):
- **`hotkey_details[]` fields:** `hotkey, submission_id, quality_score, test_loss, synth_loss, repo, rank, score_reason, offer_response`.
- **`test_loss == synth_loss` for text tasks.** Both are set from the same `eval_loss`
  (`validator/scoring/tasks.py:756-768`). The top-level `miner_scores` field is `null`.
- **`score_reason` explains the basis for a rank.** Read it rather than assuming. Prefixes come
  from `validator/scoring/tasks.py:95-97`, plus failure strings such as `"Evaluation failed: …"`,
  `"Non-finetuned submission"` and `"Invalid test loss"`. Paired tasks read
  `"Winner at per-example win rate (…)"` or `"Lost to winner <hotkey> at …"`, so **they contain hotkeys**.
- **`training_data` and `test_data` are presigned S3 URLs that expire after 7 days**
  (`X-Amz-Expires=604800`; `validator/infrastructure/minio_client.py:83`). Download them inside
  that window if you want the real held-out set for offline replay.
- **Task fields worth reading:** `model_id`, `ds`, `hours_to_complete`, `yarn_factor`,
  `trained_model_repository`, and for `ChatTask` the `chat_*` columns.
  The `ds` of a continuous-SFT task has the form `continuous-sft:<lineage>:<label>` (`constants.py:207-213`).
- **Live round structure.** `/tournament/latest/details` showed text R1 as a group with 3 instruct
  tasks, R2 as a knockout with 1 task, and R3 as the final with 6 tasks (3 instruct, chat, GRPO, DPO).
  This matches section 2.2.

**Replaying evaluation locally.**
`python -m ops.validator_ops.run_evaluation --task_id <TASK_ID> [--models <HF_REPO>]` uses the
validator's own eval images (`docs/miner.md:566-575`). VERIFIED that the documentation says this; not run here.

---

## 10. Registration gates

These are covered in detail in `sn56-mechanics.md` §3. In short, all VERIFIED:
- `commit_hash` must be a 40-character hex string (`tournament_manager.py:1091-1096`).
- The obfuscation scan runs at that commit, with a 60-second limit (`github_validation.py:20-55`).
- LICENSE and NOTICE must equal upstream's after stripping trailing whitespace from each line.
  This is checked on the **default-branch clone**, not on the commit (`github_validation.py:57-170`).
- Fee: 0.7 TAO for text, charged at enrolment (`constants.py:343`).
- One entry per coldkey, per miner IP, and per GitHub repo owner (`github_validation.py:207-306`).

---

## 11. Duplicate and copy detection

- **R1, before training.** Tier T0 (same commit) and tier T1 (same normalised source) are run
  against all entrants **including the champion's repo**. Matches are eliminated automatically
  and forfeit the fee (`tournament_manager.py:1243-1262`, `validator/tournament/dedup_gate.py:103-124`).
  Normalisation strips whitespace, blank lines and whole-line `#` comments (`validator/infrastructure/repo_dedup.py:77-102`). VERIFIED.
- **R1 → R2.**
  - **Pairwise judgement (T2).** A Claude judge compares the R2 entrants pairwise. It is given the
    validator's own source, so it can check whether a claimed difference is a real training-time
    input. Any flag **halts the tournament until a human approves**. Approved duplicates are
    published to `gradients-opensource` and eliminated (`dedup_gate.py:1-7,129-198`,
    `repo_dedup.py:511-550`, `tournament_manager.py:878-887`).
  - **Code review.** A separate review checks each non-boss entrant for cheating: undeclared
    datasets, eval data, external weights, or score manipulation. It also halts until a human
    decides (`validator/tournament/challenger_code_review.py:82-130`,
    `validator/infrastructure/challenger_code_review_config.json`, `tournament_manager.py:888-920`). VERIFIED.
- **What counts as a duplicate.** Renames, reordering, dead code, noise hyperparameters,
  single-scalar tweaks, and "differences" triggered only by inputs absent at training time
  (`docs/miner.md:624`). VERIFIED that the documentation says this.
- **Self-check before submitting:**
  `python -m ops.tools.tournament.dedup_check <repo_a> <repo_b> --hash-a <sha> --hash-b <sha>`.
  It needs your own `ANTHROPIC_API_KEY` and prints `DUPLICATE`, `DISTINCT` or `DROP_EVASION`
  (`docs/miner.md:626-635`). VERIFIED that the documentation says this.

---

## 12. Where the docs disagree with the code (code wins)

| # | Docs say | Code or live API says | Status |
|---|---|---|---|
| 1 | Preferred text Dockerfile is `ops/docker/standalone-text-trainer.dockerfile` (`docs/miner.md:233,248`; `ops/docker/README.md:17`) | The file is not in the repo and never existed at that path. The legacy `dockerfiles/` copy was removed in #1225 on 2026-06-28. The validator still *accepts* either path from **your** repo (`trainer/constants.py:4,8`). | VERIFIED |
| 2 | "every knockout instruct task from round 2" is KL-weighted (`docs/miner.md:417`) | KL is drawn with probability 0.2 per instruct task, from R2 on (`scheduler.py:74-84`, `synthetics/constants.py:20`) | VERIFIED |
| 3 | The oversampled pool also feeds "one boss-round instruct task" (comment in `core/oversampled_later_models.json`) | `FINAL_ROUND_OVERSAMPLED_TASKS = 0` (`constants.py:188`) | VERIFIED |
| 4 | Setup: `git clone https://github.com/rayonlabs/G.O.D.git` (`docs/miner.md:141`); the miner endpoint default points at `rayonlabs/G.O.D` (`miner/endpoints/training_repo.py:12`) | Read and track `gradients-ai/G.O.D`, which is where `main` is pulled from. Do not "fix" references back toward `rayonlabs`. | VERIFIED source; redirect behaviour not tested |
| 5 | Balance endpoint `/tournament/balance/{coldkey}` (`docs/miner.md:119`) | `main` registers `/transfer/balance/{coldkey}` (`validator/endpoints/transfer_balances.py:187-195`). The deployed API answers the **docs** path and returns 404 on the code path. The deployed API is not always identical to `main`. | VERIFIED by probe on 2026-10-09 |
| 6 | Local example runs `--network none` with `HOURS_TO_COMPLETE=6` (`ops/examples/training/run_instruct_task.sh:11,56`) | Production uses an internal bridge network (`runtime.py:389`), and text hours are capped at 5.0 (`datasets/constants.py:26`) | VERIFIED |

---

## 13. Not verified here (open questions)

- Whether the deployed validator runs exactly commit `b49ba86`. Item 5 in section 12 shows that
  at least the API gateway differs from `main`. INFERRED: re-check `main` before each tournament.
- What the content service actually serves (model and dataset pools) beyond the filters
  described in code. The service is private.
- Whether submitted HF repos under `gradients-io-tournaments` are publicly readable for forensics.
  Not tested in this pull.
- Exactly when per-example vectors are requested for every paired task type. The flag is passed
  by the caller (`docker_evaluation.py:232-233`); the full call graph was not traced.

## 14. Refreshing this file

```bash
# Pin what you read
curl -s https://api.github.com/repos/gradients-ai/G.O.D/commits/main | jq -r '.sha, .commit.committer.date'
# Watch the files this document depends on
for p in validator/tournament/constants.py validator/tournament/task_creator.py validator/tournament/thresholds.py \
         validator/tournament/round_results.py validator/scoring/tasks.py validator/tasks/synthetics/scheduler.py \
         validator/tasks/datasets/constants.py validator/tasks/prep/constants.py trainer/runtime.py docs/miner.md; do
  echo "== $p"; curl -s "https://api.github.com/repos/gradients-ai/G.O.D/commits?path=$p&per_page=1" | jq -r '.[0] | .sha[0:10] + " " + .commit.committer.date'
done
```
If any of those files has a newer commit than the one pinned at the top, re-read it before
trusting the numbers above.
