---
name: sn56-image-mining
description: Operate a Bittensor SN56 (Gradients / G.O.D) IMAGE-track tournament miner end to end — understand the round structure and scoring, set up a VPS and the trainer container, emit training configs that survive the wall clock across every evaluated family, run and evaluate diffusion LoRA tasks (Flux, Z-Image, Qwen-Image, Ideogram 4, Krea 2; SDXL is enum-only and not evaluated), and pull ground truth from the public task API, Grafana-Loki and the uploaded artifacts. Use when the user mentions the image tournament, an image task_id, LoRA / diffusion / Flux / Z-Image / Qwen-Image / Ideogram / Krea / SDXL training, a budget or forfeit question, miner submission, or wants to diagnose why an image run scored badly.
---

# SN56 IMAGE-track mining

This skill is the **domain brain** for the image track: how the competition actually
works, what the validator really measures, and how to run and diagnose an image miner. It
gives you the mechanics and the reasoning framework. It deliberately does **not** hand you
tuned hyper-parameters — those you earn from your own runs, because the constants that win
drift and the *reasoning* does not. (Tuned numbers live in the private memory vault, never here — git history is permanent.)

## The one sentence that matters

> An image task is not "train the best LoRA" — it is **"produce the best adapter you can
> inside a fixed wall clock, on hardware you do not choose, on a dataset you have not
> seen, across an architecture you may not have tuned for, and still hand back a valid,
> loadable checkpoint."**

Almost every loss is a violation of that sentence, not a hyper-parameter mistake.
Internalize it before touching a learning rate.

## How to use this skill

1. **Establish ground truth first.** Everything here should be re-verified against the
   validator source (`gradients-ai/G.O.D`, branch `main`) — it changes. Never act on a
   remembered constant; read the code and cite `file:line`. (The references were rebuilt
   against `b49ba86` on 2026-10-09, after the image evaluator moved to a flow-prediction
   metric.)
2. **Then pick the reference you need** (`references/`):
   - `tournament-mechanics.md` — rounds, families per round, GPU allocation, how R1 ranking,
     the final round and dethroning work, and the dedup / distinctness gate.
   - `validator-scoring.md` — exactly how an image submission is scored, which file is
     scored, and the failures that zero an otherwise good adapter.
   - `training-pipeline.md` — what the validator does from clone to upload (public
     `trainer/` code), the reference ai-toolkit entrypoint, and the seams where a miner is
     allowed to be clever.
   - `architectures.md` — the evaluated families, how the validator routes each one to your
     code, the upstream template defaults, and the per-family evaluation contract.
   - `budget-and-wallclock.md` — the forfeit problem: what the clock is, what eats it inside
     your container, and how to plan a schedule that *finishes*.
   - `failure-modes.md` — the catalogue of total-loss failures, each with a detection
     method, in triage order.
   - `dataset-and-captions.md` — what the validator ships as data: pairs, captions
     (including ideogram4's JSON), trigger words, the train/test split, and how the
     evaluator reads captions.
   - `intel-sources.md` — the public sources and how to query them.

   For VPS setup, matching the trainer image, and the smoke → train → eval ladder, see
   `.claude/playbooks/vps-operations.md`.
3. **Work by data.** Every claim you make to the user should trace to validator code, a
   logged run, a score from the API, or your own measured experiment. "It should help" is
   not a reason.

## Non-negotiables

- **Never submit or transact on-chain for the user.** Registration, submission and any TAO
  movement are the user's action. You prepare; they execute.
- **Never power off or destroy a VPS** unless explicitly asked; you do not know what else
  runs there.
- **Keep LICENSE/NOTICE byte-identical** to upstream.
- **Do not fabricate a score, a rank, or a log line.** If a query returns nothing, say so.
- **Never commit tuned constants, competitor intel, hotkeys, VPS IPs, or post-mortems** —
  the repo is private today, but git history is permanent and a submission repo may have to
  be opened.

## The loop that works

**Observe** (pull real state: task API, container logs, your own metrics) → **Orient**
(explain it against validator code; label each claim VERIFIED or INFERRED) → **Decide**
(rank by expected value; separate "ship on reasoning" from "must measure on GPU") →
**Act** (one change at a time, verified).

Two habits that pay for themselves:
- **Offline-first.** Do every bit of CPU work — dataset prep, config emission, unit tests,
  dry runs, variant preparation — before you rent a GPU.
- **Eliminate total-loss before chasing peak.** A 2% better learning rate is worth nothing
  next to a run that overran its budget and forfeited, or that uploaded an empty adapter.
  Rank your work by "what can make us score zero", then by "what makes us score better".
