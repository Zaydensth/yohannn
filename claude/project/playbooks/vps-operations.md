# Playbook — VPS operations (install · smoke · real train · real eval · monitor)

Goal: reproduce the validator's pipeline **1:1** on a GPU box, cheaply. Golden rule:
**CPU-first** — do all dataset prep and format/coverage asserts locally before you spend a
GPU-second. Fill `<VPS_IP>` / paths from memory.

## 0. Connect + inventory (read-only)
```bash
ssh root@<VPS_IP> 'nvidia-smi --query-gpu=index,utilization.gpu,memory.used,memory.total --format=csv,noheader; \
                   docker ps; df -h /; free -g'
```
- **Never power-off / reboot** the VPS — that's the user's call. If a box is unreachable,
  report it; don't try to start it.
- Note the GPU count and VRAM. Image training is VRAM-bound: resolution × batch × the
  chosen architecture decides whether a config fits at all. Match the GPU class the
  validator allocates, or your step-time measurements won't transfer.

## 1. Match the validator container
- Find the exact training + eval **container image** the validator uses (read
  `gradients-ai/G.O.D` build/run code). Build the SAME image from this repo's
  `dockerfiles/`. Do NOT hand-roll a venv that drifts from it.
- **Pin dependency versions** to the image. Mismatched `diffusers` / `transformers` /
  `torch` / `xformers` are a classic crash source.
- Disk fills fast: base checkpoints, VAE, text encoders, and cached latents are large.
  Check `df -h` before a run, not after it fails.

## 2. Stage the base model (once)
```bash
python3 -c "from huggingface_hub import snapshot_download; \
  snapshot_download('<BASE_MODEL>', local_dir='/cache/models/<BASE_MODEL_DIR>')"
```
- Cache it once and point `HF_HOME` at the cache so every later run starts warm. A cold
  download inside the tournament clock is budget you don't get back.

## 3. OFFLINE smoke (CPU — do this BEFORE any GPU run)
Run the dataset prep for a handful of images and **assert**:
- captions exist, are non-empty, and match the format the trainer expects
- the image/caption pairing is correct and no file is silently dropped
- bucketing/resize produces the resolutions you intend
- the emitted training config parses and every path in it exists
- dedup / holdout split behaves as designed and is reproducible

This catches most bugs (wrong format, dead code path, empty dataset) for zero GPU cost.

## 4. Real train (mirror the validator)
- Run the same entrypoint, the same flags, the same GPU count as the validator would.
- **Log completed-steps vs planned-steps at the end of every run.** Anything well short of
  the plan means the clock truncated you — the single most common silent loss.
- Run long jobs detached (`screen`/`tmux` or background) and log to a file you can tail.

## 5. Real eval (deterministic, validator-faithful)
- Use the validator's **own eval harness** so the scoring and any adapter-merge rule match
  the tournament exactly. Don't hand-roll scoring.
- Fix the seed and the sampler; compare candidates on the SAME seed. An image metric
  compared across different seeds is noise.
- Evaluate against a **held-out** set, never the training set.

## 6. A/B multiple variants in one session (avoid GPU idle)
- Because you prepared variants offline, pin **one variant per GPU** and train/eval them
  concurrently.
- Select the winner by the **measured** metric, never by an offline proxy alone.

## 7. Monitor (cheap, event-driven)
```bash
# de-ansi a training/eval log tail
ssh root@<VPS_IP> "sed -r 's/\x1b\[[0-9;]*m//g' <logfile> | tail -40"
```
Watch for: `Traceback`, `CUDA out of memory`, a step counter that stopped advancing while
the process is alive, and the loss trajectory. Prefer **/loop** with a sane interval over
manual babysitting.

## 8. Teardown
```bash
ssh root@<VPS_IP> 'docker ps -aq --filter "name=<YOUR_KIT_PREFIX>" | xargs -r docker rm -f; nvidia-smi'   # only the containers your kit started
```
Clean containers + scratch when done so the box is idle-free. **Do not power it off** —
tell the user it's free.

## Gotchas that actually bite
- **Don't mount managed paths the eval wipes.** Some eval code `rmtree`s well-known paths;
  a bind-mount of your host workspace onto one deletes your host files. Mount ONLY a
  scratch/cache dir.
- **`python -m` uses CWD, not PYTHONPATH** for its package — run from the right dir, or
  bake your patches into the image so the container's copy (not a stale one) runs.
- **Measure step time early and re-plan the schedule from the measurement**, not from an
  assumption. Wall-clock overrun is a forfeit; a shorter schedule that finishes and anneals
  beats a longer one that gets cut.
- **OOM fallbacks are silent.** If the trainer halves the batch on OOM, your effective
  batch — and therefore your LR regime — is not what you configured. Log what it ended with.
- macOS lacks GNU `timeout`/`ls --time-style`; datacenter bandwidth beats a home uplink —
  clone big repos and pull big checkpoints **on the VPS**, not locally.
