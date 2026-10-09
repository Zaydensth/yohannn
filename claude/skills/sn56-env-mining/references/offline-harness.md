# Rehearsing offline — the validator ships the tools

> Paths, line counts and constants re-checked against `gradients-ai/G.O.D@b49ba86` on 2026-10-09.

The "you cannot rehearse PvP" framing is wrong. `ops/tools/evaluation/` contains purpose-built
harnesses, and the prompts and rules are in the repo.

## What is readable and reproducible

- `core/pvp/game_prompts.yml` — the exact system prompt template and every per-game rules block
  the model will see. Identical to the copy under `validator/evaluation/pvp/`.
- `core/pvp/bot.py:301-314` — the exact turn format.
- `core/pvp/constants.py` — turn budget, token cap, memory slot geometry.
- `core/pvp/sglang_parsers.py` — the family → parser mapping your model id must hit.

Because eval is deterministic (temperature 0, `--enable-deterministic-inference`, seeded), a
faithful local replay reproduces the tournament exactly — **once you know the seed**, which is
withheld while the task is live.

## The harnesses

- **`ops/tools/evaluation/pvp_play.py`** (224 lines) — "Manual PvP stepper for the real
  tool-calling harness". Drives `LLMBot` + `SlotMemory` + `core.pvp.tools` directly. This is the
  closest thing to the real thing you can run yourself.
- **`core/pvp/baseline.py`** — plays a model in the real matchup shape: alternates seats for
  fairness, carries one long-term memory across games, and a model-side forfeit scores as a
  loss, mirroring eval.
- **`ops/tools/evaluation/local_environment_eval.py`** / **`basilica_environment_eval.py`** —
  57/58-line hardcoded-constant harnesses (`BASE_MODEL_NAME`, `LORA_MODEL_NAME`, `GAME_TO_EVAL`,
  `RANDOM_SEED`) over the docker/basilica text runners. The dataset argument is a dummy.
  Caveat: `basilica_environment_eval.py` sets `GAME_TO_EVAL='intercode'`, so it routes to the
  **intercode** evaluator, not `environment.py`.

Note that the generic `environment.py` evaluator plays the model against an **MCTS opponent**
through an `/evaluate` HTTP env server — that is **not** the tournament PvP path. Tournaments
route PVP envs to the pvp-evaluator image. Do not tune against MCTS and assume it transfers to
the field.

## A rehearsal ladder that costs almost nothing

1. **Parser proof.** Serve your adapter and confirm the tool-call parser resolves from the model
   id. One request. Catches the #1 zero.
2. **Format proof.** Run `pvp_play.py` for a handful of turns. Confirm every response emits a
   legal `game_action` and fits inside 512 tokens **with** memory edits.
3. **Timing proof.** Measure per-turn latency. Anything approaching 30 s is a forfeit under load.
4. **Baseline proof.** `core/pvp/baseline.py` against the untouched base model. If you cannot
   beat the model you started from, you made it worse.
5. **Field proof.** Only after 1–4, spend GPU on policy quality.

Rungs 1–3 are effectively free and eliminate the entire Tier 1 forfeit class.

## What you cannot rehearse

- The **field**. Your score is a rank against other miners' policies, and you cannot see them
  until the tournament runs. Published `position-1` repos in `gradients-opensource` are the only
  window — use them.
- **`swe_infinite`** in a scored round. It only ever appears in the boss round.
- The **eval seed** while the task is live.
