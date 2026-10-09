# Gradients SN56 mechanics across all tracks (public sources only)

> **Pulled:** 2026-10-09, about 05:45 UTC.
> **Code:** `gradients-ai/G.O.D`, branch `main`, at commit `b49ba8644a0d705e9b035746eceaa05614742934`
> (2026-10-07T23:44Z).
> **Live API:** `https://api.gradients.io` (probed 2026-10-09).
>
> In this file, `path:line` means a path in G.O.D at that commit.
> **VERIFIED** means the claim was read in code or docs, or seen live, on the pull date.
> **INFERRED** means a reasonable conclusion that has not been tested.
> **Code beats docs.** Track-specific detail is in `text-track.md`.
>
> This file holds no participant data: no hotkeys or SS58 addresses, no scores, no rankings.
> The numbers here are validator constants, arithmetic on them, or subnet-level values from the public API.

---

## 0. Summary

1. **What SN56 is.** Bittensor subnet 56, called G.O.D, "Gradients on Demand". Testnet is netuid 241
   (`docs/miner.md:73-80`). Miners compete in weekly **tournaments** by submitting **training code**.
   The validator trains and evaluates that code on its own GPUs. VERIFIED.
2. **Three tournament types with separate emission pools:** text, image and environment (env).
   Each type's pool is paid to that type's champion and runners-up (`validator/scoring/weights.py:145-297`). VERIFIED.
3. **Getting into the field.**
   - Run a miner endpoint that answers `GET /training_repo/{text|image|environment}` with a repo
     URL and a full commit SHA.
   - Pay a per-type fee from your coldkey's tournament balance.
   - Pass the obfuscation, LICENSE/NOTICE and duplicate gates. VERIFIED (section 3).
4. **The champion competes as code.** Each week the last winner's repo is entered again as the
   "boss". The challenger must beat it on fresh boss-round tasks, by a margin, on nearly every
   task (`validator/tournament/participants.py:130-176`, `validator/tournament/round_results.py:313-420`). VERIFIED.
5. **Within a type, at most 3 ranks are paid,** at roughly 76/19/5. The champion's pool can be
   boosted when the champion won decisively (section 5). VERIFIED.

---

## 1. Calendar

| | Env | Text | Image | Source |
|---|---|---|---|---|
| Scheduled start (UTC) | Mon 09:00 hour | Mon 11:00 hour | Mon 13:00 hour | `validator/tournament/constants.py:17-22`; weekday 0 = Monday (`tournament_manager.py:1898-1912`) |
| Minimum validated miners | 5 | 4 | 4 | `constants.py:33-34` |
| Expected completion | Fri 14:00 UTC (manual, after code review) | same | same | `constants.py:24-30`, `docs/miner.md:63-67` |
| Participation fee | 0.6 TAO | 0.7 TAO | 0.4 TAO | `constants.py:342-345`. Live `GET /tournament/fees` on 2026-10-09 returned the same values in RAO. |

- **Start window.** A tournament starts only inside its scheduled hour, and only if no tournament
  of the same type is active or pending. A missed window waits a week (`tournament_manager.py:1796-1895`). VERIFIED.
- **Registration lead time.** Register with Fiber and post your miner IP **at least 1 hour before**
  the start (`docs/miner.md:32,97`). VERIFIED that the documentation says this. The enforcement
  mechanism in code was not traced (INFERRED: metagraph sync timing).

---

## 2. What a miner actually runs

- **Miner service.** `task miner` serves `GET /training_repo/{task_type}` on port 7999 (`docs/miner.md:157-171`).
- **Request filtering.** The route is protected by Fiber's `blacklist_low_stake` and
  `verify_get_request`, so only validators with enough stake can query it
  (`miner/endpoints/training_repo.py:19-31`).
- **Response shape:** `TrainingRepoResponse{github_repo, commit_hash, github_token?, requested_datasets?}`
  (`core/models/payload_models.py:133-139`). VERIFIED.
- **Different repos per type.** You may answer with different repos or commits for text, image and environment (`docs/miner.md:196`). VERIFIED.
- **Private repos.** These work with a fine-grained GitHub token that has `Contents: Read-only`.
  An invalid token is silently dropped, so the clone then fails (`docs/miner.md:199-213`,
  `validator/tournament/github_validation.py:180-205`). VERIFIED.
- **Keep the commit reachable.** The repo and commit must stay accessible until the tournament
  completes (`docs/miner.md:195`). VERIFIED that the documentation says this.

---

## 3. Entry pipeline, in order

Registration pass: `validator/tournament/tournament_manager.py:961-1180`. All VERIFIED unless marked.

1. **Query responding miners** for the tournament type. Validate any GitHub tokens (`:1057`).
2. **Collapse duplicates by identity, in this order:**
   - **IP:** the miner's posted on-chain IP (`:1059-1064`).
   - **GitHub owner:** the first path segment of the repo URL, case-insensitive (`:1066-1071`).
   - **Coldkey** (`:1073-1078`).

   In each group, one entry survives. An entry with a valid token is preferred; otherwise the
   first one wins (`github_validation.py:207-306`).
3. **`commit_hash` must match `[0-9a-f]{40}`.** Branch names are rejected (`:1091-1096`).
4. **Obfuscation scan** of the repo **at that commit**. It runs a bundled binary with a 60-second
   timeout. A non-zero exit, or a timeout, excludes you (`github_validation.py:20-55`,
   `constants.py:337`). Compiled, minified or packed code fails (`docs/miner.md:270-274`).
5. **LICENSE and NOTICE.** The validator runs a plain `git clone`, which gives the **default
   branch, not your commit**. The files must equal upstream's line for line after trailing
   whitespace is stripped. Accepted names are `LICENSE.md|LICENSE|license.md|license|License.md|License`
   and `NOTICE|NOTICE.txt|notice.txt|Notice.txt|notice|Notice` (`github_validation.py:57-170`).
   INFERRED rule of thumb: copy both files byte for byte from upstream and never edit them.
6. **Requested datasets** are filtered against `core/datasets/whitelisted_sft_datasets.json`.
   At most 2 are kept (`:1130-1134`, `docs/miner.md:376-406`).
7. **Enrolment charges the fee** from the coldkey's tournament balance (`:1148-1160`).
8. **Minimum field.** Below the minimum, the validator waits 30 minutes and retries (`:1169-1180`).
9. **R1 hash de-duplication, before training.**
   - **Checks.** Tier T0 (identical commit) and tier T1 (identical source after stripping
     whitespace, blank lines and `#` comment lines) are run across all entrants **and the
     champion's repo**.
   - **Effect.** Matches are removed from the bracket and **forfeit the fee**
     (`tournament_manager.py:1243-1262`, `validator/tournament/dedup_gate.py:103-124`,
     `validator/infrastructure/repo_dedup.py:77-102`).
10. **Between R1 and R2, two human-gated reviews run.** VERIFIED:
    - **Functional-duplicate review (T2).** A Claude judge compares the R2 entrants pairwise,
      with the validator source as context (`dedup_gate.py:129-198`, `repo_dedup.py:511-550`).
    - **Cheating review.** A separate Claude review looks for undeclared datasets, eval data,
      external weights, or score manipulation (`validator/tournament/challenger_code_review.py:82-130`,
      `validator/infrastructure/challenger_code_review_config.json`).
    - **Both halt the tournament until an operator decides** (`tournament_manager.py:878-920`).
    - **Approved duplicates are published to `github.com/gradients-opensource` and eliminated** (`dedup_gate.py:1-7`).
11. **The winner's repo is re-uploaded publicly** to `github.com/gradients-opensource`
    (`docs/miner.md:47`, `tournament_manager.py:841-844`). Next week it is entered again as the
    boss through that backup copy (`participants.py:130-162`). VERIFIED.

**Checking a submission locally against duplicates:**
`python -m ops.tools.tournament.dedup_check <repo_a> <repo_b> --hash-a <sha> --hash-b <sha>`.
It needs your own `ANTHROPIC_API_KEY` (`docs/miner.md:626-635`).

### 3.1 Checklist for a second competitor from the same organisation (INFERRED from the VERIFIED rules above)
- **Coldkey:** use a separate coldkey. Entries sharing a coldkey collapse to one.
- **Miner IP:** post a separate public IP. Entries sharing an IP collapse to one. A shared office
  NAT, or two miners on one server, counts as the same IP.
- **GitHub owner:** host the repo under a **separate GitHub owner** (user or org). Repos under the
  same owner collapse to one entry, even when the repo names differ.
- **Training behaviour:** make it genuinely distinct. Shared code with cosmetic changes is
  eliminated, either automatically (R1 hash) or after review (R2).
- **Fee balance:** each coldkey pays its own fee.

---

## 4. Fees and balance

- **Who pays.** Fees come from a **per-coldkey** tournament balance. Hotkeys under one coldkey
  share that balance, and only one entry per coldkey is kept (`docs/miner.md:99-109`). VERIFIED.
- **How to fund it.** Transfer TAO to the collection address printed in `docs/miner.md:112`.
  Read the address there; it is not copied here. VERIFIED.
- **What happens to fees.** The fee is charged after the repo passes validation, and collected
  fees are burned (`docs/miner.md:101,122`). VERIFIED that the documentation says this.
- **Where to check:**
  - `GET https://api.gradients.io/tournament/fees` (live, VERIFIED).
  - `GET https://api.gradients.io/tournament/balance/<COLDKEY>`. The deployed path answered
    on 2026-10-09; `main` registers it as `/transfer/balance/{coldkey}` instead (section 8).

---

## 5. Emissions: how weight reaches miners

### 5.1 Splitting emissions between the three types
`validator/scoring/constants.py:22-48`, `validator/scoring/emission_balance.py:1-115`. VERIFIED.
- **Anchor bases:** text 0.35, image 0.25, env 0.25. These are the neutral split, and the
  fallback for a type with no history.
- **Participation balancer.** `wᵢ ∝ anchorᵢ · (anchor_shareᵢ / entrant_shareᵢ)^1.25`, then:
  - each type gets a floor of 0.05 (borrowing from burn, so the pool may grow toward 1.0);
  - each type is capped at 0.50.
- **Which tournaments count.** The entrant count is the mean of the **last 4 completed
  tournaments, excluding the most recent one** (a one-tournament lag). Tournaments with fewer
  than 3 entrants are ignored. The lag fixes the split before a cohort enters
  (`constants.py:43-48`).
- **Effect.** INFERRED: a type with fewer entrants than its anchor share gains weight over time;
  an oversubscribed type loses weight.

### 5.2 A type's pool
`validator/scoring/weights.py:145-297`. VERIFIED.
- **Pool formula.** `pool = min(base + boost, 0.50)`. Then **the three pools are rescaled to sum
  exactly 1.0**, up or down (`weights.py:263-270`). After rescaling, a pool can end up above the 0.50 cap.
- **Live snapshot** from `GET /v1/performance/latest-tournament-weights` on 2026-10-09: text ≈ 0.510,
  image ≈ 0.191, env ≈ 0.299. VERIFIED. Treat this as a point-in-time value; it moves with
  participation and boosts.
- **Champion boost.** `boost = 2.0 × (perf_diff − 0.10)` when `perf_diff > 0.10`, otherwise 0
  (`weights.py:77-87`, `constants.py:51-54`).
  - **`perf_diff`** is the **median over boss-round tasks** of the relative gap
    (`validator/tournament/performance_calculator.py:197-227,365-386`).
  - **For env,** `perf_diff` comes from the win percentage: 0 below 60%, then
    `0.10 + (win% − 0.60) × 0.125` (`weights.py:90-101`).
- **Defending champions keep their original boost.** When the champion defends, the boost uses
  the `perf_diff` stored from the tournament where they **first won**. There is no time decay
  (`weights.py:182-206,240-243`).

### 5.3 Splitting a pool between ranks
`validator/scoring/tournaments.py:296-309,395-449`, `docs/miner.md:488-500`. VERIFIED.
- **Paid ranks.** Only placements 1–3 are paid (`TOURNAMENT_PAID_RANKS = 3`), with weights
  `0.25^(rank−1)` normalised. That gives **0.762 / 0.190 / 0.048**. If no clean 3rd place exists,
  the split is **0.8 / 0.2**.
- **Who places where.** 1st is the champion, 2nd is the other boss-round finalist.
  - **Text and image:** 3rd is the loser of the last knockout before the boss round (`tournament_manager.py:799-822`).
  - **Env:** 3rd follows the separate rules in `docs/miner.md:492-500`.
- **Everyone else gets nothing from the pool.** All ranks are paid from the same per-type pool
  (`weights.py:240-243`). Entrants eliminated earlier receive nothing from it.
- **Participation weight.** Every active participant receives a fixed **0.0001**, and the other
  weights are scaled by `1 − n × 0.0001` (`weights.py:435-450,497-500`, `constants.py:23`).
- **Leftover weight.** Any undistributed remainder goes to the burn node (`weights.py:506-512`).
  The docs say "nothing is burned" (`docs/miner.md:476`). INFERRED: the remainder is normally
  about 0, except when a paid hotkey is no longer registered.
- **How the champion appears in brackets.** The defending champion is entered under a fixed
  placeholder hotkey defined in `validator/scoring/constants.py`. Its real identity is kept in
  `base_winner_hotkey` and resolved when paying (`tournaments.py:388-392`,
  `tournament_manager.py:731-738`). VERIFIED.

### 5.4 Projections (what-if)
- **Endpoints:**
  - `GET /v1/performance/weight-projection?percentage_improvement=<pct>`
  - `GET /v1/performance/weight-projection-static` (the 5/10/15/20% cases)
  - Both answered live on 2026-10-09.
- **Model behind them.** Projections use `ALPHA_PER_SECOND = 1/12` and `MINER_ALPHA_SHARE = 0.41`
  (`validator/scoring/constants.py:57-59`), and `RUNNER_UP_EMISSION_DAYS = 7`, which only the
  projection code reads (`validator/tournament/performance_utils.py:186-187`). VERIFIED.
  INFERRED: these are approximations for display, not inputs to on-chain weights.

---

## 6. Round structure by track

| | Text | Image | Environment |
|---|---|---|---|
| R1 | One group of all entrants. 3 instruct tasks, models ≤4B. Top 2 advance by average rank. | One group. 3 tasks: Flux, Ideogram4, Z-Image. Each uses ≤30 synthetic pairs, ≤0.75 h and ≤3 test images. Top 2 advance by average rank. | Groups of ≤5 (≤4 if the field is ≤7), with the boss injected. 2 environments per task; `intercode` is always one of them. |
| Middle | R2: 1 knockout task (instruct 75% / DPO 20% / GRPO 5%), models ≤12B | R2: 1 knockout task, Qwen-Image or Krea2 | R2 uses 4 environments, R3 uses 6. Can continue from your previous-round model. 1 winner per group. |
| Boss | 6 tasks: 3 instruct (one 30–72B) + DPO + GRPO + continuous-SFT chat | 6 tasks: Krea2 ×2, Ideogram4 ×2, Qwen-Image ×1, Z-Image ×1 | 3 tasks: continuation, from-scratch (3.0 h), previous-winner `swe_infinite` (2.5 h) |
| Win a boss task | Instruct, DPO and chat: paired per-example test at the 99% bootstrap lower bound. GRPO: beat the boss by 1% relative. | Beat the boss by 1% relative | PvP or individual score |
| Dethrone | ≥ (tasks − 1) wins **and** win or draw every continuous-SFT task | ≥ (tasks − 1) wins | No boss-round losses, and at least a tie on `swe_infinite` |
| GPUs | Scaled by model size and type (1–8×H100) | 1×H100 | Depends on the environment and the start point |

Sources (all VERIFIED):
- Text: `text-track.md` §2 and §8.
- Image: `constants.py:147-169` and `docs/miner.md:438-454`. The 1% rule is in `validator/tournament/thresholds.py:301-312`.
  Image hours are 0.5–1.0 h, plus 0.5 h for Qwen-Image (`validator/tasks/synthetics/constants.py:70-72`).
- Image GPUs: `validator/tournament/gpu_requirements.py:45-46`.
- Env: `constants.py:96-123` and `docs/miner.md:456-472`. Scoring: PvP win, draw and loss are
  worth 3/1/0 points, and the win-percentage threshold is 0.60 (`validator/scoring/constants.py:14-20`).
- Live check: `/tournament/latest/details` on 2026-10-09 showed text and image as group → knockout
  → final with 3 → 1 → 6 tasks, and env as group → group → final with 3 → 1 → 3 tasks.

**Universal training contract** (`docs/miner.md:276-296`, `trainer/runtime.py`). VERIFIED.
- **Steps.** The validator clones your repo at the commit, pre-downloads the model and data into
  a read-only `/cache`, builds your Dockerfile with no cache (30-minute limit), and runs it with
  no-new-privileges and all capabilities dropped, on an internal network.
- **Output.** The output at `/app/checkpoints/{task_id}/{expected_repo_name}` is uploaded to HF
  org `gradients-io-tournaments`.
- **Exit codes.** A non-zero exit uploads nothing. A timeout kills the container and then uploads
  what exists (`trainer/runtime.py:1066-1092,1138-1160`).

---

## 7. Public intelligence sources and how to pull them

All examples use placeholders. Responses contain hotkeys and repo names, so keep raw dumps out of
any public repo.

### 7.1 The validator code (the authority)
```bash
curl -s https://api.github.com/repos/gradients-ai/G.O.D/commits/main | jq -r '.sha, .commit.committer.date, .commit.message' | head -5
curl -s "https://api.github.com/repos/gradients-ai/G.O.D/commits?per_page=20" | jq -r '.[] | .sha[0:10] + " " + .commit.committer.date + " " + (.commit.message|split("\n")[0])'
curl -s "https://raw.githubusercontent.com/gradients-ai/G.O.D/main/validator/tournament/constants.py"
curl -sL -o god.tgz "https://codeload.github.com/gradients-ai/G.O.D/tar.gz/<SHA>"   # whole tree at a pinned SHA
```
Watch these files closely, because they change the rules:
- `validator/tournament/constants.py`
- `validator/scoring/constants.py`
- `validator/tournament/{task_creator,round_results,thresholds,tournament_manager}.py`
- `validator/tasks/synthetics/{constants,scheduler}.py`
- `trainer/runtime.py`
- `docs/miner.md`

### 7.2 The public API gateway
The full route list is at `https://api.gradients.io/openapi.json`, and human-readable docs at
`https://api.gradients.io/docs` (both returned 200 on 2026-10-09). Routes relevant to tournaments,
all answered anonymously on 2026-10-09 (VERIFIED):

| Route | What it gives |
|---|---|
| `GET /tournament/latest/details` | Latest tournament per track: rounds, tasks, participants, `boss_round_performance`, and the per-type weights. For text, `paired_comparison` gives the win rate, its bound and the mean gap. |
| `GET /tournament/<TOURNAMENT_ID>/details` | The same for a specific tournament |
| `GET /auditing/tasks?limit=&page=` | Recent tasks: `model_id`, `ds`, `hours_to_complete`, `status`, data URLs, and so on |
| `GET /auditing/tasks/<TASK_ID>` | One task, plus `hotkey_details[]`: `hotkey, submission_id, quality_score, test_loss, synth_loss, repo, rank, score_reason, offer_response`. **This is the per-miner truth.** The top-level `miner_scores` field is `null`. |
| `GET /auditing/tasks/hotkey/<HOTKEY>` | Recent tasks for one hotkey (a malformed hotkey returned HTTP 500) |
| `GET /auditing/scores-url` | Presigned URL to the JSON audit data behind the validator's last weight setting (`weights.py:54-74`) |
| `GET /v1/performance/latest-tournament-weights` | Current pool split and top miners per type |
| `GET /v1/performance/last-boss-battle` | Per boss task: `boss_score`, `challenger_score`, `threshold_used`, `performance_difference`, `challenger_won` |
| `GET /v1/performance/weight-projection[-static]` | Emission projections |
| `GET /tournament/fees`, `GET /tournament/balance/<COLDKEY>` | Fees and balance |

**Caveats** (all VERIFIED):
- **Analytics routes.** The `main` analytics routes (`/v1/tournaments/...`) are API-key-gated
  (`validator/endpoints/tournament_analytics.py:727`) and returned 404 at the gateway. Use the
  `/tournament/...` routes above instead.
- **Data URLs expire.** `training_data` and `test_data` are presigned URLs valid for **7 days**
  (`X-Amz-Expires=604800`, `validator/infrastructure/minio_client.py:83`). Pull the held-out set
  inside that window if you want it for offline replay.
- **`score_reason` explains the basis for a rank.** Read it rather than assuming. It can contain other miners' hotkeys.
- **The deployed gateway is not always identical to `main`** (section 8, item 2).

### 7.3 Artifacts and winners
- **Trained models** go to Hugging Face org `gradients-io-tournaments`, under the name
  `tournament-<tournament_id>-<task_id>-<hotkey[:8]>` (`validator/infrastructure/service_constants.py:6`,
  `tournament_manager.py:451`). Inspect them with `https://huggingface.co/api/models/<org>/<repo>`.
  The listing endpoint answered 200 on 2026-10-09. INFERRED: individual repos may or may not be
  publicly readable; check before relying on them.
- **Winning repos,** and repos eliminated as approved duplicates, are re-published at
  `github.com/gradients-opensource`. The org answered 200 on 2026-10-09 (`docs/miner.md:47`,
  `dedup_gate.py:1-7`). VERIFIED.
- **Tournament result pages:** `https://gradients.io/app/research/tournament/<TOURNAMENT_ID>` (`SKILL.md:14` in G.O.D).

### 7.4 Replaying evaluation offline
- Build the validator eval images: `ops/docker/validator.dockerfile` for text and
  `validator-diffusion.dockerfile` for image.
- Then run `python -m ops.validator_ops.run_evaluation --task_id <TASK_ID> [--models <HF_REPO>]`
  (`docs/miner.md:566-583`). VERIFIED that the documentation says this; not run here.

### 7.5 Not public
Validator log streams (Loki or Grafana) and validator-side W&B are operated by the validator
(`docs/developer.md:275-303`). No public endpoint is documented in the repo. VERIFIED.

---

## 8. Where the docs disagree with the code (cross-track)

| # | Docs or comment say | Code or live API says | Status |
|---|---|---|---|
| 1 | "Nothing is burned" (`docs/miner.md:476`) | Any undistributed remainder goes to the burn node (`weights.py:506-512`); INFERRED to be about 0 in normal operation | VERIFIED code, INFERRED size |
| 2 | Balance at `/tournament/balance/{coldkey}` (`docs/miner.md:119`) | `main` registers `/transfer/balance/{coldkey}` (`validator/endpoints/transfer_balances.py:187-195`). The live gateway answers the docs path and returns 404 on the code path. | VERIFIED by probe |
| 3 | Clone `rayonlabs/G.O.D` (`docs/miner.md:141`); default participant repo `rayonlabs/G.O.D` (`constants.py:125`) | Track `gradients-ai/G.O.D`. Do not rewrite references toward `rayonlabs`. | VERIFIED source |
| 4 | Pool caps of 0.50 read as hard limits | Pools are rescaled to sum to 1.0 *after* capping, so a pool can exceed 0.50. The live text pool was ≈0.510 on 2026-10-09. | VERIFIED |
| 5 | Text Dockerfile at `ops/docker/standalone-text-trainer.dockerfile` | No such file exists upstream; you must supply your own. See `text-track.md` §12. | VERIFIED |

---

## 9. Refresh procedure (before each tournament week)
1. Pin `main`'s SHA and date (section 7.1). Diff the watched files against the SHA at the top of this file.
2. Re-run `GET /tournament/fees`, `/v1/performance/latest-tournament-weights` and
   `/tournament/latest/details`, and compare the round shape against section 6.
3. If anything changed, update this file. Change the pull date and SHA at the top, and re-mark
   every changed claim VERIFIED or INFERRED.
