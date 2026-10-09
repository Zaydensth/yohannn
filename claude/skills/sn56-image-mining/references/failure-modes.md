# Failure modes — the catalogue

> Validator mechanics cited against `gradients-ai/G.O.D@b49ba86` (main, 2026-10-07), checked
> 2026-10-09. The catalogue itself is method; every threshold in it is yours to measure.

Ranked by cost. The Tier 1 group produces a **forfeit or a base-model score**, which no
amount of hyper-parameter quality can offset. Fix those first; they are also the easiest to
detect once you know what to look for.

---

## TIER 1 — total loss (you score nothing, usually silently)

### 1. Non-zero exit
**What happens:** the trainer container exits non-zero, the host skips the upload entirely, and
the task forfeits — *even though valid checkpoints are sitting on the volume*
(`trainer/runtime.py:1077-1085`, `:1138`). The reference entrypoint runs ai-toolkit with
`check=True`, so a crashed training subprocess becomes exactly this
(`ops/docker/scripts/image_toolkit_entrypoint.py:141`).
**Detect:** container exit status in the trainer logs; nothing uploaded to HF.
**Prevent:** catch everything at the top level of the training path, leave the best valid
checkpoint you have in the output folder, and exit 0. Never add a bare `raise` to that path.

### 2. Wall-clock overrun
**What happens:** the clock expires. The host treats the timeout as success, so whatever is on
disk is uploaded and scored — whichever file `select_lora` happens to pick from it
(`trainer/runtime.py:1086-1092`; `validator-scoring.md`).
**Detect:** log completed-steps versus planned-steps at the end of **every** run. Anything
well short of plan means truncation.
**Prevent:** budget arithmetic is correctness code.
`steps × measured_seconds_per_step + in_container_overhead` must fit inside
`hours_to_complete × 3600` (the clock starts with your container; `budget-and-wallclock.md`),
and a step-time table is only valid for the hardware it was measured on. Measure step time
early and re-plan from the measurement.

### 3. Empty or unloadable adapter
**What happens:** training dies early or never really starts; the output dir holds no usable
weight file; the evaluator's file search returns nothing.
**Detect:** before exit, assert that the output folder holds the adapter file `select_lora` will
pick (`validator-scoring.md`); look for the upload step complaining about an empty directory
(`trainer/containers/uploader.py:183-184`).
**Prevent:** run the dependency-proof and load-proof rungs inside the image you actually
ship, for the hardest architecture you might face.

### 4. A live network call at train time
**What happens:** something resolves a HuggingFace repo-id at runtime. Your container runs on
an internal network with no egress (`trainer/runtime.py:71-77`, `:277`), so the call fails —
straight to a crash if nothing catches it.
**Detect:** grep for repo-id strings reaching any loader; run the smoke test with the network
disabled, for every family.
**Prevent:** every model path must resolve to the staged local directory
(`/cache/models/<owner>--<repo>`, text encoders under `/cache/hf_cache`;
`ops/docker/scripts/image_toolkit_entrypoint.py:27-28`, `:99-128`).

### 5. An architecture wired in only some of the places
**What happens:** a family is handled in some places but missed in others — a Dockerfile route,
your entrypoint's `--model-type` choices, your template switch, or your per-family budget
tables. The task fails at container build or config generation (`architectures.md`).
**Detect:** for each supported arch, walk every routing site and confirm it is present.
**Prevent:** treat "add an architecture" as a multi-file checklist, never a single edit. An
unknown arch should hit a deliberate fail-soft generic path that still attempts a scoreable
submission, not a bare return.

### 6. Writing where you cannot write
**What happens:** the trainer container has `/cache` mounted read-only (the downloader and
uploader get it read-write; `trainer/runtime.py:266`, `:470`, `:820`). Code that works in one
fails in the other.
**Detect:** grep the training path for writes under `/cache`.

---

## TIER 2 — you score, but poorly

### 7. The wrong checkpoint got scored
`select_lora` prefers exactly one `last.safetensors` under `checkpoints/` or `checkpoint/`,
otherwise the **highest-numbered** file, otherwise a lone file, otherwise it fails
(`validator/evaluation/image_artifacts.py:36-46`). Selecting a better checkpoint without
removing the files it would prefer means the evaluator scores the one you rejected.
**Detect:** list exactly what is in the output dir on every exit path, and apply the rules.
**Prevent:** make the final selection the last thing your entrypoint does, after the training
subprocess has exited and written everything it will write; make it a no-op when you have no
measured signal; and remember the uploader's subfolder rule
(`trainer/containers/uploader.py:136-152`).

### 8. Training depth that does not suit the metric
Under the current flow-prediction metric (`validator-scoring.md`), how deep to train is an
empirical question per family and task shape — anything learned under the old pixel metric is
void until re-measured.
**Detect:** score a ladder of saved checkpoints with the validator's own evaluator code, not
just the final one. Compare against the untouched base — if you cannot beat it, you made it
worse.

### 9. A contaminated selection metric
Your offline number does not track the hidden score, so you pick the wrong checkpoint and
draw wrong conclusions from every A/B. The classic version here is trusting the trainer's
own loss: the score is velocity MSE at 16 fixed σ midpoints, with fixed per-image noise seeds,
on held-out images, with and without the caption (`validator-scoring.md`) — not your training
batches.
**Detect:** reproduce the evaluator offline (it is deterministic) and check that your ranking
agrees with a returned score at least once. Disagreement in *rank* is a broken meter.

### 10. Train/eval distribution mismatch
Training in a regime the evaluator never measures — conditioning, resolution handling,
timestep coverage, the base the adapter is applied to. Eval-alignment beats theoretical
correctness.
**Detect:** state your training conditions and the evaluator's contract for that family
(`validator/evaluation/image_flow_adapter.py:23-51`, CFG 1.0, 16 σ midpoints, both prompts,
the eval base in `validator-scoring.md`) side by side and compare them literally.

### 11. Caption handling that ignores what the evaluator prompts with
The evaluator prompts with the test captions exactly as shipped — for ideogram4 that is the
magic-prompt JSON (`dataset-and-captions.md`). Whatever you do to captions for training, know
the difference from what is evaluated and measure its effect. Caption policy is
**per family**, not global.

### 12. A silent OOM fallback
If your trainer or a library falls back on OOM — halving the batch, offloading — your effective
batch, and therefore your LR regime, is not what you configured, and nothing says so.
**Detect:** log what the run *ended with*, not what it was launched with.

---

## TIER 3 — self-inflicted

### 13. Shipping code that never runs
A feature behind a default-off env flag contributes nothing. The host sets only a few env vars
in your container (`trainer/runtime.py:235-245`), so **the coded default is what runs
on-chain** — env-gated-OFF is equivalent to not shipping, and the dedup judge treats it as dead
code (`validator/infrastructure/repo_dedup.py:350-356`). Survival and correctness mechanisms
must be default-ON with a kill-switch.

### 14. Silent no-ops
A dead regex, a swallowed exception, a missing package behind a graceful fallback, inert
plumbing. Every mechanism you ship needs one distinctive log line you can confirm actually
fired.

### 15. Stranded wins on a dead branch
The fix exists — on a branch that is not the submitted commit, or on an entry that got
eliminated. Consolidate into the submission branch and verify the *submitted tree* contains
each fix you believe you have. After any elimination, audit the dead entry for validated wins
and port them.

### 16. Failing the distinctness gate
A measured win can be correctly rejected because the implementation is too close to another
entry's. The dedup judge measures deltas over the shared open-source baseline: changing values
in existing knobs is cosmetic, a new mechanism that runs at training time is distinct
(`tournament-mechanics.md`). Your sibling entries are compared too (T0/T1 over all
participants, T2 over the R1 winners). Build a mechanism of your own; never copy the bytes.

### 17. Tuning against a stale rulebook
The validator changed and you optimised the old objective. Re-read upstream every cycle,
especially anything under `validator/evaluation/`, `validator/scoring/`,
`core/training_templates/` and `ops/docker/`.

### 18. Leaking through logs
Container stdout is publicly queryable. Competitors read your logs exactly as you read
theirs. Never print strategy fields or per-task plan dumps.

---

## The triage order

When a run scores badly, ask in this order and stop at the first one that explains it:

1. Did the container exit 0, and did anything get uploaded? *(1, 3)*
2. Did it finish inside the clock? What fraction of planned steps completed? *(2)*
3. Which artifact actually got scored — the one you selected, or the tail? *(7)*
4. Was the training depth right for this family and task shape, measured under the current
   metric? *(8)*
5. Did it train in the regime the evaluator measures? *(10, 11)*
6. Does your offline metric agree with the returned score? *(9)*
7. *Only now* consider hyper-parameter quality.

Most teams start at 7 and never reach 1. That is why they stay stuck.
