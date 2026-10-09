---
description: Pre-submission audit — check the submitted tree against every known forfeit path before a tournament.
---

Audit the tree that is about to be submitted. This is a forfeit-prevention pass, not a code
review. Work through every item and report PASS / FAIL / UNVERIFIED with a `file:line` for
each. Do not fix anything until I have seen the report.

Validator behaviour is cited from `gradients-ai/G.O.D` `main` at commit `b49ba86`
(read 2026-10-09). `main` moves: re-read each cited line on current `main` before you rely on
it, and cite the SHA you read.

**1. Exit-code integrity**
- Trace every `raise`, `sys.exit`, and uncaught call on the top-level path of the trainer
  entry point. A non-zero exit uploads nothing, even if a good checkpoint was already written:
  the run is marked failed (`trainer/runtime.py:1074-1085`) and the upload runs only on success
  (`trainer/runtime.py:1138-1160`).
- If the trainer has a crash-salvage or fallback path: is it reachable from every failure
  branch, and does it write atomically?
- Is an intended early stop distinguishable from a real failure, in the exit code and in the
  log?

**2. Artifact integrity**
- Will the run leave exactly the artifact the validator will pick? Know your track's selection
  rule. Image: among `checkpoints/` (or `checkpoint/`) `*.safetensors`, exactly one
  `last.safetensors` wins; otherwise the highest `[-_]<number>.safetensors`; otherwise the only
  file; otherwise the evaluation fails (`validator/evaluation/image_artifacts.py:36-46`). A
  numbered file left behind can be scored instead of the one you meant.
- Is that true on **every** exit path, including timeout? On timeout the container is killed
  and what already exists is uploaded (`trainer/runtime.py:1086-1092`, then `:1138-1160`), so
  the output folder must hold a valid artifact at every moment after the first save.

**3. Budget (wall clock)**
- For every model type you accept, recompute the worst case of
  `steps × measured sec/step + reserve` (plus setup, data prep and any salvage) against
  `hours_to_complete × 3600`. The clock is the container's; nothing you do before training
  starts is free.
- Flag any sec/step figure that was measured on hardware other than what the validator
  allocates for that task.

**4. Model-type routing completeness**
- For every model type the validator can send for your track (read the enum in G.O.D and cite
  it), confirm it is wired in every place your code branches on it: enum / argparse choices,
  dockerfile routing, config templates, per-type step / sec-per-step / reserve tables, and any
  per-type special case. Not handling every model type in the tournament is a listed pitfall
  (`docs/miner.md:648`).
- Report any type missing from any one of them.

**5. Network and paths**
- The training container runs on an internal Docker network (`trainer/runtime.py:71-76`,
  `internal=True`) and `/cache` is mounted read-only (`trainer/runtime.py:266`;
  `trainer/constants.py:46`). Grep for any repo-id string reaching a runtime loader instead of
  a staged local path, for any download at runtime, and for any write under `/cache`.
- Read the dataset from the pre-downloaded cache path, not from the URL argument
  (`docs/miner.md:649`).

**6. Data path**
- Every input shape the validator can send for your track is consumed: no silently dropped
  sample, no empty caption or row, no unhandled field. List every per-type data special case
  your design depends on and confirm each is still in place.

**7. Shipping hygiene**
- Are all survival-critical mechanisms default-ON? The validator sets only its own environment
  variables in the container (`trainer/runtime.py:235-239`), so anything gated OFF by default
  never runs there. List every env-gated mechanism.
- Does each shipped mechanism emit one distinctive log line you can find afterwards?
- Do any logs print strategy fields or per-task plan dumps? Assume container output can be read
  by others.
- Are `LICENSE` and `NOTICE` byte-identical to upstream on the **default branch** of the
  submitted repo? The validator compares normalized line content against its own copies
  (`validator/tournament/github_validation.py:131-132,155-156`; `docs/miner.md:268`). Any
  watermark or identity marker?
- Readable source only: no `.pyc`, binaries, packed or minified logic (`docs/miner.md:272`).
  The obfuscation scan runs at the submitted commit with a 60-second timeout, and a timeout
  counts as a failure (`validator/tournament/github_validation.py:20-55`).
- Is the delta distinct from published winners? Run G.O.D's own dedup tool
  (`ops/tools/tournament/dedup_check.py`); duplicates are detected and eliminated
  (`docs/miner.md:646`). INFERRED: a delta whose every change is default-OFF behaves like the
  code it came from and risks reading as a duplicate.
- Is `CLAUDE.md` / `.claude/` absent from the submitted commit?

Finish with a single ranked list of anything that could cost us a task, worst first.
