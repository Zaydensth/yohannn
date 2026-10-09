# Tournament mechanics — image track

> Re-derived from `gradients-ai/G.O.D@b49ba86` (main, 2026-10-07) on 2026-10-09, mostly from
> `validator/tournament/`. Unqualified `constants.py:N` means `validator/tournament/constants.py`.
> **Re-verify before acting** — these change between tournaments, and tuning against a stale
> rulebook is its own failure mode.

## Shape of an image tournament

```
R1   one group, every entrant, 3 tasks (flux, ideogram4, z-image)
       └─► top 2 by average rank across the 3 tasks
           (dedup judge + code review of the 2 — either may halt for a human)
R2   one knockout task, family drawn from (qwen-image, krea2)
       └─► winner = boss challenger
Boss 6 tasks vs the boss: krea2 ×2, ideogram4 ×2, qwen-image ×1, z-image ×1
       └─► challenger dethrones only by beating the boss's score by the margin
           on all but at most one task
```

| Rule | Cite |
|---|---|
| Text and image R1 is always one multi-match group | `validator/tournament/tournament_manager.py:180-188`; `constants.py:90-95` |
| R1 = one task per family of `ROUND_ONE_IMAGE_MODEL_TYPES` = (flux, ideogram4, z-image); ≤ 30 pairs → ≤ 3 test images, ≤ 0.75 h | `constants.py:147-154`; `validator/tournament/task_creator.py:349-415` |
| R1 ranking: each task ranked by loss; per miner, positions summed (missing from a task → last + 1); order by (missing any task?, average rank, summed loss, hotkey); top `SMALL_TOURNAMENT_ADVANCE = 2` advance | `validator/tournament/round_results.py:1067-1128`; `validator/tournament/models.py:242-257`; `constants.py:95` |
| **A miner without a valid result on any one R1 task sorts behind every miner who completed all three, whatever its ranks** | `validator/tournament/models.py:255-257`; `round_results.py:1118-1121` |
| Before R2: dedup judge on the R1 winners and a code review of each non-boss entrant; either can hold the tournament for a human | `tournament_manager.py:880-905`; `validator/tournament/dedup_gate.py:129-177` |
| R2 = one knockout task, family `random.choice((qwen-image, krea2))`; for image the winner is the rank-1 (lowest-loss) valid result, and with no valid result there is no winner | `constants.py:156-160`; `task_creator.py:115-119`; `round_results.py:429-431`; `validator/db/sql/submissions_and_scoring.py:495-508` |
| Boss round = 6 tasks (krea2 2, ideogram4 2, qwen-image 1, z-image 1); the boss joins only in the final round | `constants.py:162-169`; `task_creator.py:1144-1180`; `tournament_manager.py:498-503` |
| A boss-round task counts as won only if `challenger ≤ boss − |boss| × 0.01` (lower is better) | `constants.py:258-263`; `validator/tournament/thresholds.py:301-312`; `round_results.py:299-303` |
| Dethrone: the challenger may lose at most one task (required wins = decided tasks − 1, minimum 1); no decided task → the boss retains | `round_results.py:341-344`, `:359-369`, `:393-403` |
| Image boss rounds have no draws (relative margin only) | `constants.py:321-333` |
| Every image task trains on 1×H100 | `validator/tournament/gpu_requirements.py:45-46` |
| Minimum field for text/image: 4 | `constants.py:34` |

Read the table as arithmetic: R1 is flux / ideogram4 / z-image only; qwen-image and krea2 first
appear in R2; krea2 and ideogram4 decide 4 of the 6 boss tasks; and with three R1 tasks, one
failed task outweighs any amount of quality on the other two. Re-derive this every cycle — the
constants move.

## Distinctness / dedup — a shipping gate

| Rule | Cite |
|---|---|
| T0 (identical commit) and T1 (identical normalized source) run over all participants before R1 trains; duplicates are auto-eliminated, the boss is kept | `dedup_gate.py:1-7`, `:103-123`; `tournament_manager.py:1247`; `validator/infrastructure/repo_dedup.py:6-11`, `:184-190` |
| T2: a Claude agent judges pairs of R1 winners (`TOURN_DEDUP_CLAUDE_MODEL`, up to 60 turns and $15 per pair, read-only tools, with the validator's own source checked out to verify claimed differentiators) | `constants.py:136-140`; `repo_dedup.py:335-379`, `:434-458` |
| The judge's rules are public: value changes (any number of them), re-parametrisation, rename/format, and branches on inputs that never exist at training time are **cosmetic**; one confirmed new mechanism that runs at training time makes a pair **distinct**; padding/obfuscation is `drop_evasion` | `validator/infrastructure/repo_dedup_config.json` (`system_prompt`) |
| Tournament scope: a difference that only fires for another tournament type's tasks is dead code | `repo_dedup.py:323-332` |
| The reference baseline is published | `DEFAULT_PARTICIPANT_REPO` / `DEFAULT_PARTICIPANT_COMMIT`, `constants.py:125-126` |

Consequences:
- **Default-off is dead.** Only the env vars that `trainer/runtime.py` sets exist at training
  time (`trainer/runtime.py:235-245`); the judge is told to treat anything gated on another one
  as dead code (`repo_dedup.py:350-356`).
- **Distinct means a new mechanism that runs**, not different numbers in the same knobs.
- A validated win can end up **stranded** on an entry that gets eliminated. After any
  elimination, audit the dead entry and port its wins to the survivor.

## Emissions

| Rule | Cite |
|---|---|
| Image anchor weight 0.25, cap 0.50; the text / image / env split is re-balanced each cycle from rolling entrant counts (window 4, lagged one tournament), floor 0.05, alpha 1.25 | `validator/scoring/constants.py:27-48`; `validator/scoring/emission_balance.py:1-18` |
| Task scores: rank 1 gets `FIRST_PLACE_SCORE` (3); failures, and the bottom 25 % of pools larger than 8, get `SCORE_PENALTY` (−1); everyone else 0 | `validator/scoring/constants.py:4`, `:11-12`; `validator/scoring/tasks.py:115-241` |
| Image participation fee: 0.4 TAO | `constants.py:342-345` |
| A runner-up keeps its share for 7 days | `constants.py:12-14` |

So the value of a track moves with how crowded it is, not only with how well you do.

## Schedule

`TOURNAMENT_SCHEDULE_IMAGE_DAY_OF_WEEK = 0` (Monday) and `TOURNAMENT_SCHEDULE_IMAGE_HOUR = 13`
(`constants.py:21-22`; the timezone is the validator's clock — INFERRED UTC), with a
`TOURNAMENT_INTERVAL_HOURS = 120` (`constants.py:10`). Use it to time the upstream re-read and
the pre-flight audit.

## What to re-read every cycle

`git log` since your last read on `validator/tournament/`, `validator/evaluation/`,
`validator/scoring/`, `trainer/`, `core/training_templates/` and `ops/docker/`; then re-check
every constant on this page. Anything touching scoring, constants, the trainer or evaluation
changes your optimisation target.
