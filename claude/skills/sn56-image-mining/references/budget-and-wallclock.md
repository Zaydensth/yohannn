# Budget and wall clock — the forfeit problem

> Mechanics re-derived from `gradients-ai/G.O.D@b49ba86` (main, 2026-10-07) on 2026-10-09. The
> arithmetic is yours; every number you put into it must be measured on the hardware you
> actually get.

Wall-clock overrun and a non-zero exit are the two ways to score nothing, and no amount of image
quality compensates for either.

## What the clock actually is (VERIFIED by reading)

| Fact | Cite |
|---|---|
| Image tasks always train on 1×H100 | `validator/tournament/gpu_requirements.py:45-46` |
| `hours_to_complete` is 0.5 h (10 pairs), 0.75 h (11–30 pairs) or 1.0 h (31–50 pairs), +0.5 h on qwen-image — computed from the formula and constants | `validator/tasks/synthetics/diffusion.py:688-701`, `:719`, `:733-735`; `validator/tasks/synthetics/constants.py:61-72` |
| On the trainer host: downloader → build of your image (no cache, 30-minute build timeout) → your container | `trainer/runtime.py:951-986`, `:93-131`; `trainer/constants.py:41` |
| The kill timer is `hours_to_complete × 3600` s and starts when **your container** starts; download and build are outside it | `trainer/runtime.py:927`, `:1061-1067`; `trainer/cleanup.py:41-55` |
| Exit 0 → upload | `trainer/runtime.py:1074-1076`, `:1138-1152` |
| Non-zero exit → **nothing is uploaded** | `trainer/runtime.py:1077-1085`, `:1138` |
| Timer fires → container killed, run counted as **success**, whatever is in the output folder is uploaded | `trainer/runtime.py:1086-1092`, `:1120-1125`, `:1138-1152` |
| Container limits per GPU: 110 GB RAM, 24 CPUs; 8 GB shared memory below 4 GPUs | `trainer/constants.py:34-35`; `trainer/runtime.py:79-90`, `:253` |

The reference entrypoint runs ai-toolkit with `check=True`, so a crashed training subprocess
becomes a non-zero container exit — and therefore an empty submission
(`ops/docker/scripts/image_toolkit_entrypoint.py:141`).

## The arithmetic

```
planned_steps × measured_seconds_per_step + in_container_overhead  ≤  hours_to_complete × 3600
```

`in_container_overhead` is everything inside your container that is not an optimizer step:
dataset extraction and any preprocessing you add (captioning, filtering, latent or
text-embedding caching), model load, checkpoint writes, and your final select/prune step. It
grows with the dataset and with every pass you add, so measure it per family; never carry a
constant over from someone else's run.

**Treat this as correctness-critical code, not tuning.** A step-time table measured on one GPU
under-estimates on a slower or throttled one; the plan then cannot finish and the run is killed.

## The two ways the clock kills you

**1. Hard overrun.** The container is killed at the deadline, the run counts as a success, and
whatever is in the output folder at that moment is uploaded. Whether that helps you depends
entirely on whether the folder held a scoreable artifact at that moment — so it has to be
scoreable at **every** point of the run, not just at the end. Which file the evaluator then
picks is decided by `select_lora` (see `validator-scoring.md`).

**2. Silent truncation.** The run finishes inside the clock but far short of the planned
schedule. Nothing errors; you shipped an under-trained adapter, or one whose schedule never
annealed.

Both are detected the same way: **log completed steps against planned steps at the end of every
run**, and treat a large gap as the finding.

## How deep to train — measure it yourself

The metric changed (see `validator-scoring.md`). Any rule of thumb about training depth that was
learned under an older metric is void until re-measured. Find the depth that helps under the
current metric by scoring a ladder of saved checkpoints with the validator's own evaluator code,
on held-out sets you archived, against the untouched base. Remember that the upstream
`max_step_saves_to_keep` (4 on flux / qwen-image / z-image, 100 on ideogram4 / krea2;
`architectures.md`) decides which checkpoints still exist to choose from.

## Planning a schedule that finishes

1. **Measure step time on the hardware you will actually get** (1×H100), early in the run, and
   re-plan from the measurement rather than from a table.
2. **Budget backwards from the deadline**, not forwards from a step count.
3. **Keep the overhead honest** — scale it with dataset size and family.
4. **Make every exit state scoreable.** At any moment the output folder should hold the
   checkpoint you currently intend to ship, and nothing `select_lora` would prefer over it.
5. **Verify the estimate offline.** For every family and every possible `hours_to_complete`
   (0.5 / 0.75 / 1.0, and +0.5 on qwen-image), assert the inequality at the worst case. This is
   a CPU test; run it in pre-flight (`/preflight`).

## Detection checklist

Run these against any finished task before blaming hyper-parameters:

- Did the container exit 0?
- Did it hit the timeout, or finish on its own?
- Completed steps / planned steps — what fraction?
- Applying `select_lora`'s rules to the uploaded `checkpoints/` folder, which file gets scored —
  and is it the one you intended?
- Was the shipped checkpoint chosen by a rule you measured, or is it just the last one written?
- Did the LR schedule actually anneal, or was it cut at high LR?

Only when all six are clean does a hyper-parameter question make sense.
