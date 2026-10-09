# The environment task contract

> `file:line` re-checked against `gradients-ai/G.O.D@b49ba86` on 2026-10-09.

## What arrives

`EnvRawTask` carries `environment_names`, `environment_weights`, `eval_seed`, and
`synthetic_data` (`validator/tasks/models.py:117-127`). But that is the **validator-side DB
row** — what the container is actually handed in `--dataset-type` is `EnvironmentDatasetType`,
which has exactly one field: `environment_names`.

The `--dataset` file is a **1000-row proxy** of `{"prompt": "Interact with this environment."}`.
It is not training data. **You generate your own signal.**

The `eval_seed` is deliberately **withheld while the task is live** — it is post-mortem intel,
not a training-time lever.

## What you must hand back

The standard text-trainer CLI (`docs/miner.md:295-317`):

```
--task-id --model --dataset --dataset-type --task-type EnvTask
--file-format --expected-repo-name --hours-to-complete
```

Deliverable: a directory at `/app/checkpoints/{task_id}/{expected_repo_name}`, uploaded by the
validator to **its own** HF account. Either a LoRA adapter (`adapter_config.json`) or full
weights is accepted.

**Ship an adapter.** In R2+ the eval continues from your previous-round model **only if it is a
real LoRA adapter** — `check_for_lora` failing degrades to the foundation model with an
INFO-level log and no error. Merged weights silently cost you the continuation.

## The training sidecar

You get **ONE** environment sidecar, not one per environment.
`_select_training_env_server_name` returns the first PVP env that has an `env_image` — always
the MCTS-API image — and its URLs arrive in `ENVIRONMENT_SERVER_URLS`
(`trainer/runtime.py:748-756`, `:989-1006`).

Nuance: the loop starts one container **per assigned GPU** for that single env, so a 4×H100
round-3 task gets 4 sidecars — all of the same game. `intercode` and `swe_infinite` get no
sidecar at all.

## Model prep is not free time

`MODEL_PREP_TIMEOUT_SECONDS = 5400` (`validator/tasks/prep/model.py:22`), and baselines are
restricted to the task's own environments. At `ENV_BASELINE_TIME_BUDGET_SECONDS = 420` per env
(`trainer/model_prep/env_stats.py:51`), a 6-env round-3 task spends real time before you train.

## The environments

`core/constants/environments.py:10-12` defines `EvalType.INDIVIDUAL` and `EvalType.PVP`;
`:59-187` holds the per-env configs. Six OpenSpiel games run PvP (goofspiel, clobber, leduc,
gin, othello, liars); `intercode` and `swe_infinite` are INDIVIDUAL. **A single task can
contain both kinds** and they are combined afterwards.

R1 env selection is anti-repeat: environments **not** used in the previous env tournament are
shuffled to the front and picked first (`task_creator.py:167-176`) — except
`FORCED_R1_ENVIRONMENT` (currently `EnvironmentName.INTERCODE`,
`validator/tournament/constants.py:120-122`), which is always pulled to the front
(`task_creator.py:178-183`), so intercode plays in every R1.

All groups in a round train the **same** task parameters — the first group's task becomes the
`reference_task` and every later group copies its `model_id`, `environment_names`, and
`eval_seed`. R2+ additionally forces the model to whatever R1 used
(`task_creator.py:300-331`).
