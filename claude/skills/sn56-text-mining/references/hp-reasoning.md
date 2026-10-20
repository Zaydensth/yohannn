# Reasoning about hyper-parameters

Deliberately **no tuned constants**. Constants drift with the model zoo, the budgets and the rules;
the reasoning does not. Copying someone's numbers gives you their answer to a question you did not ask.

The one framing to keep: you are not choosing "the best hyper-parameters", you are choosing **the best
hyper-parameters reachable inside a fixed wall clock on unknown hardware**.

---

## Learning rate

**What it depends on** (in rough order of importance):
1. **The model's state.** A fresh base model explores; an already-fine-tuned or continued model anneals.
   These want materially different rates, and confusing them is the single most damaging LR error.
2. **Model scale.** Larger models want smaller rates. A widely used starting point is inverse-square-root
   scaling in parameter count; treat it as a prior to be corrected, not a law.
3. **Effective batch size.** Larger batches tolerate (and usually want) higher rates, but the relationship
   is sub-linear — naive linear scaling overshoots.
4. **Task type.** Preference and RL objectives are far more fragile than supervised fine-tuning and
   generally want lower rates and tighter safety bounds.
5. **The weights themselves.** Statistics of the actual weight tensors (e.g. a typical magnitude across
   the main projection groups) carry real signal about the scale the model is comfortable with, and — 
   unlike names or metadata — survive anonymisation of the model.

**How to build a policy, in order of increasing sophistication:**
- *Level 0*: one constant per task type. Terrible on unusual shapes, but never catastrophic.
- *Level 1*: a formula from size and task type. Predictable; fails predictably; a fine baseline.
- *Level 2*: formula seeded from **measured weight statistics** — more robust to anonymised or unusual
  models than any name-based lookup.
- *Level 3*: **measure the loss response** early in the run (a handful of steps at the candidate rate) and
  correct the estimate from the observed curvature.
- *Level 4*: a real in-run search over a small band, scored on the actual objective, with weights restored
  between trials.

Levels 3–4 buy the most on unfamiliar models, and cost the most in complexity and multi-GPU risk. Do not
build level 4 until levels 0–2 are solid and level 3 has proven itself.

**Bounds matter more than the point estimate.** A clamp that rejects absurd values converts a
catastrophic run into a mediocre one. Set an upper bound from the fragility of the task type and a lower
bound from "does this train at all".

**Be wary of stacking correctors.** Multiplying together several independently calibrated adjustments
compounds their errors in ways nobody audited. One coherent formula plus one measurement generally beats
four heuristics multiplied together.

**A specific trap:** a "curvature" correction derived from the initial loss can invert its own sign when
the seed model has been perturbed (some validators augment weights), because a damaged model reports a
high initial loss that looks like a sharp landscape. Prefer measuring the *response to training* over
inferring from a single initial value.

---

## Schedule

**The floor.** A decay-to-zero schedule spends its last stretch barely learning; a floor keeps some
progress to the end. More importantly, if the clock truncates you, the floor determines where you land.

**The completion requirement.** This is the part most people miss:

> A schedule that does not complete is worse than a shorter schedule that does.

Plan the length from a **measured** step time, not a formula:

```
usable      = (budget - elapsed) * safety_fraction     # leave room for eval, save, upload
affordable  = usable / measured_seconds_per_step
planned     = affordable * slight_over_plan            # so the curve isn't flat at the end
```

Slightly over-planning is right: the risk is asymmetric. Under-planning finishes early and wastes budget;
over-planning by a lot leaves you un-annealed. And if you correct a schedule mid-run, correcting *down*
is far more valuable than correcting *up* — shrinking rescues a truncated run, while growing merely
converts spare budget into extra epochs you may not want.

**Warmup** should scale with the run's length. A fixed constant is simultaneously too long for a short
run and pointless for a long one. A small fraction of total steps, bounded at both ends, works.

---

## Batch size and accumulation

Two different quantities:
- **Per-device batch** is a *memory* decision — as large as fits, after accounting for activations,
  optimizer state and checkpointing.
- **Effective batch** (per-device × devices × accumulation) is a *statistical* decision — it sets how much
  gradient noise each update carries.

Two failure directions:
- **Too small**: noisy updates, unstable training.
- **Too large**: too few optimizer updates inside the budget — you smooth the gradient beautifully and
  then take forty steps.

So bound it from **both** sides: a floor on effective batch for gradient quality, and a ceiling driven by
"how many optimizer updates will this run actually get". When the two conflict, update count usually
wins, because a model that has barely stepped cannot be good.

And whenever you change effective batch, revisit the learning rate — they are coupled.

---

## Epochs and steps

Epochs are a *proxy*; optimizer steps are what matters. Think in steps, then express it as epochs if the
dataset makes that meaningful.

- **Fresh base + small data**: several passes are usually fine and often necessary.
- **Large data**: one pass may be plenty; more may over-fit.
- **Continuation from a converged model**: the useful amount is typically small — this is annealing, not
  exploring. Verify rather than assume in either direction.

Never let an epoch *floor* survive contact with the clock: forcing more passes than the budget can anneal
guarantees truncation, which is the worse failure.

---

## Sequence length and packing

**Length**: derive from the data's actual token-length distribution (a high percentile, not the max, not
the mean), bounded by the model's context and by what the evaluator uses. Training far longer than the
data needs is pure waste; shorter than the data needs truncates content.

**Packing** (concatenating short samples into one sequence) can multiply throughput several times over on
short-form data. It is only safe if the attention path genuinely isolates the packed segments — the
mechanism must prevent one segment attending to another *and* reset positional information per segment.
If your attention implementation cannot do that, packing silently corrupts training by letting unrelated
examples condition each other.

Two rules:
1. **Degrade rather than disable.** Losing packing entirely can cost you the budget; a simpler, safer
   packing mode is usually better than none. But you must know which mode you are in.
2. **Never let packing touch the evaluation path** unless you have proven it is neutral there. Throughput
   belongs on the training half; the measuring half stays clean.

---

## Precision, kernels, and other throughput levers

Everything here converts to **more optimizer steps inside the budget** — that is the only reason to care.

- **Precision**: reduced precision is standard, but decide from the model's own stored dtype. Some small
  models are genuinely fragile in reduced precision.
- **Fast attention kernels**: large wins where supported; they have architecture requirements. Have a
  fallback and know which one you are on.
- **Fused kernels**: usually free throughput on supported families — but some fuse the loss computation
  and therefore do not expose logits, which breaks any objective that needs them.
- **Gradient checkpointing**: trades compute for memory; enables a larger batch or a larger model.
- **Optimizer choice**: memory-efficient optimizers can be the difference between fitting a model and not.

Every one of these should be a **ladder with a defined fallback**, chosen from the model's own config,
never from its name.

---

## How to decide anything here

1. **Reason from the mechanism** — what does this actually change about the optimisation?
2. **Bound it** — what values are obviously unsafe? Clamp those away first.
3. **Measure** — one controlled comparison beats ten opinions.
4. **Keep the reasoning, not the constant.** Write down *why*; the number will change next month.
