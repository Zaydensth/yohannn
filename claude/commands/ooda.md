---
description: Run one OODA cycle — pull real state, reconcile against validator code, decide a ranked plan, act on the top item.
---

Run our OODA loop for this cycle. Work by data; label every conclusion VERIFIED or INFERRED.

**OBSERVE** — pull real state, do not guess:
- New commits or active feature branches on `gradients-ai/G.O.D` (main) that touch
  `validator/evaluation/`, `validator/scoring/`, `validator/tournament/`,
  `core/training_templates/`, `trainer/`, or `ops/docker/`.
- The last tournament's image tasks and results for my hotkey from
  `api.gradients.io/auditing/tasks/<id>`, plus the container logs for those runs.
- Our own repo state: which branch, what is unmerged, what is uncommitted.

**ORIENT** — explain each result mechanistically against the validator code. For every run
answer, in this order:
1. Did it produce a real, non-empty `checkpoint/last.safetensors`?
2. Did the container exit 0, and did it finish inside the wall clock — what fraction of the
   planned steps completed?
3. Was the promoted checkpoint the *best* one, or just the last one?
4. Was the step budget right for this arch and this task shape?
5. Does our offline proxy agree with the returned score?
6. *Only now* consider hyper-parameter quality.

**DECIDE** — give a ranked plan by expected value. Separate "ship on reasoning alone" from
"must be measured on a GPU". Say what you would NOT do and why.

**ACT** — implement the top items one change at a time, verify each on CPU, and commit with
a message that follows the repo's `WHY (by data) / WHAT / DEDUP / VERIFIED` body format. Do
not batch unrelated changes into one commit.

Then report: what changed, what is still unverified, and whether you need a GPU next — and
if so, the single highest-value experiment to run on it.
