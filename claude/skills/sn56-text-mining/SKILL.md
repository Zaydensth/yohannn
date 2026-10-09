---
name: sn56-text-mining
description: Operate a Bittensor SN56 (Gradients / G.O.D) TEXT-track tournament miner end to end — understand the round structure and scoring, set up a VPS and the trainer container, emit training configs that survive the wall clock, run and evaluate instruct / DPO / GRPO / continuous-SFT tasks, and pull ground truth from the public task API, Grafana-Loki and W&B. Use when the user mentions the text tournament, a text task_id, instruct/DPO/GRPO/chat (continuous-SFT) training, miner submission, or wants to diagnose why a text run scored badly.
---

# SN56 TEXT-track mining

This skill is the **domain brain** for the text track: how the competition actually works, what the
validator really measures, and how to run and diagnose a text miner. It gives you the mechanics and the
reasoning framework. It deliberately does **not** hand you tuned hyper-parameters or someone else's
recipes — those you earn from your own runs, because the constants that win drift and the *reasoning*
does not.

## The one sentence that matters

> A text task is not "train the best model" — it is **"produce the best model you can inside a fixed
> wall clock, on hardware you do not choose, on data you have not seen, and still hand back a valid
> checkpoint."**

Almost every loss is a violation of that sentence, not a hyper-parameter mistake. Internalize it before
touching a learning rate.

## How to use this skill

1. **Establish ground truth first.** Everything here should be re-verified against the validator source
   (`gradients-ai/G.O.D`, branch `main`) — it changes. Never act on a remembered constant; read the code
   and cite `file:line`. See `references/tournament-mechanics.md` for where each rule lives.
2. **Then pick the reference you need:**
   - `references/tournament-mechanics.md` — rounds, task types, GPU allocation, hour budgets, how the
     final round and dethroning work, participation costs.
   - `references/validator-scoring.md` — exactly how each task type is scored, plus the duplicate/quality
     checks that can zero an otherwise good submission.
   - `references/training-pipeline.md` — what the container actually does from clone to upload, and the
     seams where a miner is allowed to be clever.
   - `references/vps-and-runs.md` — VPS setup, building/matching the trainer image, offline mode, and the
     smoke → train → eval ladder.
   - `references/evaluation.md` — mirroring the validator's eval locally, per-task-type pitfalls, and how
     to make an internal metric that actually predicts the hidden one.
   - `references/intel-sources.md` — the public sources and how to query them: the task API, the
     container logs, the run tracker, and the uploaded model repos (`training_args.bin` for exact final
     hyper-parameters, `trainer_state.json` for the eval trajectory and whether a run actually annealed).
   - `references/failure-modes.md` — the catalogue of total-loss failures, each with a detection method.
   - `references/hp-reasoning.md` — how to reason about LR, schedule, batch, epochs and packing from
     first principles plus measurement, instead of copying numbers.
3. **Work by data.** Every claim you make to the user should trace to validator code, a logged run, a
   score from the API, or your own measured experiment. "It should help" is not a reason.

## Non-negotiables

- **Never submit or transact on-chain for the user.** Registration, submission and any TAO movement are
  the user's action. You prepare; they execute.
- **Never power off or destroy a VPS** unless explicitly asked; you do not know what else runs there.
- **Keep LICENSE/NOTICE files intact** in any repo derived from the reference miner.
- **Do not fabricate a score, a rank, or a log line.** If a query returns nothing, say so.

## The loop that works

**Observe** (pull real state: task API, container logs, your own metrics) → **Orient** (explain it against
validator code; label each claim PROVEN or HYPOTHESIS) → **Decide** (rank by expected value; separate
"ship on reasoning" from "must measure on GPU") → **Act** (one change at a time, verified).

Two habits that pay for themselves:
- **Offline-first.** Do every bit of CPU work — config emission, unit tests, dry runs, variant preparation
  — before you rent a GPU. GPU time should only be spent on things that genuinely require a GPU.
- **Eliminate total-loss before chasing peak.** A 2% better learning rate is worth nothing next to a run
  that silently produced no checkpoint. Rank your work by "what can make us score zero", then by "what
  makes us score better".
