# The training pipeline — clone to upload

> The validator-side flow, re-derived from `gradients-ai/G.O.D@b49ba86` (main, 2026-10-07) on
> 2026-10-09 (`trainer/`, `ops/docker/`). How your own trainer works inside the container is
> yours to design.

What happens when a task lands, and where a miner is allowed to be clever.

## The two layers

There are two things people call "the entry point", and confusing them wastes time:

1. **Host-side trainer service** — `trainer/` in G.O.D, a FastAPI app (`trainer/asgi.py`). It
   receives the request, clones **your** repo at the registered commit, builds your image, runs
   the containers and uploads the result (`trainer/README.md:1-21`). None of it is importable
   inside your container.
2. **Your container** — whatever your Dockerfile's `ENTRYPOINT` runs. The upstream reference for
   the ai-toolkit families is `ops/docker/standalone-image-toolkit-trainer.dockerfile` +
   `ops/docker/scripts/image_toolkit_entrypoint.py`.

Your entry point must accept exactly the arguments the host sends
(`trainer/runtime.py:217-233`); you cannot add any.

## Control flow (VERIFIED by reading `trainer/runtime.py`)

```
start_training_task                                              runtime.py:917
 ├─ downloader container   /cache read-write, default network    :951-971, :425-491 (mount :470)
 │    base model → /cache/models/<owner>--<repo>                  training_paths.py:16-23
 │    dataset zip → /cache/datasets/<task_id>_tourn.zip           training_paths.py:42-43
 │    family extras (adapters, text encoders)                     downloader.py:557-601
 ├─ docker build of YOUR repo   no cache, 30-minute timeout      :973-986, :93-131; trainer/constants.py:41
 │    Dockerfile by family: flux → standalone-image-trainer.dockerfile,
 │    others → standalone-image-toolkit-trainer.dockerfile
 │    (ops/docker/ first, then legacy dockerfiles/)              :896-914
 ├─ YOUR container   /cache READ-ONLY, /app/checkpoints read-write,
 │                   internal network (no egress), cap_drop ALL  :199-297 (:264-267, :275-277), :71-77
 │    clock = hours_to_complete × 3600 s from container start    :927, :1061-1067
 └─ uploader container   only after exit 0 or a timeout; never after a non-zero exit
      uploads /app/checkpoints/<task_id>/<expected_repo_name>
      into checkpoints/ of a public repo on the validator's HF org
                                                                 :1070-1092, :1138-1158, :804, :1140;
                                                                 trainer/constants.py:58;
                                                                 validator/infrastructure/service_constants.py:6
```

**The upload is exit-code gated.** Exit 0 and the timeout both count as success; a non-zero exit
ships nothing, even with good checkpoints on disk (`trainer/runtime.py:1074-1092`, `:1138`).

## What the uploader does with your output folder (`trainer/containers/uploader.py`)

- If a subfolder contains `.safetensors`, it uploads **that subfolder** instead of the whole
  folder — the first match in `os.listdir` order, which is arbitrary (`:136-152`, `:179-181`).
- It refuses an empty or metadata-only folder; the upload then fails and the task counts as
  failed (`:80-99`, `:183-184`; `trainer/runtime.py:1155-1158`).
- It rewrites `adapter_config.json` `base_model_name_or_path` and the README `base_model:` line
  to the task model (`:32-77`, `:186`).
- It replaces the repo's contents (`delete_patterns=["*"]`) and skips `trainer_state.json`,
  `training_args.bin` and optimizer / scheduler / scaler / rng files (`:200-216`).
- It creates the repo public (`:190`).

Which uploaded file is then scored: `validator-scoring.md` → "Which file gets scored".

## The environment inside your container (VERIFIED)

Only `TRANSFORMERS_CACHE`, `HF_HOME`, the trainer host's HF credentials and an optional
`BASELINE_STATS_PATH` are set (`trainer/runtime.py:235-245`). Anything gated on another env var
is off in production — and the dedup judge treats such a branch as dead code
(`validator/infrastructure/repo_dedup.py:350-356`). **The coded default is what runs.**

## The reference ai-toolkit entrypoint (`ops/docker/scripts/image_toolkit_entrypoint.py`)

1. Parse the CLI; `--model-type` choices are the five templates (`:20-26`, `:31-40`).
2. Extract the dataset zip and flatten every image and `.txt` into `/dataset/images` (`:48-77`).
3. Load the family template; set `name` = expected repo name, `training_folder` =
   `/app/checkpoints/<task_id>`, `trigger_word`, the dataset folder, the model path
   `/cache/models/<owner>--<repo>`, the ideogram4/krea2 text-encoder paths and the krea2 VAE path
   (`:80-134`).
4. Run `python3 run.py <config>` in `/app/ai-toolkit` with `check=True` (`:137-141`).

It does no time planning, no checkpoint selection and no pruning: the template's step count runs
as written. The image pins ai-toolkit to one commit and torch 2.6.0 / cu124
(`ops/docker/standalone-image-toolkit-trainer.dockerfile:1-10`).

## Where a miner is allowed to be clever

The validator fixes the task, the data, the clock, the hardware and the eval. What it leaves to
you:

| Seam | What you control |
|---|---|
| **Recipe** | steps, LR, schedule, network shape, loss / timestep policy — per task, per family |
| **Budget** | how many steps you plan for the measured hardware, and the overhead you keep |
| **Data** | preprocessing, filtering, resolution / bucketing, caption handling |
| **Selection** | which checkpoint ships — and therefore what `select_lora` will pick |
| **Survival** | crash handling, deadline watchdog, offline-safe loading, atomic writes |

Not on that list: the eval, the seeds, the prediction settings, and the base model your adapter
is scored on (`validator-scoring.md`). Optimising against your own proxy instead of those is the
classic way to ship a regression that looks like an improvement. And per the dedup judge,
different values in the same knobs are cosmetic; a new mechanism that runs is what counts
(`tournament-mechanics.md`).

## Structural traps (public code)

- `/cache` is **read-only** in your container and read-write in the downloader and uploader
  (`trainer/runtime.py:266`, `:470`, `:820`). Code that writes under `/cache` works in one and
  fails in the other.
- Your container has **no internet** (`trainer/runtime.py:71-77`, `:277`). Every model component
  must load from the staged local paths, never from a repo id.
- The build runs **without cache, for every task** (`trainer/runtime.py:99`, `:118-124`).
  Anything your Dockerfile downloads is downloaded again each time, inside the 30-minute build
  limit (`trainer/constants.py:41`) — not inside your training clock. Build your Dockerfile the
  same way, without cache, before you ship it.
- The uploader's subfolder rule and `select_lora` together decide what is scored; check both
  against the folder your trainer actually leaves behind.
