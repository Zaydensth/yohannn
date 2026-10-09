# VPS, containers, and the run ladder

Goal: reproduce the validator's environment closely enough that "it worked on my box" means something,
and spend as little GPU time as possible getting there.

## Choosing a box

- **1×H100 80GB** covers the overwhelming majority of useful validation: dependency proofs, load tests,
  short training runs, evaluation, throughput measurement. Rent this by default.
- **Multi-GPU** is only genuinely required to test distributed behaviour (sharding, collectives, the
  fixed multi-GPU task types). It is expensive and often unavailable — so **design so that you do not
  need it**: fail-soft ladders, rank-synchronised decisions, and features that degrade rather than crash.
- Check disk before anything else. Model caches, checkpoints and container layers are tens of GB, and a
  full disk fails in confusing ways mid-run.

## First-time setup

```bash
nvidia-smi                      # driver + GPU visible?
df -h                           # disk headroom (want 100GB+ free)
docker info | head -20          # docker present, and can it see the GPU runtime?
```

Then: install the NVIDIA container toolkit if `docker run --gpus all … nvidia-smi` fails, and set up a
persistent cache directory you will mount into containers so you download a model once, not once per run.

**Always run long jobs inside `screen` or `tmux`.** An SSH drop must not kill a 4-hour training run.

```bash
screen -S train      # start; detach with Ctrl-A then D
screen -r train      # reattach later
```

## Build the trainer image the same way the validator does

Build **your own repo's Dockerfile**, because that is what the validator will build. Do not test inside a
hand-rolled environment — you will validate a dependency set that is not the one you ship.

```bash
docker build -f dockerfiles/<your-trainer>.dockerfile -t my-text-trainer:test .
```

Watch the build for: the exact library versions resolved, any dependency-resolver conflict warnings, and
whether every package you *think* you install actually installed. A silently skipped install becomes a
model that will not load, which becomes an empty checkpoint.

**Kernel/extension packages are version-coupled to your torch build.** Prebuilt wheels are published per
(CUDA, torch, python, ABI) combination — pick the one matching *your* base image rather than copying a
URL from elsewhere, and keep a build-from-source fallback so the image still builds if a wheel is
missing.

## Offline behaviour

The trainer runs without internet. Before you burn a real run:

- Force local-only model/tokenizer resolution in the environment so any accidental remote lookup fails
  fast instead of hanging.
- Grep your code for anything that passes a **repo id** where a **local path** is available; those are the
  calls that try to reach the network.
- Make sure experiment tracking is in offline mode; it should write locally, never block on a server.

## The run ladder — cheapest proof first

Never jump straight to a full training run. Each rung answers one question and costs a fraction of the
next.

**Rung 0 — CPU only, no GPU.** Config emission for a range of synthetic tasks: tiny/large models, 1/2/4
GPUs, short/long budgets, every task type. Assert the emitted config is sane and the projected wall clock
fits. Most bugs die here for free.

**Rung 1 — dependency proof (any GPU, ~2 min).** Inside your built image, import every package your
hardest model needs. This single check catches the "empty checkpoint" class of failure outright.

**Rung 2 — load + forward (1 GPU, ~15 min).** Load the actual model the way your trainer loads it
(including remote code if applicable) and run one forward pass. This proves the kernels *execute*, not
merely that they import.

**Rung 3 — micro-train + save (1 GPU, ~20 min).** A handful of optimizer steps, then save, then **list the
output directory and assert weight shards exist**. Reload the saved checkpoint. This proves the whole
load → step → save → reload chain.

**Rung 4 — scaled rehearsal (1 GPU, hours).** A real run at a scaled-down budget with your real config
path. Watch: loss actually decreasing, no NaN, measured step time, evaluation overhead as a fraction of
the budget, and — critically — **what fraction of the planned schedule completed**.

**Rung 5 — evaluate.** Score the artifact with a harness that mirrors the validator (see
`evaluation.md`), and compare against a baseline you already understand.

## Monitoring a live run

Watch these, in this order of importance:

1. **Is it still alive** — steps advancing, not stuck on a collective.
2. **Loss finite and decreasing** — a NaN at step 1 usually means precision/kernel mismatch, not a bad LR.
3. **Measured step time** — multiply by planned steps: does it still fit the budget? If not, the schedule
   needs re-planning *now*, not at the end.
4. **Memory headroom** — an OOM at hour three costs the whole task.
5. **Evaluation overhead** — evals that consume a large share of wall clock are stealing training steps.

## Teardown

Save the artifacts you will want later (final config, logs, metrics, the checkpoint), then stop the
container. **Do not power off or destroy the machine unless the user asks** — and always tell the user
when a GPU is idle but still billing.
