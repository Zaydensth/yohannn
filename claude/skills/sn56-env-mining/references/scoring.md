# How an environment submission is scored

> `file:line` re-checked against `gradients-ai/G.O.D@b49ba86` on 2026-10-09.

Two disjoint machines, selected per environment by `EvalType`, combined afterwards.

## PVP envs — the six OpenSpiel games

**All-play-all round robin inside your group.** Every unordered pair of surviving hotkeys is
evaluated in its own 2-GPU container, one container per pair, all pairs dispatched in parallel
(`validator/scoring/tasks.py:997-1000`, `:1021-1047`; 2 GPUs per pair at
`validator/evaluation/constants.py:121`).

Per environment per pair: **900 s wall clock** (`PVP_MATCHUP_TIME_BUDGET_SECONDS`). Games are
played as **seed pairs** — the same seed twice with the seats swapped — and the deadline is
checked only between complete pairs, so position bias is always balanced.

Per-game outcome comes from OpenSpiel terminal returns: zero-sum games min-max normalise the
player's return and compare to 0.5 (>0.5 win, <0.5 loss, =0.5 draw); general-sum compares raw
returns (`core/pvp/scoring.py:10-35`).

Pair results become a per-environment **win rate**:

```
(wins + 0.5 × draws) / games      pooled across every pair you played in that env
```
`validator/scoring/tournaments.py:107-129`

## INDIVIDUAL envs — intercode, swe_infinite

Mean per-task reward from a solo 1-GPU container, written into the same `eval_loss` field and
fed into the same combiner (`evaluators/intercode.py:1081-1092`). The `[0,1]` range is
**INFERRED from the env servers** — the validator applies no clamp.

## Combining envs — rank quantile, not raw score

Inside each env the miners are **ranked**: lowest 0.0, highest 1.0, ties share the mean
quantile. Then averaged with per-env weights (all default 1.0, equal in practice because task
creation sets them so). Final task score is a number in **[0,1]**, and **higher is better**.
`validator/scoring/tournaments.py:57-104`, called at `validator/scoring/tasks.py:880`

A miner missing from an env scores 0.0 **in that env**.

**Dead code — do not model against it:** `accumulate_points` (3/1/0),
`pvp_results_to_pairwise`, `individual_scores_to_pairwise`, `compute_pvp_tournament_points`.
None is reachable from production code: `compute_pvp_tournament_points` wraps the first two
(`validator/scoring/tournaments.py:205-208`) and is itself called only from tests.

Because it is a rank, **your score depends on the field, not on an absolute bar.** Improving in
a way everyone else also improves buys nothing.

## The harness your model actually plays through

The eval harness is fully readable and reproducible offline: the system prompt and per-game
rules live in `core/pvp/game_prompts.yml` (identical to the copy under
`validator/evaluation/pvp/`).

Turn format is a single tool-calling response
(`core/pvp/bot.py:301-314`):

```
Current state:
...
You are Player N.
Legal actions:
<id> -> <action_to_string>
```

**Memory harness** (`core/pvp/constants.py:37-40`):
- 4 working slots × 128 tokens — reset each game
- 8 long-term slots × 128 tokens — persist across the whole matchup, updated by a post-game
  `reflect()` call

Your 512-token turn budget must cover memory edits **and** the move.

## Determinism

SGLang runs with `--enable-deterministic-inference` at temperature 0.0, seeded from the task's
`eval_seed`; the seed stream and game-variant selection are plain `random.Random` draws
(`core/pvp/sglang_launch.py:28-39`). Given the seed, the whole thing reproduces.

## The LoRA merge rule

- **Individual / env evaluator:** merge happens **iff** the adapter repo contains
  `added_tokens.json`. Plain LoRA without added tokens is served natively as an adapter; a
  non-LoRA repo is served as full weights (`evaluators/environment.py:489-498`).
- **PvP evaluator NEVER merges your own adapter** — it always serves it natively. Merging in
  the PvP path happens only for `base_chain` continuation adapters
  (`validator/evaluation/pvp/__main__.py:77-105`).

The merge is hardened and pinned by test: `trust_remote_code=False` on both loaders, the stale
`_hf_peft_config_loaded` flag cleared before save, snapshot downloads ignore `*.py`, and any
`--trust-remote-code` in the SGLang command raises.
