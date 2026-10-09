# How an image submission is actually scored

> Re-derived from `gradients-ai/G.O.D@b49ba86` (main, 2026-10-07) on 2026-10-09. Citations are
> `file:line` in that commit; unqualified file names are under `validator/evaluation/`.
> **Re-verify before acting.** This page replaces an older description (img2img pixel MSE with a
> 0.25 / 0.75 text / no-text weighting) that no longer matches the code.

## The metric — flow-prediction error on held-out latents

For each held-out test image (`evaluators/diffusion.py:91-111`, `:129-159`):

1. The image is resized so its long side is 1024 (LANCZOS) and centre-cropped to multiples of
   16; its id is the sha256 of its pixels plus size (`image_test_data.py:59-67`, `:108`).
2. It is encoded once with the family's VAE in float32, using the posterior mean, never a sample
   (`image_flow_adapter.py:54-70`, `:98-107`; `image_encoder.py:7-28` for z-image), then scaled
   into model space (`evaluators/diffusion.py:100-101`). Candidate LoRAs never touch this VAE.
3. **Cases:** 16 strata with σ at the midpoints `(k + 0.5) / 16`, and 16 noise draws per
   stratum — 256 cases per image (`evaluators/diffusion.py:41-42`; `denoising_mse.py:17-39`).
   Each noise seed is `sha256("42:<image hash>:<stratum>:<index>")`, so every miner faces the
   same noise (`denoising_mse.py:12-14`). Noisy input `(1 − σ)·x + σ·ε`; target velocity `ε − x`
   (`denoising_mse.py:35-36`).
4. The base model plus your LoRA predicts the clean latent at each σ; the evaluator converts it
   back to a velocity `(x_t − x̂₀) / σ` and takes the mean squared error per case
   (`denoising_mse.py:42-52`; `image_denoising.py:19-31`). A non-finite loss fails the candidate.
5. This runs twice per image: once conditioned on the image's caption, once on the empty string,
   both at CFG 1.0 with no negative conditioning (`evaluators/diffusion.py:135-145`).
6. Per image, the 256 case losses are averaged; per mode, the per-image means form two lists,
   `text_guided_losses` and `no_text_losses` (`evaluators/diffusion.py:146-149`, `:170-173`).
7. A repeatability check re-runs the first case set; a mismatch beyond rtol 1e-5 / atol 1e-7
   fails the candidate (`evaluators/diffusion.py:160-169`).

Lower is better.

## The weighting — an even split

```
score = 0.5 × mean(text_guided_losses) + 0.5 × mean(no_text_losses)
```

`DIFFUSION_TEXT_GUIDED_EVAL_WEIGHT = 0.5` (`constants.py:6`), combined in
`validator/scoring/tasks.py:309-329`. The result becomes the miner's `test_loss` and
`synth_loss` (`validator/scoring/tasks.py:722-725`, `:770-779`) and is ranked ascending
(`validator/scoring/tasks.py:175-176`).

## Per-family evaluation contract

- Only five families have a contract (`image_flow_adapter.py:23-51`); anything else raises
  (`evaluators/diffusion.py:75-76`), and `sdxl` is rejected before dispatch
  (`docker_evaluation.py:379-380`).
- The model must be unit-noise rectified flow, or the evaluator refuses
  (`image_flow_adapter.py:118-128`).
- qwen-image is evaluated with sampling shift 3.0; flux conditioning carries guidance 3.5
  (`image_flow_adapter.py:29-40`, `:109-124`).
- Per-family text encoders and VAEs are fixed by the contract — table in `architectures.md`.
- Evaluation runs on A100s (`constants.py:39`; `docker_evaluation.py:409-413`); training runs on
  H100 (`validator/tournament/gpu_requirements.py:45-46`).

## Which base your adapter is applied to (`image_artifacts.py:80-98`)

- **ideogram4:** always `Comfy-Org/Ideogram-4` → `diffusion_models/ideogram4_fp8_scaled.safetensors`,
  not the task's `model_id`.
- **Every other family:** the task's base repo (`augmented_model_id` if set, else `model_id`;
  `validator/scoring/tasks.py:423`, `:438`) must hold exactly one root-level `.safetensors` larger
  than 5 GiB, or the evaluation fails.
- **z-image:** the VAE comes from the task repo's `vae/` subfolder at the resolved revision
  (`evaluators/diffusion.py:56-63`).
- A submitted repo equal to the base is recorded as a non-finetune
  (`validator/scoring/tasks.py:424-429`).

## Which file gets scored (`image_artifacts.py:36-46`)

`select_lora` takes the repo's files under `checkpoints/` or `checkpoint/` that end in
`.safetensors`, sorted, and:

1. if **exactly one** is named `last.safetensors` → that file;
2. otherwise the highest trailing number matching `[-_](\d+)\.safetensors$`;
3. otherwise, if there is exactly one file → that file;
4. otherwise → "No unambiguous LoRA checkpoint", and the candidate fails.

It reads the repo at the revision current when evaluation runs (`image_artifacts.py:54-59`).
Your files arrive under `checkpoints/` because the uploader puts them there
(`trainer/constants.py:58`; `trainer/runtime.py:1140`), after its own subfolder rule
(`trainer/containers/uploader.py:136-152`, `:179-181`).

**What you keep and how you name it decide what is scored.** List the uploaded repo and apply the
four rules by hand before you trust a run. Note that the upstream templates say `name: last`
while the reference entrypoint renames the run to the expected repo name
(`ops/docker/scripts/image_toolkit_entrypoint.py:89`): check which file names your trainer
actually writes (how ai-toolkit names its saves is library behaviour — INFERRED until you see a
real upload).

## How your LoRA is applied (`image_flow_adapter.py:130-154`)

- ComfyUI `LoraLoader` at strength 1.0 on both the model and the text encoder.
- Any `lora key not loaded` / `NOT LOADED` log line, or zero model patches, fails the candidate
  ("Incomplete LoRA application").
- If your LoRA patches the text encoder, the conditioning is recomputed with your patched encoder
  (`evaluators/diffusion.py:125`, `:137-138`).

## Failures, and how they score

| Failure | Result | Cite |
|---|---|---|
| No expected repo name, or the repo is missing on HF | failed result with that `score_reason` | `validator/scoring/tasks.py:599-622` |
| Any exception for your candidate (download, LoRA keys, non-finite loss, repeatability) | `Image prediction evaluation failed (<Type>)` → `Evaluation failed: …` | `evaluators/diffusion.py:181-192`; `result_processing.py:93-94`; `validator/scoring/tasks.py:690-713` |
| Any test image without a caption, or a broken base | the whole evaluation fails | `image_test_data.py:105-106`; `evaluators/diffusion.py:222-229` |
| Scores | rank 1 gets `FIRST_PLACE_SCORE` (3); failed submissions get `SCORE_PENALTY` (−1) when valid ones exist | `validator/scoring/tasks.py:115-241`; `validator/scoring/constants.py:11-12` |

## What this means in practice

- **The metric is reproducible.** σ grid, noise seeds, resize, VAE, CFG 1.0 and both prompts are
  deterministic and public — your dev metric can be the real metric. Calibrate once against a
  returned score on a held-out set you archived before trusting your own numbers.
- **Your trainer's loss is not this metric** (different noise draws, timestep sampling, images and
  prompt handling). Compare candidates with the evaluator's code, never with training loss.
- **Both halves count equally:** the empty-prompt half and the captioned half.
- **Depth, learning rate, checkpoint selection:** measure each under this metric. Rules of thumb
  learned under the old pixel metric are void until re-measured.
