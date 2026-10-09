# The training pipeline: what actually runs

Read `trainer/` in the validator repo alongside this. Knowing the exact flow tells you **where you are
allowed to be clever** and where you are just breaking the contract.

## End-to-end flow

```
validator picks task ──> clones YOUR repo at the submitted commit
                    ──> builds YOUR Dockerfile              (your dependency list runs here)
   [downloader container, HAS internet] ──> stages base model + dataset into a shared cache
   [trainer container, NO internet]     ──> runs YOUR entrypoint with a task payload
                    ──> your code writes a checkpoint into the output dir
   [uploader container] ──> pushes that directory to the hub
                    ──> evaluation container scores the uploaded artifact
```

Four consequences people learn the hard way:

1. **Your Dockerfile is part of your submission.** A dependency missing there is not a warning — it is a
   model that never loads and an empty upload. Custom architectures in particular pull extra kernel
   packages, and their modeling code often imports them **unconditionally at import time**.
2. **The trainer has no internet.** The downloader already put everything in the cache. Any code path
   that tries to reach the network will hang and retry rather than fail fast, burning budget. Force
   local-only resolution and always load from the staged path, never from a repo id.
3. **You get a payload, not a command.** The task payload carries the model path, dataset path, task
   type, hour budget, deadline and output locations. Your job is to turn that into a training
   configuration. That translation *is* your product.
4. **The upload is a directory.** If it contains no weight shards, you scored nothing regardless of how
   training went. Assert on this before you exit.

## The seams where you can differentiate

Everything below is yours to decide. This is where miners actually differ:

- **Configuration emission** — given (model, data, task type, hours, GPUs), what hyper-parameters do you
  emit? This is the highest-leverage code in the repo.
- **Data preparation** — filtering, deduplication, length handling, how you build the train/dev split,
  how conversations are rendered and masked.
- **Throughput engineering** — sequence packing, attention kernel choice, fused kernels, precision,
  gradient checkpointing. All of it converts to *more optimizer steps inside the budget*.
- **Schedule control** — measuring your own step time and re-planning so the schedule completes.
- **Selection** — which checkpoint you actually hand over, and whether you average several.
- **Robustness ladders** — what happens on OOM, on an unknown architecture, on a model that will not load
  with your fast path.

## Configuration emission: the mental model

Think of it as a function with a hard constraint:

```
emit(model_stats, data_stats, task_type, hours, gpu_count) -> config
    subject to:  expected_wall_clock(config) < hours
```

Inputs worth computing (all cheap, all CPU):
- **Model**: parameter count, hidden size, layers, vocabulary size, dtype, architecture family, whether it
  needs remote code, context length.
- **Data**: number of samples, token-length distribution (median/p95/p99 — not just the mean), whether it
  is conversational, how long the *completions* are versus the prompts.
- **Task**: type, and any flags on the payload (e.g. a KL flag).
- **Environment**: GPU count and memory, measured step time once training starts.

Outputs: learning rate, schedule shape and floor, warmup, batch size and accumulation, epochs or max
steps, sequence length, packing on/off, attention implementation, precision, optimizer, LoRA-vs-full,
evaluation cadence, save strategy.

`hp-reasoning.md` covers how to choose each of those from first principles.

## Distributed correctness (the part that silently deadlocks)

Once a task grants more than one GPU, any code that runs "sometimes" on "some ranks" is a hazard:

- **Decisions must be identical across ranks.** If rank 0 halves its batch after an OOM and rank 1 does
  not, the next collective mismatches and the job hangs until the deadline. Synchronise such decisions
  (e.g. reduce to the minimum across ranks) rather than letting each rank decide locally.
- **Side effects belong to one rank.** Writing files, uploading, logging summaries — guard with a
  main-process check.
- **Anything that runs extra forward/backward passes** (probing, searching, measuring) must either run
  identically on every rank or not at all. A search loop that prunes trials based on local values will
  diverge across ranks.
- **Prefer measuring over searching** when you cannot test the multi-GPU path. A formula that is slightly
  suboptimal beats a search that deadlocks.

## A checklist before you call a config "done"

- [ ] Does the estimated wall clock fit the budget, with room for saving and uploading?
- [ ] Will the schedule *complete* (not merely start) inside the budget?
- [ ] Does it work at 1 GPU, and does it still work — or degrade cleanly — at 2 and 4?
- [ ] Is every optional feature fail-soft, with a defined fallback?
- [ ] Does the eval path use the same masking as the scoring harness?
- [ ] Does anything reach the network? (It must not.)
- [ ] Is there an assertion that the final checkpoint directory is non-empty?
