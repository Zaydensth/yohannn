# vendor/god — the upstream G.O.D files this kit runs WITHOUT docker

`DRY=1` runs and the CPU tests (`tests/`) run the real upstream uploader and LoRA-selection code against a loopback
mock hub (`../../hfmock/`), so a dry run proves the same code paths the box uses. These files are **not shipped** in
this directory: they are third-party code under G.O.D's own `LICENSE.md` / `NOTICE`. Fetch them once:

```bash
GOD_SHA=<the 40-hex G.O.D commit you reviewed> bash vendor/fetch_god_vendor.sh
```

The script downloads the three files below from `gradients-ai/G.O.D` at that commit into this directory, refuses a
partial download, and prints each file's sha256 and git blob id. Never edit them here: a change upstream means a new
fetch, a new review and new hashes.

| file here | upstream path | sha256 at `b49ba8644a0d` | git blob at `b49ba8644a0d` |
|---|---|---|---|
| `trainer/containers/uploader.py` | `trainer/containers/uploader.py` | `ff8bfb8f90fe45516e67bfda91a511ee79f668b57c544faa6a64329ebc2edcea` | `08d934e3d2ac94fd2aadbbaff3df4090428e69b7` |
| `validator/evaluation/image_artifacts.py` | `validator/evaluation/image_artifacts.py` | `6e0deec7acd47182a229a0cf7d13931ec287bc12898df63cf83d5e7608964817` | `71c8c842590463271d9fcf76b83d52d0337c441a` |
| `hf-uploader.dockerfile` | `ops/docker/hf-uploader.dockerfile` | `1ddd0ff55994b3a103564e8cccc70f02169152a541a3e00452e90b5588a4855d` | `6cb021fa3af07cfa3c70ca4c1cec7a340230c15d` |

VERIFIED 2026-10-09: the values above were computed from a checkout of `gradients-ai/G.O.D` main at
`b49ba8644a0d705e9b035746eceaa05614742934` (`sha256sum` and `git rev-parse HEAD:<path>`). At another commit they may
differ; that is your cue to re-read the file, not to edit this table to make it match.

After you have READ `trainer/containers/uploader.py` at your pin, record its sha256 as the first word of
`../../reviewed_uploader.sha256` (or export `HF_UPLOADER_REVIEWED_SHA256`). `hf_lib.sh` hashes the uploader code inside
the production uploader IMAGE before every upload and refuses anything else unless `ALLOW_UNREVIEWED_UPLOADER=1`.

## How production uses them (file:line at `b49ba8644a0d`)

- `trainer/runtime.py:790-871` `upload_repo_to_hf` runs image `gradientsio/trainer-uploader:latest`
  (`trainer/constants.py:24`), entrypoint `python trainer/containers/uploader.py` (`ops/docker/hf-uploader.dockerfile:19`),
  env HUGGINGFACE_TOKEN / HUGGINGFACE_USERNAME / WANDB_TOKEN / WANDB_LOGS_PATH / LOCAL_FOLDER / MODEL / TASK_ID /
  EXPECTED_REPO_NAME / HF_REPO_SUBFOLDER, volumes `checkpoints:/app/checkpoints:rw` + `cache:/cache:rw`, `remove=False`
  then `container.remove(force=True)` in `finally` (`:862-871`). Image tasks: `HF_REPO_SUBFOLDER="checkpoints"`
  (`runtime.py:1140`, `trainer/constants.py:58`), no W&B token (`runtime.py:1141`). It is called from `_final_cleanup`
  (`runtime.py:1106-1160`) only when the trainer exited 0 **or hit the timeout** (`runtime.py:1066-1092`: on timeout
  `success = True`, so the kill is followed by an upload, not a forfeit).
- `uploader.py:174` repo = `<HUGGINGFACE_USERNAME>/<EXPECTED_REPO_NAME>`; `:179-181` `detect_subfolder` (`:136-152`:
  the FIRST sub-directory, in `os.listdir` order, that holds a `.safetensors`); `:183-184` refuses an empty /
  metadata-only folder; `:186` `patch_model_metadata`; `:190` `create_repo(..., private=False)` - **public**;
  `:200-216` `upload_folder(path_in_repo="checkpoints", delete_patterns=["*"], ignore_patterns=[trainer_state.json,
  training_args.bin, optimizer.pt, optimizer.bin, scheduler.pt, scaler.pt, rng_state*.pth])`, 3 tries; `:237-242`
  **deletes LOCAL_FOLDER** afterwards.
- `validator/evaluation/evaluators/diffusion.py:123` -> `image_artifacts.materialize_model(api, repo, None,
  models/loras)` (`image_artifacts.py:49-77`): `model_info(files_metadata=True)` -> revision = `info.sha` (the repo's
  HEAD at eval time), `list_repo_files`, `select_lora` (`image_artifacts.py:36-46`: exactly one `last.safetensors` under
  `checkpoints/` | else the highest `[-_]<n>.safetensors` | else the only file | else ValueError), then
  `hf_hub_download(repo, file, revision=sha)`.

`cell_run.sh` runs the production uploader image and `score_latent.sh` the evaluator image on the box; these vendored
copies only stand in where there is no docker (DRY=1 and `tests/`).
