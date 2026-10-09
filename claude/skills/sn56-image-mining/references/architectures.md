# Architectures — what changes per family

> Re-derived from `gradients-ai/G.O.D@b49ba86` (main, 2026-10-07) on 2026-10-09. Every row is
> VERIFIED by reading the cited file unless marked INFERRED. Line numbers drift: re-check before
> you rely on one.

## The enum vs what is actually played

`ImageModelType` has six values (`core/models/image_models.py:4-10`): `flux`, `sdxl`, `z-image`,
`qwen-image`, `ideogram4`, `krea2`. Only five are live:

| Where | Families | Cite |
|---|---|---|
| Synthetic task pool | flux, z-image, qwen-image, ideogram4, krea2 | `validator/tasks/synthetics/scheduler.py:57-63` |
| Downloader `--model-type` choices | the same five | `trainer/containers/downloader.py:527-536` |
| Evaluator family contracts | the same five | `validator/evaluation/image_flow_adapter.py:23-51` |
| `sdxl` | rejected before evaluation | `validator/evaluation/docker_evaluation.py:379-380` |

Which family lands in which round is in `tournament-mechanics.md`.

## How the validator routes a family to your code

1. **Dockerfile.** flux → `ops/docker/standalone-image-trainer.dockerfile` (legacy
   `dockerfiles/standalone-image-trainer.dockerfile`); every other family →
   `ops/docker/standalone-image-toolkit-trainer.dockerfile` (legacy `dockerfiles/…`). The first
   path that exists in **your** repo wins; neither → the task fails before training
   (`trainer/runtime.py:896-914`, `trainer/constants.py:2-12`). G.O.D's own tree at `b49ba86`
   ships only the toolkit Dockerfile (VERIFIED by listing `ops/docker/`), so make sure your repo
   has a flux Dockerfile at one of the two flux paths.
2. **CLI.** Both images receive the same arguments: `--task-id --model --dataset-zip
   --model-type --expected-repo-name --hours-to-complete [--trigger-word]`
   (`trainer/runtime.py:217-233`). You cannot add arguments.
3. **Downloader extras**, staged into `/cache` before your container starts
   (`trainer/containers/downloader.py:557-601`):

   | family | extra staged |
   |---|---|
   | z-image | `ostris/zimage_turbo_training_adapter` → `zimage_turbo_training_adapter_v2.safetensors` |
   | qwen-image | `ostris/accuracy_recovery_adapters` → `qwen_image_torchao_uint3.safetensors` |
   | ideogram4 | text encoder `Qwen/Qwen3-VL-8B-Instruct` + `ostris/ideogram_4_unconditional_lora` (`…_r16.safetensors`) |
   | krea2 | text encoder `Qwen/Qwen3-VL-4B-Instruct` |
   | flux | standalone-checkpoint normalisation of the base (`downloader.py:118-267`) |

4. **Template.** The reference entrypoint picks `core/training_templates/base_diffusion_<family>.yaml`
   (`ops/docker/scripts/image_toolkit_entrypoint.py:20-26`).

## Upstream template defaults — the shared baseline

These are what the reference trainer ships, i.e. the baseline every entry starts from and the
baseline the dedup judge measures your deltas against (`tournament-mechanics.md`).

Shared by all five (`core/training_templates/base_diffusion_*.yaml`): ai-toolkit
`diffusion_trainer`, config `name: last`, `training_folder: /app/checkpoints`, dataset
`folder_path: /dataset/images` with `caption_ext: txt`, `save_format: diffusers`, `batch_size: 1`,
`optimizer: adamw8bit`, `noise_scheduler: flowmatch`, `dtype: bf16`,
`gradient_checkpointing: true`, LoRA `linear` / `linear_alpha` 32 / 32,
`resolution: [512, 768, 1024]`.

| | flux | ideogram4 | krea2 | qwen_image | zimage |
|---|---:|---:|---:|---:|---:|
| `steps` | 2000 | 2000 | 2000 | 3000 | 2000 |
| `lr` | 1e-4 | 4e-4 | 1e-4 | 1e-4 | 1e-4 |
| `timestep_type` | weighted | linear | linear | weighted | weighted |
| `save_every` | 250 | 200 | 200 | 250 | 250 |
| `max_step_saves_to_keep` | 4 | 100 | 100 | 4 | 4 |
| `caption_dropout_rate` | 0.05 | 0.05 | 0.05 | — | — |
| `cache_latents_to_disk` | true | false | false | true | true |
| `quantize` | false | false | false | true (uint3 file) | true (qfloat8) |
| `train_unet` / `train_text_encoder` | true / false | true / false | true / false | true / false | not set |

Family-specific keys:
- **flux** — `is_flux: true`.
- **ideogram4** — `trigger_word: null`, `lokr_full_rank: true`, `lokr_factor: -1`,
  `content_or_style: balanced`, `loss_type: mse`, `weight_decay: 1e-4`, EMA off,
  `unconditional_lora_path: /cache/hf_cache/ideogram_4_unconditional_lora_r16.safetensors`.
- **krea2** — the ideogram4 block without the unconditional LoRA, plus
  `do_differential_guidance: true`, `differential_guidance_scale: 2`.
- **qwen_image** — `arch: qwen_image`, `qtype: uint3|/cache/hf_cache/qwen_image_torchao_uint3.safetensors`,
  `cache_text_embeddings: true`, `low_vram: true`.
- **zimage** — `arch: zimage:turbo`, LoRA `conv` / `conv_alpha` 16 / 16,
  `assistant_lora_path: /cache/hf_cache/zimage_turbo_training_adapter_v2.safetensors`.

What the reference entrypoint overrides at run time
(`ops/docker/scripts/image_toolkit_entrypoint.py:80-134`): `name` → the expected repo name,
`training_folder` → `/app/checkpoints/<task_id>`, `trigger_word` → the task's trigger, dataset
folder → the flattened `/dataset/images`, model path → `/cache/models/<owner>--<repo>`, the
ideogram4/krea2 text-encoder path, and the krea2 VAE path (it refuses to start if
`<model>/vae` is missing).

Two things to notice, and to measure yourself rather than assume:
- `max_step_saves_to_keep` is 4 on flux/qwen/z-image and 100 on ideogram4/krea2. Whatever
  selection you do can only choose among the files that still exist. (How ai-toolkit prunes is
  library behaviour; read the pinned commit, `ops/docker/standalone-image-toolkit-trainer.dockerfile:5`.)
- `quantize: true` on qwen-image and z-image changes the memory profile and what the adapter
  is trained against.

## What the evaluator does per family

From `validator/evaluation/image_flow_adapter.py:23-51`, `validator/evaluation/evaluators/diffusion.py:45-67`
and `validator/evaluation/image_artifacts.py:80-98`:

| family | eval text encoder(s) | eval VAE | extra | base the LoRA is applied to |
|---|---|---|---|---|
| flux | `t5xxl_fp16` + `clip_l` (`comfyanonymous/flux_text_encoders`) | `ae.safetensors` | guidance 3.5 | task base repo: one root `.safetensors` > 5 GiB, loaded as UNet |
| z-image | `qwen_3_4b` (`Comfy-Org/z_image_turbo`) | the task repo's `vae/` subfolder (diffusers) | — | task base repo, same rule |
| qwen-image | `qwen_2.5_vl_7b_fp8_scaled` (`Comfy-Org/Qwen-Image_ComfyUI`) | `qwen_image_vae` | sampling shift 3.0 | task base repo, same rule |
| krea2 | `qwen3vl_4b_fp8_scaled` (`Comfy-Org/Krea-2`) | `vae/qwen_image_vae` (`Comfy-Org/Krea-2`) | — | task base repo, same rule |
| ideogram4 | `qwen3vl_8b_fp8_scaled` (`Comfy-Org/Ideogram-4`) | `vae/flux2-vae` (`Comfy-Org/Ideogram-4`) | — | **fixed** `Comfy-Org/Ideogram-4` `diffusion_models/ideogram4_fp8_scaled.safetensors` |

Consequences, by reading:
- **ideogram4 adapters are scored on a fixed Comfy fp8 checkpoint, not on the task's base repo.**
- For the other four, the evaluator needs **exactly one** root-level `.safetensors` over 5 GiB
  in the base repo, or the whole evaluation fails (`image_artifacts.py:89-95`).
- The model must be unit-noise rectified flow, or the evaluator refuses (`image_flow_adapter.py:118-128`).
- The eval text encoders and VAEs above are fixed per family; train against what you choose, but
  know what you are scored with.

Full scoring detail: `validator-scoring.md`.

## Per-family notes (public code only)

- **krea2** — the reference entrypoint injects local text-encoder and VAE paths and refuses to
  start without `<model>/vae` (`image_toolkit_entrypoint.py:116-128`). Your container has no
  internet (`trainer/runtime.py:71-77`, `:277`), so every component must resolve to the staged
  local path.
- **ideogram4** — its captions are the validator's canonical "magic prompt" JSON (see
  `dataset-and-captions.md`), and the evaluator uses that same caption string as the prompt for
  the captioned half of the score (`validator/evaluation/evaluators/diffusion.py:135-138`).
- **z-image** — eval VAE comes from the task repo's `vae/` subfolder at the resolved revision
  (`validator/evaluation/evaluators/diffusion.py:56-63`).
- **qwen-image** — eval applies AuraFlow sampling shift 3.0 (`image_flow_adapter.py:35-40`,
  `:118-124`); the task clock gets +0.5 h (`validator/tasks/synthetics/diffusion.py:719`,
  `:734-735`).
- **flux** — eval conditioning carries guidance 3.5 (`image_flow_adapter.py:29-34`, `:109-116`);
  separate Dockerfile route (above).

## Handling a family in your miner

A family has to be wired everywhere it is routed, or that family silently forfeits:

1. the validator's routing for it (Dockerfile path, downloader choices — above);
2. your entrypoint's `--model-type` choices (mirror the reference `TEMPLATE_BY_MODEL_TYPE`,
   `image_toolkit_entrypoint.py:20-26`, `:36`);
3. every routing site in your own code (enum, template switch, any per-family budget table);
4. a test that runs your argument parsing and config emission for every family on CPU.

An unknown family should hit a deliberate fail-soft generic path that still produces a
scoreable adapter, rather than returning empty.
