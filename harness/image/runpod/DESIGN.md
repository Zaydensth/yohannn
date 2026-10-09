# Two-pod validator replica on RunPod (TRAIN pod + EVAL pod) — a generic design

**Status: design, not an implementation.** Nothing here has been run on RunPod by this kit. It shows how the
validator's image-track pipeline maps onto rented pods, and which differences from production you accept by doing so.
Every `file:line` refers to `gradients-ai/G.O.D` main at `b49ba8644a0d` (read 2026-10-09) — re-read them at the
commit you pin. RunPod facts are marked with where they come from; **cloud prices and stock change without notice:
read them in the RunPod console at rent time** (no prices are quoted here on purpose).

Use it when you have no long-lived GPU box. If you do have one, the box kit in `../replica/` already runs the whole
validator-shaped flow on a single machine with a docker daemon, and is the simpler choice.

---

## 0. Two facts that shape the design

1. **Production trains on H100 but evaluates image tasks on A100.** Training: `GpuRequirement.H100_1X` for every
   image task (`validator/tournament/gpu_requirements.py:45-46`; `docs/miner.md:454`). Evaluation:
   `BASILICA_GPU_MODELS = ["A100"]` with `BASILICA_SGLANG_MIN_GPU_MEMORY_GB = 80`
   (`validator/evaluation/constants.py:39-40`), and the RunPod backend hardcodes `"A100"` again
   (`validator/evaluation/runpod.py:161, 668, 675`). So a faithful replica uses **two GPU classes**: an H100 for the
   TRAIN pod and an A100 80 GB for the EVAL pod.
2. **RunPod is already one of the validator's own eval backends — through dstack.** `validator/evaluation/remote.py:9-31`
   selects `basilica` (default) or `runpod` from the env `EVAL_BACKEND`; `runpod.py:1` describes the RunPod backend as
   "implemented as dstack services". A replica does not need dstack: what matters for the score is the same evaluator
   image, env and GPU model, not the provisioning path.

## A. TRAIN pod

### A.1 What production does for an image task (`trainer/runtime.py:917-1166`, `start_training_task`)

| # | Step | file:line | What happens |
|---|---|---|---|
| 1 | Pick the dockerfile | `runtime.py:896-916`, `trainer/constants.py:1-12` | flux -> `ops/docker/standalone-image-trainer.dockerfile`, else the toolkit dockerfile; each falls back to the legacy `dockerfiles/...` path of the MINER repo. Missing -> FileNotFoundError. |
| 2 | Downloader | `runtime.py:425-506` | separate container, public image `gradientsio/trainer-downloader:latest` (`trainer/constants.py:25`), `--task-id --model --task-type --dataset --model-type` (+`--anonymize` only when model prep ran, `runtime.py:949,961`), volume `cache:/cache` rw. The dataset zip lands at `/cache/datasets/<task>_tourn.zip` (`trainer/training_paths.py:42-43`). |
| 3 | Build the miner image | `runtime.py:93-146` | docker-py `client.api.build(path=<repo>, dockerfile=..., nocache=True)`; timeout `DOCKER_BUILD_TIMEOUT_MINUTES = 30` (`trainer/constants.py:41`). The whole repo is the build context. |
| 4 | Run the trainer | `runtime.py:199-296` | CLI `--task-id --model --dataset-zip --model-type --expected-repo-name --hours-to-complete [--trigger-word]`; env `TRANSFORMERS_CACHE=HF_HOME=/cache/hf_cache` + HF credentials (`:58-68`); volumes `checkpoints:/app/checkpoints` rw, `cache:/cache` **ro**; memory `110g` and 24 CPUs per GPU (`:79-90`, `trainer/constants.py:34-35`), `shm_size 8g`, `no-new-privileges`, `cap_drop ALL`, network `internal_bridge` created with `internal=True` (`:71-76`) — **no route out during training**. |
| 5 | Wait | `runtime.py:927, 1066-1092` | timeout `max(1, int(hours * 3600))`; on timeout the container is killed **and the run still counts as success** (`:1092`), so the upload still happens. |
| 6 | Upload | `runtime.py:1106-1160`, `790-871` | separate container `gradientsio/trainer-uploader:latest` (`trainer/constants.py:24`): public repo `<user>/<expected_repo_name>`, path `checkpoints/` for image tasks (`:1140`), no W&B for image (`:1141`); details in `../replica/vendor/god/VENDOR.md`. |

### A.2 One pod, three steps

A RunPod pod is one container started from one image; you cannot run your own docker daemon inside it (RunPod docs,
"Pods overview" — re-check before you rely on it). So steps 2, 4 and 6 become three **processes in sequence** inside
one TRAIN pod instead of three sibling containers:

```text
# SKETCH - the order inside one TRAIN pod, not a finished script
<downloader entrypoint> --task-id $TASK_ID --model $MODEL --task-type ImageTask --dataset $DATASET_URL --model-type $FAMILY
timeout --signal=KILL "$((HOURS*3600))" <your trainer entrypoint> --task-id ... --hours-to-complete $HOURS ; RC=$?
# do NOT stop on RC: production kills at the timeout and uploads anyway (runtime.py:1086-1092)
<uploader entrypoint>   # env TASK_ID MODEL EXPECTED_REPO_NAME LOCAL_FOLDER HF_REPO_SUBFOLDER=checkpoints HUGGINGFACE_*
```

The downloader and uploader code is public (`trainer/containers/downloader.py`, `trainer/containers/uploader.py`,
their dockerfiles under `ops/docker/`), so the TRAIN image is your trainer image plus their (small) dependencies.

### A.3 Where the image is built

The pod cannot build images (no daemon), so build on a machine that has one — a CPU-only VM is enough — with the same
no-cache rule production uses, push it to a registry (GHCR / Docker Hub), register the registry credentials in RunPod,
and point the pod template at the tag. Time the no-cache build against the 30-minute cap (`trainer/constants.py:41`):
a build that does not finish in production means no container and no score.

### A.4 Which H100

The requirement code names a logical `H100_1X` and does not distinguish SXM / PCIe / NVL. Measure what you rent
(`nvidia-smi -q -d CLOCK,POWER,TEMPERATURE`, sustained clocks under load) instead of assuming from the product name;
step-time tables built on one variant do not transfer to another.

## B. EVAL pod

### B.1 What production does (`validator/evaluation/docker_evaluation.py:361-428`, `run_evaluation_basilica_image`)

- base env `ORIGINAL_MODEL_REPO`, `MODEL_TYPE`, `TRANSFORMERS_ALLOW_TORCH_LOAD=true`, `PYTHONUNBUFFERED=1` +
  `HF_CONTAINER_ENV_IMAGE` (`constants.py:126-171`: HF cache paths, `HF_HUB_ENABLE_HF_TRANSFER=0`, a READ-ONLY token
  only when the host has one, `:139-155`);
- **one deployment per submitted repo** (`MODELS=<repo>`, `TEST_SPLIT_URL`, `:397-401`);
- image `gradientsio/image-evaluator:basilica` (`core/constants/docker.py:2`), built from
  `ops/docker/validator-diffusion.dockerfile` (pinned ComfyUI commit, torch, `requirements/image-evaluator.txt`);
- container size `cpu=4`, `memory=64Gi` (`basilica.py:697-698`, `constants.py:59-60`).

### B.2 Design

- **GPU: A100 80 GB.** On another GPU, ComfyUI's fp8 path differs (it asks `supports_fp8_compute()`); the kit's shim
  `../replica/run_latent_eval.py` can force the A100 decision, but real A100 hardware removes the question.
- **Image:** build it with `../replica/build_eval_image.sh` at the G.O.D commit you reviewed (on a VM with a docker
  daemon), push it, run it in the pod. The pod only *runs* the image, like production.
- **Command:** `python -m run_latent_eval` with the shim mounted or baked in (local `local/<name>` LoRAs + the fp8
  knob), or the stock `/app/start.sh` when you only score HF repos.
- **Inputs:** the task's `test_data.zip` (from your private archive) and an HF cache on a volume so base models, text
  encoders and VAEs are not downloaded for every artifact.

## C. Orchestration

### C.1 Calls, in order

```text
1. list GPU types / stock     H100 (TRAIN) and A100 80GB (EVAL) in the region you want      read-only
2. [YOU APPROVE] create network volume(s)                                                   billable
3. [YOU APPROVE] create the TRAIN pod (your trainer image, the volume)                      billable
4. run the three steps per cell; the upload is the hand-off
5. [YOU APPROVE] stop the TRAIN pod when idle
6. [YOU APPROVE] create the EVAL pod (evaluator image)                                      billable
7. score every uploaded repo (the evaluator downloads from HF, exactly like production)
8. [YOU APPROVE] stop / delete both pods
```

Every create / stop / delete changes what you are billed: an agent prepares it, **you approve each one**.

**HF is the bridge between the pods, not a shared volume.** A network volume is tied to one data center, and H100 and
A100 stock may not be in the same one at the same time; production also bridges training and evaluation through the HF
upload. So the two pods never need to share a volume or a region.

### C.2 Pod per cell or pod per queue

| | Pod per cell | Pod per queue |
|---|---|---|
| Fidelity | closer: production starts a new container per task | one long-lived process: clean state between cells yourself |
| Overhead | boot + image pull for every cell | once |
| Fits `../replica/queue.sh` | needs a new driver | yes, mostly (the queue assumes one long-lived machine) |

## D. Accepted differences from production (write them down, do not hide them)

| # | Difference | Severity | Handling |
|---|---|---|---|
| 1 | No no-cache **build** inside a pod (no docker daemon) | high | build + time it on a VM with a daemon (A.3) |
| 2 | Downloader / trainer / uploader run as processes in ONE container, not three containers | medium | accepted for your own code; per-step isolation (`cap_drop`, `no-new-privileges`) is lost |
| 3 | No `internal_bridge`: the trainer has internet during training | medium | a run that downloads during training passes here and FAILS in production: test it on a box with `internal_bridge` (`../replica/provision.sh` creates one) or block egress yourself |
| 4 | Provisioning through the RunPod API, not dstack | low | the score depends on image, env and GPU model, not on how the pod was made |
| 5 | Network volumes are network-attached, not a local docker volume | low | sequential reads; account for the load time per cell |
| 6 | The production retry / timeout / "kill then upload" semantics must be re-created by hand | medium | A.2's sketch keeps "always upload after the timeout"; keep it when you implement |

## E. First steps that cost nothing

- **You** create the RunPod account, the payment method and the API key (console -> Settings -> API Keys). Keep the key
  in a `0600` file outside every repo; never paste it into a chat or a commit.
- Optional: RunPod's MCP server for Claude Code (`npx @runpod/mcp-server@latest`, Node 20+) gives an agent read-only
  calls (list GPU types, list volumes) before anything billable.
- Read-only first: list GPU types / stock, list volumes, list registries — zero cost, and it replaces every assumption
  in this document with today's facts.
