# Evaluation: measuring what will actually be measured

If your internal metric disagrees in **rank** with the validator's, everything downstream — checkpoint
choice, A/B conclusions, averaging decisions — is built on sand. Fixing the meter comes before tuning.

## Mirror the harness, don't approximate it

Read `validator/evaluation/` and copy the semantics exactly:

- **Masking.** Supervised text scoring is normally completion-only. Score the same tokens the validator
  scores — no prompt tokens, same special-token handling, same truncation rule.
- **Sequence length.** The evaluator has its own rule for the maximum length it uses, sometimes derived
  from the model's own limits. If you train at a very different length than it evaluates at, you have a
  train/eval mismatch that shows up as unexplained loss.
- **Batching must be semantically neutral.** Any throughput trick that changes what a sample can "see"
  (concatenating multiple samples into one sequence, for instance) is fine for training only if the
  attention path truly isolates them — and must be **off** on the eval path unless you have proven it is
  neutral there. When in doubt, evaluate one example per sequence.
- **Same dtype/kernel path** as your final artifact will be loaded with, or you measure a different model
  than you ship.

## Building an honest dev split

1. **De-duplicate first.** Near-duplicates between train and dev inflate your score and hide overfitting.
   A cheap n-gram/MinHash near-dedup is enough.
2. **Size it for signal.** A split of a couple hundred rows gives noisy rankings; too large and you starve
   training. Scale with dataset size, with sensible floor and cap, and know your noise floor — if two
   checkpoints differ by less than it, you cannot rank them.
3. **Stratify.** Sample across the dimensions that vary (length buckets, difficulty proxies, source), so
   the split resembles the whole distribution rather than the head of it.
4. **Keep grouped items together.** All turns of one conversation belong on the same side of the split.
5. **Fix the seed** so runs are comparable.

## Choosing the checkpoint you submit

- Evaluate periodically, but **not uniformly**: very early evaluations cannot contain the best model, and
  each one costs training time. Concentrate them in the region where the best checkpoint plausibly lives.
- **Cap total evaluation cost** as a fraction of wall clock, using the *measured* evaluation duration. An
  eval that is slower than you assumed will quietly eat the run.
- Selecting by dev loss is only as good as the dev split. See above.

## Averaging checkpoints (if you do it)

Averaging several good checkpoints often helps — but only with one discipline:

> **Re-evaluate the average, and keep it only if it actually beats the best individual member.**

An average that is silently accepted without re-evaluation can be *worse* than the best checkpoint you
already had, and you will never know. Accept on a strict improvement, otherwise keep the single best.
Also check that your averaging path applies to the artifact type you actually produce (full weights
versus adapters) — averaging code that silently skips one of them is doing nothing on those runs.

## Per-task-type pitfalls

**Instruct** — Confirm whether the task carries a KL-to-base term. If it does and you ignore it in
training, you are optimising a different objective than the one being scored.

**DPO** — Score candidates with the **evaluator's** beta and reference setup, not your training beta.
Preference collapse is not visible in the training loss; watch a held-out preference metric.

**GRPO** —
1. **Confirm the polarity from the code before every comparison.** Write down the `file:line`.
2. Evaluate two models **in the same call**, because the prompts may be generated per call and
   cross-call comparisons are invalid.
3. Read the actual reward functions shipped with the task; the score is often dominated by one blunt
   term, and knowing which one tells you what "better" means here.

**Continuous-SFT** — Your baseline is the seed model itself. Always compute the seed's score first: if
your run does not beat the untouched seed, you have made it worse, which is a very real outcome when the
learning rate is too hot for an already-converged model.

## Comparing two runs honestly

- Change **one** thing at a time; otherwise you learn nothing about which change mattered.
- Use the same data, the same split, the same seed, the same eval path.
- Know your noise floor. Repeat a config to measure it, and don't celebrate differences below it.
- Prefer **relative** comparisons on the same task over absolute numbers across tasks — losses are not
  comparable between datasets.
- Record everything: config, commit, seed, hardware, wall clock, and the resulting score. An experiment
  you cannot reproduce is an anecdote.
