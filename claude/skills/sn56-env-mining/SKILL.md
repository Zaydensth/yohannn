---
name: sn56-env-mining
description: Operate a Bittensor SN56 (Gradients / G.O.D) ENVIRONMENT-track tournament miner end to end — understand that env tasks ship no dataset and are scored head-to-head by PvP win rate combined via rank quantile, avoid the tool-call-parser and per-turn forfeit paths that zero a run, plan against the SWE-gated boss round, and rehearse offline with the validator's own PvP harness. Use when the user mentions the environment tournament, an env task_id, PvP / self-play / OpenSpiel games (goofspiel, clobber, leduc, gin, othello, liars), intercode or swe_infinite, or wants to diagnose why an environment run scored badly.
---

# SN56 ENVIRONMENT-track mining

Read `sn56-core` first — round structure, dedup, emissions, and the shared method live there.
This skill covers only what is different about environment, and it is different in ways that
break intuitions carried over from text and image.

## The one sentence that matters

> An environment task is not "train on the data" — there **is** no data. It is **"turn a
> fixed wall clock into a policy that out-plays other miners' policies at games you may not
> have seen, served through a tool-calling harness that forfeits the moment your output does
> not parse."**

## Four things that surprise everyone

1. **No dataset.** The task payload is a list of environment names, weights, and an eval seed.
   The "dataset" handed to the container is a 1000-row proxy file of
   `{"prompt": "Interact with this environment."}`. You generate your own training signal —
   that is the entire game.
2. **You are scored against other miners, not against a target.** PvP envs run all-play-all
   round robin inside your group. Your score depends on the field.
3. **Rank quantile, not points.** Per env you get a win rate; across envs the miners are
   ranked and the quantiles averaged. Final task score is in `[0,1]`, **higher is better** —
   the opposite direction from text and image. `accumulate_points` (3/1/0) is dead code.
4. **The most common zero has nothing to do with training quality** — it is an unmapped
   SGLang tool-call parser. See `references/forfeit-modes.md` before anything else.

## How to use this skill

1. **Establish ground truth.** Re-verify everything here against `gradients-ai/G.O.D@main`;
   cite `file:line`. The env code moves faster than text or image. (The references were written
   2026-08-18 and their `file:line` re-checked against `b49ba86` on 2026-10-09.)
2. **Then pick the reference:**
   - `references/task-contract.md` — what the container receives and must hand back, the
     sidecar, the CLI, model prep.
   - `references/scoring.md` — PvP win rate, INDIVIDUAL envs, rank-quantile combination, the
     memory harness the model plays through.
   - `references/forfeit-modes.md` — every way to score zero, in triage order. **Read first.**
   - `references/tournament-structure.md` — rounds, group sizing, the SWE-gated boss round,
     hours and GPUs.
   - `references/offline-harness.md` — the validator's own tools for rehearsing without a
     tournament.
3. **Work by data.** Every claim traces to validator code, a logged run, a score from the API,
   or your own measured experiment.

## The loop that works here

Observe → Orient against the code → Decide → Act, one change at a time. Ranked by what it
protects:

1. **Forfeit paths** — parser mapping, per-turn budget, output cap, container exit
2. **Wall clock into usable training signal** — self-play generation throughput
3. **Policy quality against the actual field** — not against a static baseline
4. **Metric honesty** — does your offline win rate predict the tournament's?
5. **Only then** hyper-parameters

## Non-negotiables

- Never submit or transact on-chain. Never power off a VPS. LICENSE/NOTICE byte-identical.
- Do not fabricate a score, a rank, or a log line.
- Secrets and tuned constants never enter the submission repo — they live in your private memory
  vault (`<VAULT>`).
- **Ship an adapter, not merged weights**, unless you know exactly why you are merging.
