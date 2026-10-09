# text — SN56 text-track harness

| file | what | status |
|---|---|---|
| `eval/validator_parity_eval.py` | score a text model with the validator's instruct recipe (per-example CE after the shift, batch 1, sequence-cap rules, length-sorted, rows over the cap dropped - every rule with its G.O.D file:line at b49ba8644a0d) and assert it reproduces a known `test_loss` (`--expect`) | recipe re-read against G.O.D b49ba8644a0d; not run here (no torch on this machine) |
| `eval/paired_rows.py` | paired per-row comparison of two scorings of the same held-out set: delta, row bootstrap interval, per-row wins outside the validator's 0.01-nat dead zone, rank against the field | stdlib; logic unchanged |
| `train_step_probe.py` | a pre-submit probe that runs ONE real optimizer step on a toy model inside your image (TrainingArguments -> Trainer -> forward/backward/step -> save -> artefact checks); `PROBE_HOOK` plugs in your own artefact predicate | syntax-checked only (needs torch + transformers) |

## Prerequisites

- `validator_parity_eval.py`: torch + transformers (+ peft for adapters), the model dir locally, and the task's
  held-out `test_data` pulled INSIDE its 7-day window (`../common/intel/pull_tournament.py --data`).
- `train_step_probe.py`: run it inside your built trainer image with the interpreter your entrypoint uses
  (`../common/pre-submit-smoke-test.sh` does, for `SMOKE_TRACK=text`).

## Run

```bash
python3 eval/validator_parity_eval.py --model <dir> --heldout <task8>_test_data.json --expect <official test_loss> --out ctl.jsonl
python3 eval/validator_parity_eval.py --model <arm dir> --heldout <task8>_test_data.json --out arm.jsonl
python3 eval/paired_rows.py ctl.jsonl arm.jsonl "<field test_losses, comma-separated>"
docker run --rm --network none -v "$PWD:/probe:ro" --entrypoint python <your-image> /probe/train_step_probe.py
```

**Calibrate first.** Before you trust any delta, reproduce at least one official `test_loss` from a public artefact
(ideally two, far apart) with `--expect`, and measure your own run-to-run noise (score one artefact twice; train one
config with two seeds). Write both numbers into your runbook before reading any arm.

## Not included

No calibration data, no per-row files, no task ids, no competitor artefacts, no thresholds: they are yours to measure.
The tournament-level tools shared with the image track (intel pull, dedup replica, repo-contract gate, smoke gate,
test-suite runner, pre-registration checks) are in `../common/`.
