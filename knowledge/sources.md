# Sources and how to re-pull them

Everything in [`image-track.md`](image-track.md) comes from the public sources below, pulled on **2026-10-09**.
Re-pull before every tournament (the image track starts **Monday 13:00 UTC**), and treat any difference as news.

**Hygiene rules for pulled data:**
- API payloads contain participant hotkeys and **presigned dataset URLs** (bearer links, valid 7 days). Keep raw
  pulls in a git-ignored scratch folder, and **never commit them** to a public repo.
- Write down *mechanics* (with `file:line`), not other people's identities or scores.
- The examples use placeholders such as `<TASK_ID>`, `<TOURNAMENT_ID>`, `<HOTKEY>` and `<COLDKEY>`. Substitute your
  own values locally.

---

## 1. Validator code: `gradients-ai/G.O.D` (branch `main`)

| What | URL / command |
|---|---|
| Repo | https://github.com/gradients-ai/G.O.D (the old `github.com/rayonlabs/G.O.D` 301-redirects here) |
| Snapshot used | commit `b49ba8644a0d705e9b035746eceaa05614742934` (2026-10-07 23:44 UTC) |
| Current head | `curl -s https://api.github.com/repos/gradients-ai/G.O.D/commits/main \| jq -r '.sha, .commit.committer.date'` |
| Full local copy | `git clone --depth 1 https://github.com/gradients-ai/G.O.D.git` |
| One file | `curl -s https://raw.githubusercontent.com/gradients-ai/G.O.D/main/<path>` |
| One file at a pinned commit | `curl -s https://raw.githubusercontent.com/gradients-ai/G.O.D/<sha>/<path>` |
| History of one file | `curl -s "https://api.github.com/repos/gradients-ai/G.O.D/commits?path=<path>&per_page=5" \| jq -r '.[] \| "\(.sha[0:10]) \(.commit.committer.date[0:10]) \(.commit.message \| split("\n")[0])"'` |
| What changed since the last pull | `git -C G.O.D fetch --depth 50 origin main && git -C G.O.D diff <old_sha> origin/main -- validator/tournament validator/evaluation validator/tasks/synthetics trainer core/constants docs/miner.md` |

### Files that define the image track (read these first)

| Area | Files |
|---|---|
| Miner contract (doc) | `docs/miner.md`, `SKILL.md`, `AGENTS.md`, `miner/endpoints/training_repo.py`, `core/models/payload_models.py` |
| Tournament structure, fees, margins, schedule | `validator/tournament/constants.py`, `validator/tournament/tournament_manager.py`, `validator/tournament/task_creator.py`, `validator/tournament/participants.py` |
| Round winners and dethrone | `validator/tournament/round_results.py`, `validator/tournament/thresholds.py`, `validator/tournament/models.py` (`GroupMatchStanding`), `validator/db/sql/submissions_and_scoring.py` (`get_task_winner`) |
| Entry gates | `validator/tournament/github_validation.py`, `validator/tournament/obfuscation_detection/anti_obfuscation` (Linux x86-64 binary) |
| Dedup and code review | `validator/tournament/dedup_gate.py`, `validator/infrastructure/repo_dedup.py`, `validator/infrastructure/repo_dedup_config.json`, `validator/tournament/challenger_code_review.py`, `validator/infrastructure/challenger_code_review_config.json`, tool `ops/tools/tournament/dedup_check.py` |
| Task generation | `validator/tasks/synthetics/diffusion.py`, `validator/tasks/synthetics/constants.py`, `validator/tasks/synthetics/scheduler.py`, `validator/tasks/datasets/preparation.py`, `validator/tasks/datasets/constants.py`, `validator/tasks/requests.py`, `validator/tasks/prep/constants.py` |
| Trainer runtime (build/run/upload) | `trainer/runtime.py`, `trainer/constants.py`, `trainer/host.py`, `trainer/training_paths.py`, `trainer/containers/downloader.py`, `trainer/containers/uploader.py`, `validator/tournament/gpu_requirements.py` |
| Evaluation | `validator/evaluation/evaluators/diffusion.py`, `validator/evaluation/denoising_mse.py`, `validator/evaluation/image_denoising.py`, `validator/evaluation/image_encoder.py`, `validator/evaluation/image_flow_adapter.py`, `validator/evaluation/image_artifacts.py`, `validator/evaluation/image_test_data.py`, `validator/evaluation/docker_evaluation.py` (`run_evaluation_basilica_image`), `validator/evaluation/constants.py`, `ops/docker/validator-diffusion.dockerfile`, `core/constants/docker.py` |
| Scoring within a task | `validator/scoring/tasks.py`, `validator/scoring/constants.py`, `validator/tournament/task_results.py` |
| Baseline image trainer | `ops/docker/standalone-image-toolkit-trainer.dockerfile`, `ops/docker/scripts/image_toolkit_entrypoint.py`, `core/training_templates/base_diffusion_*.yaml` |
| Local tools | `ops/examples/training/run_image_task.sh` (note: not production-faithful), `ops/validator_ops/run_evaluation.py` |

**Quick grep recipes** (run inside the clone):
```sh
grep -n 'ROUND_ONE_IMAGE\|ROUND_TWO_IMAGE\|FINAL_ROUND_IMAGE\|BOSS_ROUND_WIN_MARGIN\|SCHEDULE_IMAGE\|IMAGE_PARTICIPATION_FEE' validator/tournament/constants.py
grep -n 'IMAGE_SYNTH_CATEGORY_WEIGHTS\|IMAGE_SYNTH_PAIRS\|IMAGE_COMPETITION_HOURS\|QWEN_IMAGE_EXTRA' -A0 validator/tasks/synthetics/constants.py
grep -n 'DEFAULT_STRATA\|DEFAULT_NOISES\|set_cfg' validator/evaluation/evaluators/diffusion.py
grep -n 'FAMILIES = {' -A28 validator/evaluation/image_flow_adapter.py
grep -n 'IMAGE_DOCKERFILE_PATHS\|IMAGE_TOOLKIT_DOCKERFILE_PATHS' trainer/constants.py trainer/runtime.py
grep -n 'COMFYUI_COMMIT' ops/docker/validator-diffusion.dockerfile
```

---

## 2. Public API: `https://api.gradients.io`

| Endpoint | Notes |
|---|---|
| `GET /openapi.json` and `GET /docs` | the authoritative list of public routes (60 paths at pull time). Validator-internal `/v1/tournaments/*` analytics return 404 publicly. |
| `GET /tournament/latest/details` | latest tournament per type (`.image`) |
| `GET /tournament/<TOURNAMENT_ID>/details` | a specific tournament |
| `GET /auditing/tasks/<TASK_ID>` | the full task record: `model_id`, `model_type`, `hours_to_complete`, `trigger_word`, `ds`, `image_text_pairs`, `test_data` (presigned, 7 days), `hotkey_details[]` |
| `GET /auditing/tasks` | recent tasks (100 at pull time) |
| `GET /auditing/tasks/hotkey/<HOTKEY>` | tasks for one hotkey |
| `GET /auditing/scores-url` | pointer to a scores dump |
| `GET /tournament/fees` | fees in RAO |
| `GET /tournament/balance/<COLDKEY>` | tournament balance for a coldkey |
| `GET /v1/performance/last-boss-battle` | per-type boss-battle performance |
| `GET /v1/performance/latest-tournament-weights` | weight split |

**Recipes** (save to a scratch dir, never commit):
```sh
API=https://api.gradients.io
# structure of the latest image tournament: rounds, round types, task counts
curl -s $API/tournament/latest/details | jq '.image | {status, rounds: [.rounds[] | {round_number, round_type, is_final_round, n_tasks: (.tasks|length)}]}'

# per-task facts for every image task in that tournament
for t in $(curl -s $API/tournament/latest/details | jq -r '.image.rounds[].tasks[].task_id'); do
  curl -s $API/auditing/tasks/$t | jq -c '{model_type, model_id, hours_to_complete, n_pairs: (.image_text_pairs|length), has_trigger: (.trigger_word != null), created_at}'
done

# per-miner results of one task (rank, loss, reason, uploaded HF repo)
curl -s $API/auditing/tasks/<TASK_ID> | jq '.hotkey_details[] | {rank, test_loss, score_reason, repo}'

# expiry of the held-out test zip link (seconds); do not print or store the URL itself in shared places
curl -s $API/auditing/tasks/<TASK_ID> | jq -r '.test_data' | sed -n 's/.*X-Amz-Expires=\([0-9]*\).*/\1/p'

# boss-round per-task verdicts (threshold_used = 0.01 for image)
curl -s $API/tournament/latest/details | jq '.image.boss_round_performance[] | {task_type, threshold_used, challenger_won, decision_reason}'
```

---

## 3. Hugging Face Hub (public metadata)

| What | URL |
|---|---|
| Model metadata (gating, files, sha) | `https://huggingface.co/api/models/<REPO_ID>` (e.g. `krea/Krea-2-Raw` reported `gated: "auto"` on 2026-10-09; `Comfy-Org/Ideogram-4`, `Comfy-Org/Krea-2` and the `gradients-io-tournaments/*` bases reported `gated: false`) |
| Files with sizes | `https://huggingface.co/api/models/<REPO_ID>?blobs=true` or `huggingface_hub.HfApi().model_info(repo, files_metadata=True)` |
| Uploaded tournament artifacts | `https://huggingface.co/gradients-io-tournaments/<expected_repo_name>` (LoRA under `checkpoints/`; name from `hotkey_details[].repo`) |
| Published winner repos (GitHub) | https://github.com/gradients-opensource |

---

## 4. Product site

| What | URL |
|---|---|
| Site | https://gradients.io |
| Tournament results page | `https://gradients.io/app/research/tournament/<TOURNAMENT_ID>` (`SKILL.md:14`) |

---

## 5. Re-pull checklist (each Monday before 13:00 UTC)

1. `commits/main` head SHA → if it changed, diff the folders in §1 against the last SHA you recorded.
2. Re-read `validator/tournament/constants.py` (round families, task counts, margins, fees, schedule).
3. Re-read `validator/evaluation/evaluators/diffusion.py` and `image_flow_adapter.py` (metric, families, LoRA
   strictness) and the `COMFYUI_COMMIT` pin.
4. Re-read `trainer/constants.py` / `trainer/runtime.py` (Dockerfile paths, build and run settings, timeout).
5. Run the recipes in §2 against the latest completed tournament to confirm the code matches what is live.
6. Update `image-track.md`: snapshot SHA, pull date, VERIFIED/INFERRED labels, §12 (doc-vs-code) and §13
   (commit log).
