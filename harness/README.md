# harness — tools to work the SN56 (Gradients) tournament the way the validator does

The validator's code (`gradients-ai/G.O.D`, branch `main`) is the only source of truth for how tasks are trained,
uploaded, evaluated, ranked and de-duplicated. These tools reproduce those mechanics on your own machines so that what
you measure before a tournament is what the tournament will measure. Every validator behaviour they copy is cited as
`file:line`, read at `gradients-ai/G.O.D` main **`b49ba8644a0d`** (2026-10-09). Upstream moves often: re-read the cited
lines at the commit you pin before you trust a tool's assumption.

```
harness/
  image/
    replica/    the box kit: validator-shaped train -> upload -> verify -> score through HF -> fetchcheck -> delete,
                calibration against official boards, lock-gated queue, staging, CPU tests        (start: replica/README.md)
    archive/    archive every tournament's datasets to PRIVATE HF dataset repos within the 7-day window
    runpod/     a two-pod (TRAIN H100 + EVAL A100) design for when you have no long-lived box
  text/         validator-parity eval for instruct tasks, paired per-row comparison, one-step training probe
  common/       tournament intel pull, local dedup replica, repo-contract gate, pre-submit smoke gate, honest suite
                runner, fixes-present check, pre-registration checks, an empty A/B runbook, Claude Code commands
```

## How to read the status of a tool

Each README says how its tool was checked. **VERIFIED** means it was run (CPU tests with stubs, synthetic repos,
read-only calls to public endpoints) or read in the G.O.D source at the commit above. **INFERRED** means a reasoned
assumption that was not executed here. In particular: **the image box kit has not been run on a GPU box in this
form** - its CPU tests prove the control flow, the HF half against the real upstream uploader / LoRA-selection code, and
every failure path; your first box session is its integration test (run it `DRY=1`, then one cell, then the queue).

## Ground rules baked into the tools

- **No experiment without a locked plan.** The queue refuses GPU work unless its cells file is bound to a plan whose
  frozen text carries the strategist's `FABLE-PLAN: ... POINT=lock VERDICT=GO` line, the ledger agrees, and nothing
  changed since (`image/replica/plan_gate.py`). Stopping and disabling a cell never need a new lock.
- **One change per A/B**: same task data, split, seed, hours and evaluator commit; only the arm differs.
- **Calibrate before you compare**: score public artefacts whose official `test_loss` you know (`anchors.sh`,
  `validator_parity_eval.py --expect`) and measure your own noise floor; a gap smaller than either is noise.
- **A gate that cannot fail is not a gate**: refs must resolve, empty trees / suites / marker lists are refused,
  pattern scanners self-test, a skipped stage is INCONCLUSIVE, failed evaluations are rows, never dropped.
- **Secrets**: tokens only from `0600` files (`/root/.hf_env`, `~/.hf_env`) or the environment - never on a command
  line, never printed, never in a repo; containers get them through temporary `0600` env-files.
- **Irreversible / outbound actions stay yours**: nothing here pushes to GitHub, registers an entry, submits on chain,
  powers a box off or deletes anything without an explicit confirmation (the only automatic deletions are the
  kit's own temporary public LoRA repos, logged in the HF ledger, and `hf_cleanup.py apply --yes --confirm <account>`).
- **Keep this harness OUT of your miner repo**: the validator clones your whole repo; every non-training file is
  anti-obfuscation and dedup surface.

## Prerequisites (overall)

bash, python3 >= 3.8, git, curl, rsync, ssh on your workstation; a Linux GPU box with docker + nvidia-container-toolkit
for the image kit (`image/replica/provision.sh` sets it up); a Hugging Face write token; torch + transformers where a
text tool says so. Upstream G.O.D files that the image kit's DRY runs and tests execute are not redistributed: they are
fetched at setup from the commit you choose (`image/replica/vendor/fetch_god_vendor.sh`). One exception:
`common/dedup_local.py` contains functions and prompt text copied verbatim from G.O.D; those portions stay under the
upstream license and notice, see `common/third_party/README.md`.

## Not included (on purpose)

Training recipes, engine code, hyper-parameters, measured numbers (scores, ranks, offsets, timings, noise floors),
plans, pre-registrations, results, competitor analysis, hotkeys, task / tournament ids, account names, hosts and
tokens. Everything that depends on your miner or your hardware is an argument or an environment variable you fill from
your own measurements.
