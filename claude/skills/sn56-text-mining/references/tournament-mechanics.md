# Tournament mechanics (text track)

Everything here lives in `gradients-ai/G.O.D` (the canonical validator; other orgs host mirrors).
**Re-verify before you rely on it** — these constants are tuned by the subnet team every few weeks.
Where to look:

| What | Where |
|---|---|
| Round composition, task counts, model size bands | `validator/tournament/constants.py` |
| Who wins the final round / dethrone rule | `validator/tournament/round_results.py` |
| Win-margin comparison helper | `validator/tournament/thresholds.py` |
| GPUs granted per task | `validator/tournament/gpu_requirements.py` |
| Emission weights, decay, payout ranks | `validator/scoring/{constants,weights,tournaments}.py` |
| Hour budget + training-side constants | `validator/tasks/datasets/constants.py` (e.g. `MAX_TRAINING_HOURS`), `validator/constants.py`, `validator/tasks/…/constants.py` (paths as of `b49ba86`, 2026-10-09) |
| The trainer/eval containers | `trainer/`, `validator/evaluation/` |

## Shape of a tournament

A tournament is a **knockout bracket** run on a weekly cadence, ending in a **final round** against the
sitting champion.

- **Round 1** — the wide round. Several tasks, many miners per task. This is the gate: most entrants die
  here, so R1 performance decides whether anything else you built ever runs.
- **Middle rounds** — the field narrows quickly; fields can be as small as two miners, so a single
  failure is elimination.
- **Final round ("boss round")** — the surviving challenger versus the reigning champion across a fixed
  mix of task types. The champion is represented internally by a placeholder hotkey that resolves to the
  real defender.

**Read the current numbers from `constants.py`** — task counts per round and the final-round mix are
named constants (look for the round/task-distribution and continuous-SFT lineage constants).

## The task types you must handle

| Type | What it is | What the miner must produce |
|---|---|---|
| **Instruct** | Supervised fine-tune on instruction/response data | A fine-tuned model (or adapter) |
| **DPO** | Preference optimisation on chosen/rejected pairs | Same |
| **GRPO** | Reward-driven RL with supplied reward functions | Same |
| **Continuous-SFT / Chat** | Continue training a model that is **already trained** (often a prior tournament's output, sometimes a custom architecture) | Same |

Continuous-SFT is the type most miners handle worst, for three structural reasons: the seed is already
converged (so the "normal" LR is far too hot), the seed may be a **custom architecture** requiring
`trust_remote_code` and extra kernel packages, and it is granted the largest hour budget, which means the
schedule is long enough to be truncated if you plan it badly.

## Hardware you are given (not chosen)

GPU count is decided by the validator per task, typically from **effective model size** with multipliers
for the more expensive task types, and some task types are pinned to a fixed multi-GPU allocation.
Consequences you must design for:

- Your code runs on **1, 2 or 4+ GPUs** depending on the task. Anything you build must work under
  distributed training or degrade cleanly — a feature that deadlocks a collective on 4 GPUs is worse
  than not having it.
- You cannot validate every shape. Assume the shapes you cannot test will happen, and make every
  optional feature **fail soft**: it must degrade (fast kernel → slower kernel; search → formula;
  packing → simpler packing) and never crash or disable training.

## The wall clock is the real opponent

Each task carries `hours_to_complete`. The validator derives it from measured throughput on the prep
side, with only a small overhead allowance, and there is a hard maximum. When the deadline hits, whatever
weights exist are what gets scored — and a deadline kill can be recorded as a *successful* run, so you
can lose silently while your logs look fine.

Design rules that follow directly:
- **Measure, don't guess, your step time.** Plan the schedule from a measured seconds-per-step early in
  the run, not from a formula involving batch size and sequence length.
- **Plan to finish.** A learning-rate schedule that is still near its peak when the clock stops is
  effectively an un-annealed model. It will lose to a properly annealed one *even when its training loss
  is lower*. Prefer finishing a shorter schedule over truncating a longer one.
- **Reserve time for the ending.** Final save, adapter merge and upload are not free. Budget them.

## Winning the final round

The dethrone condition is deliberately hard, and it has two parts you must read from
`round_results.py`: a **count** of tasks the challenger must win, and a **per-task margin** (the
challenger must beat the champion by a relative margin, not merely tie). There is also typically a
**hard gate on specific task types** — a subset the challenger must win *all* of, where a failed or
skipped task counts against them.

Practical reading of that: **do not optimise your average.** Find the gated task type and make sure you
can always complete it, because losing one of those ends the run regardless of how well everything else
went. Ties and failures resolve in the champion's favour.

## Economics (check before you add entries)

- **Only the finalists are paid**, in a steeply skewed split; everyone eliminated earlier receives a
  negligible participation weight. Reaching the final pair is the only outcome with real value.
- **Every entry costs a participation fee** in TAO, recurring per tournament.
- The champion's emission **decays over their reign** toward a floor, so a long-seated champion is
  economically softer than a fresh one, and dethroning **resets the winner to full weight**.
- A second entry only helps if its failure modes are genuinely *uncorrelated* with the first's, and only
  one of your entries can occupy the challenger seat. Two forks of the same codebase mostly buy you the
  same lottery number twice — and see the duplicate checks in `validator-scoring.md` before you try.

## What to re-verify every week

1. New commits on the validator's `main` (scoring, constants, trainer, evaluation).
2. Active feature branches — they show what lands next.
3. Whether the hour cap, GPU thresholds, task mix or emission split changed.

An hour of reading upstream is worth more than a day of tuning against rules that already moved.
