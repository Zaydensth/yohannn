# Environment tournament structure

All from `validator/tournament/` in `gradients-ai/G.O.D@main`. Re-verify — these move.
`file:line` re-checked against `b49ba86` on 2026-10-09.

## Rounds

Env round 1 is **always a group round**, never knockout.

| Konstanta | Nilai |
|---|---:|
| `MAX_ENVIRONMENT_GROUP_SIZE` | 5 |
| `SMALL_ENVIRONMENT_GROUP_SIZE` | 4 (round 1, field ≤ 7) |
| `MIN_ENVIRONMENT_GROUP_SIZE` | 2 |
| `ENV_ADVANCE_PER_GROUP` | 1 |
| `ENV_FINAL_ROUND_TASK_COUNT` | 3 |
| `ENV_ENVS_PER_ROUND_MULTIPLIER` | 2 |

`num_groups` shrinks until every group clears the floor. The boss counts toward the group-count
arithmetic but not toward the split (`tournament_manager.py:149-178`).

**Exactly one non-boss miner advances per group**, ties at the cutoff all advancing. If the boss
is in your group and its score ≥ the top challenger's, **that group advances nobody**
(`round_results.py:912-930`). Being drawn into the boss's group is strictly worse than any other
group — a tie keeps the incumbent.

The boss is injected into the **smallest** group in non-final rounds
(`tournament_manager.py:398-400`, `:414-424`) and auto-advances every non-final round that
produced winners (`:680-685`). The tournament reaches the final round as soon as only the boss and
one challenger remain (`:537-540`).

Envs per task = `round_number × 2`, capped at 7 (all environments minus the forced boss env).

## The boss round — a SWE gate, not a margin

Three tasks, all in one group. The single contender must have **zero losses across all three**
**and** at least **tie the boss on the `swe_infinite` task**. Comparison is bare `>` / `<` on the
aggregated score — **no dead zone, no `BOSS_ROUND_WIN_MARGIN`**. Any single loss and the boss
retains (`round_results.py:61-157`).

`FORCED_BOSS_ENVIRONMENT = EnvironmentName.SWE_INFINITE`. It is **excluded from every non-final
round** and pinned to boss-round task 3.

> **The cruellest structural fact in the track:** `swe_infinite` never appears in a scored
> non-final round, yet it is the mandatory dethrone gate. You get **zero in-tournament signal**
> on the one environment that decides the crown. Rehearse it offline or go in blind.

The three boss tasks are **not homogeneous**:

| # | Kind | Envs | Hours |
|---|---|---|---|
| 1 | CONTINUATION on the tournament base | `min(round×2, 7)` | 1.5 |
| 2 | FROM_SCRATCH on a **different randomly drawn model** | sample of the 7-env pool | 3.0 |
| 3 | PREVIOUS_WINNER, `swe_infinite` only | 1 | 2.5 |

A missing or unevaluated contender score on **any** single boss task is an immediate
boss-retains, not a skipped task. When the boss itself has no score, `0` is substituted.

## Hours and GPUs

`ENV_TRAINING_HOURS = 1.5` for every group-round task — the round number is ignored.
`ENV_TRAINING_HOURS_SWE_INFINITE = 2.5`, `ENV_TRAINING_HOURS_BOSS_ROUND_FROM_SCRATCH = 3.0`.

GPU allocation is **round-contingent**, not unconditional 4×H100. Boss tasks 2 and 3 hit the
start-point clause at `gpu_requirements.py:49-53`; task 1 qualifies only via
`environment_count > 4`, and its env count is `min(round_number × 2, 7)`.

## Thresholds that do NOT apply

`PVP_WIN_PCT_THRESHOLD = 0.60` does **not** gate the boss round. It gates only the
analytics/emission-projection display and the env champion's emission boost curve
(`validator/scoring/constants.py:19`).

`BOSS_ROUND_WIN_MARGIN` (0.01) does **not** apply to env crowning either, despite a comment in
`constants.py` implying env uses `PVP_WIN_PCT_THRESHOLD`. Neither is true —
`determine_env_tournament_winner` uses bare comparison.

## Known stale docs and a live bug

- `docs/miner.md:460` says "groups of 2 to 6" — the real cap is 5 (or 4 for small fields).
- An older `docs/miner.md` gave env base weight 0.125 / max 0.175; that text is gone at
  `b49ba86`. The constants are `TOURNAMENT_ENVIRONMENT_WEIGHT = 0.25` and
  `MAX_ENVIRONMENT_TOURNAMENT_WEIGHT = 0.50` (`validator/scoring/constants.py:36`, `:29`).
- **Live bug (VERIFIED by reading, not executed):** the emission-boost formula at
  `performance_calculator.py:202` assumes scores on the 3-points-per-env scale but is fed
  rank-quantile points in `[0,1]`. Do not model your emission projection on it. (The formula is
still at `:202` on `b49ba86`; the input-scale claim was not re-checked — INFERRED.)

`ENVIRONMENT_TASKS_PER_GROUP = 1` (`validator/tournament/constants.py:145`) is defined but **has no reader** — the behaviour is hardcoded
in a loop. Do not treat it as a tunable upstream might flip.
