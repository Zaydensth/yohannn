# GPU A/B runbook — TEMPLATE

> Copy this file per GPU session (e.g. `RUNBOOK-<yyyymmdd>-<slug>.md`) in your **private** notes, fill every `<...>`,
> and get the strategist's `FABLE-PLAN: ... POINT=lock VERDICT=GO` line into the plan BEFORE the box is switched on.
> Everything below is prepared on CPU first; treat the harness as untested until arm 0 produced a scored row.
> The session ends with an explicit "the GPU is no longer needed, it can be switched off" to the box's owner.

## 0. Preflight (one time per box)

| Item | Where it comes from | Check |
|---|---|---|
| Checkout of the commit under test | `stage.sh` (pinned SHA, clean tree) | box HEAD == the lock's `pin_sha` |
| Trainer image | built from THAT commit with the production build rule (no cache) | label / tag names the SHA; build time recorded vs the 30-min limit |
| Base models / datasets in the cache | the production downloader (`cell_run.sh`) | dataset zip byte-identical to the archive |
| Held-out data | your private archive (pulled inside the 7-day window) | `verify_stage.py` PASS on the box |
| Evaluator | `build_eval_image.sh` at a REVIEWED G.O.D commit | `god.sha` label == `GOD_SHA` |
| Disk | `df -h`; keep `<N>` GB free | queue's `MIN_FREE_GB` |
| Box idle | `nvidia-smi` shows no process; nothing else scheduled to run (e.g. unattended upgrades) | — |

Every long command runs in `screen` / `tmux`. Bound every wait (no unbounded `ssh` / polling).

## 1. Gates (what makes a SHA a submission candidate)

| Gate | Evidence | Fails when |
|---|---|---|
| G0 plan | `FABLE-PLAN:` GO line in the locked plan + the ledger line | absent, or a later NO-GO / REVISI |
| G1 zero-score | one production-shaped run per roster task at the real hours: artefact present and loadable, wall clock under the hours | any task ships base weights / nothing / an unloadable file, or overruns |
| G2 no regression | paired comparison vs your current shipped baseline on every task's held-out rows | worse than YOUR measured noise floor on ANY task (not on average) |
| G3 smoke | `pre-submit-smoke-test.sh <sha>` exit 0 on the exact SHA to register | anything else (INCONCLUSIVE is not PASS) |
| G4 dedup | `dedup_local.py` (+ the T2 reading) vs the nearest published winner and the boss | DUPLICATE / DROP_EVASION |

Noise floor: `<measure it on arm 0: score one artefact twice; train one config with two seeds>` — write the number
and how it was measured here before reading any arm.

## 2. Arm queue — order is also the order of cut if the box comes late

Control for every arm = the same SHA with the arm's lever at its OFF value, same seed, same task data, same hours, same
evaluator commit; ONE change per arm.

| # | Hypothesis (one sentence) | Task(s) | Lever ON vs OFF | GPU-hours | Read from (rows / log lines) |
|---|---|---|---|---|---|
| ARM 0 | harness check: the replica reproduces the official board (anchors) + noise floor | `<task>` | — | `<h>` | `board.py` delta, repeat delta |
| ARM 1 | `<...>` | `<...>` | `<LEVER=on>` vs `<LEVER=off>` | `<h>` | `<...>` |
| ARM 2 | `<...>` | `<...>` | `<...>` | `<h>` | `<...>` |

Total `<h>` GPU-hours; the first `<k>` arms decide whether the branch can be a candidate at all.
Run arms sequentially on one GPU (two containers on one GPU corrupt step-time measurements).

## 3. Verdict per arm — PRE-REGISTERED (written before any number exists)

- **ADOPT**: better beyond the floor on `<≥ n>` of the tasks it touches AND not worse beyond the floor anywhere -> the
  lever ships default-ON (a default-OFF lever is dead code to the dedup judge and does not run in the tournament).
- **REFUTE**: worse beyond the floor on `≥ 1` task -> the code is REMOVED (not defaulted off), in a commit that carries
  the numbers.
- **UNDECIDED**: everything else -> recorded; not a distinctness claim.

Every arm's evidence (`control / arm / delta / task / seed / sha`) goes to one results file per session, and one line
per verdict to the ledger.

## 4. After the queue

Numbers into the plan's `## RESULTS`; freeze the candidate SHA; G3 + G4 on that SHA; report NEEDS USER with the
40-character SHA and the gate ledger. Pushing and registering are the user's actions.
