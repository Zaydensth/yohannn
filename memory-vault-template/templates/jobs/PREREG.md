# PREREG_<n> — <one-line question> — DRAFT

**Status: DRAFT.** At lock, change this line to `**Status: LOCKED <yyyy-mm-dd hh:mm UTC>**`.
**Nothing in this file may change after the lock except the `## RESULTS` section.** The first
score must not be read before the lock.

<Replace this line, BEFORE locking, with `FABLE-PLAN: ` followed by the strategist's FABLE line
from `<track>/jobs/FABLE_LEDGER.md`. This placeholder deliberately does not start with
`FABLE-PLAN:` so an unfilled template can never pass the G0 / chain-script check.>

Written <date> from <plan file / strategist consultation>. Repo `<miner repo>`, branch `<branch>`,
SHA `<sha>`; validator G.O.D `main@<sha>`; trainer image `<tag>` built from `<sha>`.

**Locking means:** (a) block 0 is finished (box provisioned, data + models staged, preflight
clean); (b) any calibration the decision rules need is scored; (c) the tests of the decision
script pass on the box's own python; then `touch PREREG_<n>.LOCKED` with a file manifest (md5 of
the decision script, the chain script, the queues, the arm files, the calibration outputs), the
image label, and a copy of the `FABLE-PLAN:` line.

**Units:** <state once, use everywhere — e.g. d = ln(score_arm / score_ref), negative = better;
show ratio-% only in brackets>.

---

## 1. Question — and what this session can and cannot decide

<hypothesis; what result would refute it; what is explicitly out of scope>

## 2. Cells

| cell | arm | the ONE lever changed | control | tasks | seed | draws |
|---|---|---|---|---|---|---|

### 2.1 Held fixed for every cell

<data, split, seed policy, eval path, validator SHA, image SHA, GPU class>

### 2.2 Literal checks per cell

<exact strings that must appear (or must be ABSENT) in each run's log or emitted config — matched
literally (`grep -F`), never as a regex>

## 3. Order, hours, truncation

<run order; wall-clock budget per cell; what is dropped first if time runs out — decided now>

## 4. Power — n from σ, stated before any run

<σ inputs and where they were measured (your own repeated runs); the n they imply; what the
plan can and cannot detect at that n>

## 5. Decision rules — LOCKED before any score is read

<mechanical rules, implemented in `<decide script>`; metric; threshold; per-task vs pooled>

- No arm is added after data. No threshold moves. No data-dependent extra draw.
- <what result would refute the hypothesis>

## 6. Schedule (UTC)

## 7. Box checklist (block 0 — none of this is optional)

- [ ] <provisioned, disk free, no background apt / unattended-upgrade>
- [ ] <image built from the exact SHA; data and weights staged>
- [ ] <preflight clean; decision-script tests green on the box>
- [ ] `touch PREREG_<n>.LOCKED` with the manifest. **Only then** read a score.

## 8. Operating the chain

<launch; monitor (event-driven, not sleep loops); stop conditions; abort and make-safe steps>

## 9. What is recorded whatever the verdict

<per cell: score, gate result, seed, image SHA, wall time, steps completed vs planned, artefact
check; the interval of the decision statistic, including when it fails>

## RESULTS

<the only section that may be edited after the lock: raw scores, rule-by-rule outcomes, final SHA,
open items for the next cycle>
