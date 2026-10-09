# What the validator actually measures

Read `validator/evaluation/` (scoring, the per-type evaluators, and the docker evaluation entry point)
before trusting anything below. The point of this file is to teach you **which number you are optimising**
— the single most common cause of "we trained well and still lost" is optimising a different quantity
than the one being scored.

## The universal rule

The validator evaluates your **uploaded artifact** on a **held-out set you never see**, using **its own
harness**, not yours. Therefore:

- Your training loss is not the objective. Your internal eval is not the objective either — it is an
  *estimator* of the objective, and an estimator can be biased.
- Anything that makes your internal number diverge from the validator's number is a bug, even if
  training is perfect. Hunt those aggressively.

## Per task type

### Instruct (supervised)
- Scored as a **loss on held-out completions — lower is better**, and typically **completion-only**: the
  prompt tokens are masked out and only the response contributes.
- **Implication:** if your training or your internal eval scores the prompt tokens too, you are measuring
  a different quantity than the validator. Mirror the masking exactly.
- Some instruct tasks additionally carry a **KL term against the base model** with its own coefficient —
  i.e. the score rewards staying close to the original model while improving. When that flag is set, a
  model that drifts far from base is penalised even if its CE is good. Check for the flag and mirror the
  term in training if you want the training objective to match the scoring objective.

### DPO
- Scored with the standard preference objective on held-out pairs, using **the validator's own beta**,
  not yours. Lower is better.
- **Implication:** if you select checkpoints using a different beta than the evaluator, you can pick the
  wrong checkpoint. Read the eval beta and score your own candidates with it.
- Preference training is asymmetric in risk: too cold is recoverable, too hot collapses the policy in a
  way no later step recovers.

### GRPO
- Scored from the **reward functions supplied with the task**, combined with a **KL penalty** against the
  reference policy.
- ⚠️ **Get the polarity right, from the code, every time.** GRPO is the classic place where teams invert
  the comparison and then "improve" their model in the wrong direction. Read the sorting code in the
  scoring module and confirm which end wins before you compare two runs. Write the answer down with the
  `file:line` you read it from.
- The reward is often dominated by one crude term (e.g. a length target). Look at the actual reward
  functions in the task payload — they tell you what the score really rewards, which is frequently not
  "better text".
- Because prompts may be generated stochastically, **comparing two models across two separate eval runs
  is invalid**. Evaluate both in the *same* call so they face identical prompts.

### Continuous-SFT / Chat
- Scored like a supervised task, but the starting point is an already-trained model.
- **Implication:** the useful regime is *annealing*, not exploring. A learning rate appropriate for a
  fresh base model will damage a converged one, and "more epochs" is usually worse rather than better —
  measure this rather than assuming either way.

## The checks that can zero a good model

These are as important as the loss, because they turn a competitive run into nothing.

### Is-it-actually-a-finetune checks
The validator sanity-checks that your submission is a genuine fine-tune of the specified base — including
that the architecture recorded in the config still matches. Two traps:
- Newer transformers versions may alias model classes on load, so `save_pretrained` can record a
  *different* architecture string than the base had. Restore/verify the config's architecture field on
  the artifact you upload.
- An empty or metadata-only upload directory fails outright. Always assert your checkpoint contains real
  weight shards before the run ends.

### Duplicate / quality checks
There is a multi-tier duplicate system, roughly: exact-hash → normalised-content hash → a **semantic
pairwise judge** that runs at a round boundary and asks whether two repos differ in **function**, not in
authorship. What matters for you:
- **Cosmetic differences are not differences.** Renaming, reformatting, or reordering does not make a
  distinct entry.
- **Dead code is not a feature.** A "new lever" that ships behind a default-off flag never executes and
  is read as no change at all. If you believe in a mechanism, ship it on by default; if you don't, don't
  count it as your differentiator.
- Judged **at a round boundary**, only the code that has actually *run* by then is evidence. A difference
  confined to a task type that has not occurred yet is weak evidence of distinctness.
- When two entries are judged near-duplicate, the resolution can penalise one of them — so the downside
  is not symmetric with the upside.

### Reproducibility / audit
Runs are logged publicly (see `intel-sources.md`). Assume everything you emit — your CLI args, your
config, your losses — is visible to everyone, including your competitors, and that the reverse is also
true.

## Building an internal metric you can trust

1. **Mirror the validator's masking and preprocessing**, not an approximation of it.
2. **Hold out honestly.** A dev split that leaks near-duplicates from train will read far better than the
   hidden set. De-duplicate before splitting.
3. **Make the split representative.** Too small a sample and your ranking is noise; stratify by whatever
   dimension varies most (length, difficulty, source).
4. **Do not let an optimisation contaminate the measurement.** Any trick that changes how examples are
   batched or masked for speed must not be applied to the eval path unless it is provably neutral there.
   Speed belongs on the training half; the measuring half stays clean.
5. **Calibrate once with real evidence:** train something, score it internally, then compare against the
   score the validator gives that same artifact. If they disagree in *rank*, your meter is broken — fix
   the meter before you tune anything else.
