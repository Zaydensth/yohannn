# Failure modes — the catalogue

Ranked by cost. The top group produces a **score of zero or a base-model score**, which no amount of
hyper-parameter quality can offset. Fix those first; they are also the easiest to detect once you know
what to look for.

---

## TIER 1 — total loss (you score nothing, often silently)

### 1. Empty or invalid checkpoint
**What happens:** training dies (or never really starts), the output directory holds no weight shards,
the upload step fails.
**Common causes:** a dependency missing from *your* Dockerfile; a model that needs remote code being
loaded without it; an exception in the load path; running out of disk.
**Detect:** assert non-empty weight shards before exit; in logs, look for the upload step complaining
about an empty directory.
**Prevent:** run the dependency-proof and load-proof rungs (see `vps-and-runs.md`) inside the image you
actually ship, for the hardest model class you might face.

### 2. Deadline kill recorded as success
**What happens:** the clock expires; whatever exists is scored; the run is logged as successful. You lose
without any error.
**Detect:** log completed-steps versus planned-steps at the end of every run. Anything well short of the
plan means truncation.
**Prevent:** measure step time early and plan the schedule to *finish* (see `hp-reasoning.md`).

### 3. Non-finite gradients from step one
**What happens:** loss becomes NaN immediately; the optimizer writes NaN; the model never moves and you
effectively submit the base model.
**Common cause:** forcing a model into a precision/kernel combination it cannot support — small models
stored in fp32 are the classic case, since some mixed-precision paths have no loss scaler and a clipped
infinity becomes NaN.
**Detect:** watch grad norm and loss for the first ~50 steps. A NaN at step 1 is a configuration bug, not
a learning-rate bug.
**Prevent:** decide precision and attention implementation from the model's *own* config, and keep a
conservative fallback for the fragile classes.

### 4. Distributed deadlock
**What happens:** ranks disagree about what to do next; a collective never completes; the job hangs until
the deadline.
**Common causes:** per-rank OOM handling; a search or probe loop that prunes differently per rank;
conditional logic keyed on something rank-local.
**Detect:** steps stop advancing while the process stays alive.
**Prevent:** synchronise every decision that affects tensor shapes or control flow across ranks; keep
optional machinery off on multi-GPU paths you cannot test.

### 5. Unloadable architecture
**What happens:** an unfamiliar `model_type` raises on load or in a patching step keyed by architecture.
**Detect:** load-proof rung; look for key errors on the model type in logs.
**Prevent:** treat "unknown architecture" as a first-class case with a conservative path — plain loading,
standard attention, no exotic kernels — rather than an error.

### 6. Failing the artifact sanity checks
**What happens:** the upload is technically fine but the validator rejects it (e.g. the recorded
architecture no longer matches the base).
**Prevent:** verify the artifact's config against the base before finishing, and re-load your own saved
checkpoint as a final check.

---

## TIER 2 — large quality loss (you score, but poorly)

### 7. Under-training
Too few optimizer steps to converge — from a step-time misestimate, a too-small budget assumption,
excessive evaluation overhead, or a throughput configuration far slower than necessary.
**Detect:** compare achieved steps against a reasonable target for the data size; compare your throughput
against what the same hardware should deliver.

### 8. Un-annealed schedule
The run completes but the learning rate never came down, because the schedule was planned for more steps
than were achievable. A model cut at high LR loses to an annealed one **even with a better training
loss** — this is the most under-appreciated failure in the entire list.
**Detect:** log the final learning rate as a fraction of peak, and the completed/planned ratio.

### 9. Wrong LR regime for the model's state
A fresh base model and an already-converged model want very different learning rates. Applying a base
model's rate to a continuation damages it; applying a continuation's rate to a fresh model under-trains.
**Detect:** compare against the seed model's own score — if you cannot beat the untouched seed, you made
it worse.

### 10. A contaminated selection metric
Your dev score does not track the hidden score, so you pick the wrong checkpoint, accept a bad average,
and draw wrong conclusions from every A/B. Usually caused by leakage between train and dev, a split too
small to rank, or applying a training-only optimisation to the eval path.
**Detect:** compare your internal ranking against actual returned scores at least once. Disagreement in
rank is a broken meter.

### 11. Over-fitting
More passes over the data than it supports; training loss keeps falling while held-out loss rises.
Especially easy on small datasets with generous budgets, and on continuations that are already converged.
**Detect:** the gap between train and dev loss widening over time.

### 12. Train/eval mismatch
Training at a different sequence length, template, or masking than the evaluator uses. The model is
optimised for a distribution it is not tested on.
**Detect:** state your training masking and length rules side by side with the evaluator's and compare
them literally.

---

## TIER 3 — self-inflicted

### 13. Fragmented effort
Splitting a budget across many trials/restarts so that no single run trains long enough. One well-planned
run usually beats several short ones inside a fixed clock.

### 14. Shipping code that never runs
A feature behind a default-off flag contributes nothing and, at judging time, is indistinguishable from
no change at all. Decide: ship it on, or don't count it.

### 15. Fragmented branches
The fix exists — on a branch that is not the submitted commit. Consolidate everything into the submission
branch and verify the *submitted tree* contains each fix you believe you have.

### 16. Tuning against a stale rulebook
The validator changed and you optimised the old objective. Re-read upstream every cycle.

---

## The triage order

When a run scores badly, ask in this order:

1. Did it produce a real checkpoint? *(Tier 1)*
2. Did it finish its schedule? What fraction? *(2, 8)*
3. Were the gradients finite throughout? *(3)*
4. How many optimizer steps did it actually take? *(7)*
5. Was the LR regime right for this model's state? *(9)*
6. Does my dev metric agree with the returned score? *(10)*
7. *Only now* consider hyper-parameter quality.

Most teams start at 7 and never reach 1. That is why they stay stuck.
