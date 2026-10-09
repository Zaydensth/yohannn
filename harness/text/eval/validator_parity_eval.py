#!/usr/bin/env python3
"""Score a text model the way the G.O.D validator scores an instruct task - and PROVE it against a known answer.

WHY. An internal eval whose numbers cannot be compared with the validator's `test_loss` can only rank your own runs
against each other, never against the field. This harness reproduces the validator's per-example cross-entropy
recipe so that its mean lands on the validator's number for the same model and the same held-out rows.

CALIBRATE IT YOURSELF before you trust it: find a task whose official board (task API `hotkey_details[].test_loss`)
contains an artefact you can download and score - ideally two points far apart (an untrained base someone shipped,
and a trained model) - pull that task's held-out `test_data` inside its 7-day window, score both with --expect, and
keep the per-row files. A recipe that drifts is worse than none, because it is trusted: re-run the calibration
whenever this file or the upstream evaluator changes. Expect a small systematic offset from precision (fp32 here vs
bf16 there); size it on your own calibration points.

THE RECIPE, read from the validator source (gradients-ai/G.O.D main, b49ba8644a0d - re-read at your pin):
  validator/evaluation/evaluators/instruct_text.py, validator/evaluation/common.py, validator/assets/test_axolotl.yml

  1. RANKING METRIC is eval_loss from HF Trainer.evaluate() (instruct_text.py:329-334), not a mean you compute.
  2. BUT test_axolotl.yml:24 sets starting_batch_size: 1 and find_executable_batch_size only SHRINKS, so the eval batch
     is 1 - which makes eval_loss a flat mean over examples of the per-example quantity below (instruct_text.py:178-181
     states the same identity).
  3. PER EXAMPLE: shift first - logits[:, :-1] against labels[:, 1:] - then sum(cross_entropy over supervised positions)
     / count(supervised positions AFTER the shift) (instruct_text.py:201-231). The unshifted count is wrong by n/(n-1)
     on any row whose first token is supervised, which is every row of a completion-only dataset.
  4. ignore_index = -100. Rows whose labels are entirely -100 are dropped before scoring (instruct_text.py:71).
  5. sequence_len starts at 4096 (test_axolotl.yml:5). When config.max_position_embeddings is below 2 * sequence_len it
     becomes ceil(max_position_embeddings / 2) (common.py:546-549). The first candidate is then min(that, cap) with
     cap = min(max_position_embeddings or 131072, tokenizer.model_max_length if < 1e6) (instruct_text.py:80-110).
     Rows longer than that are DROPPED by axolotl, not truncated (instruct_text.py:119). Upstream retries with a longer
     sequence_len only when NO row survives (:113-145); this harness reports that case as "no rows scored" instead.
     Check how many rows your task drops: it changes which rows are scored, and so the mean.
  6. The eval set is sorted by input length before batching (instruct_text.py:75).
  7. bf16=True on the validator's GPU (instruct_text.py:317); this runs fp32.
  8. The per-example VECTOR (the boss round's paired test) is computed only when EMIT_PER_EXAMPLE_LOSSES_ENV=1
     (instruct_text.py:342). KL weighting applies only when the task carries a kl_coef, and it is added to eval_loss
     (instruct_text.py:371-384), never to the per-example vector.

PROMPT CONSTRUCTION for a task whose dataset_type has format / no_input_format null (the common tournament shape): the
prompt is the raw `field_instruction` string, the response is concatenated with NO separator, EOS is appended, and the
prompt's labels are -100. That is axolotl's user-defined prompter with no_input_format="{instruction}"; a task with a
`field_input` column uses upstream's defaults (core/training_config.py:93-97: format "{instruction} {input}" for rows
whose input is non-empty). A system prompt is NOT modelled.

USAGE
    python3 validator_parity_eval.py --model DIR --heldout FILE.json [--limit N] [--expect <test_loss>] [--out rows.jsonl]

  --model    a local directory (offline). A LoRA adapter dir (adapter_config.json) is loaded on its base (--base, or the
             base_model_name_or_path it names, resolved under /cache/models/<org>--<name> when present).
  --heldout  the validator's own test_data pull: a JSON list of {instruct, output} rows. Those URLs are presigned and
             EXPIRE AFTER 7 DAYS: pull them the day the task appears.
  --expect   assert the mean lands within --tolerance of a known answer; exit non-zero if not.

Resumable: rows already present in --out are skipped, so an interrupted run costs nothing.
"""
import argparse
import json
import math
import os
import statistics
import sys
import time


def build_prompt_text(row, field_instruction="instruct", field_input="", fmt=None, no_input_fmt=None):
    """The prompt STRING the validator builds, before tokenisation.

    Upstream core/training_config.py:93-97 fills the axolotl custom format like this:
        no_input_format defaults to "{instruction}"
        format          defaults to "{instruction} {input}" when field_input is set, else "{instruction}"
    axolotl then uses `format` when the row's input is non-empty and `no_input_format` otherwise. Ignoring `input` is
    harmless on a task without an input column and wrong on one with it - always pass --field-input when the task has one.
    """
    if no_input_fmt is None:
        no_input_fmt = "{instruction}"
    if fmt is None:
        fmt = "{instruction} {input}" if field_input else "{instruction}"
    instruction = row[field_instruction]
    value = row.get(field_input) if field_input else None
    if field_input and value:
        return fmt.format(instruction=instruction, input=value)
    return no_input_fmt.format(instruction=instruction)


def build_example(tok, row, field_instruction="instruct", field_output="output",
                  field_input="", fmt=None, no_input_fmt=None):
    """axolotl user-defined prompter: prompt from build_prompt_text, response concatenated with
    no separator, EOS appended, prompt masked to -100."""
    text = build_prompt_text(row, field_instruction, field_input, fmt, no_input_fmt)
    prompt = tok(text, add_special_tokens=True)["input_ids"]
    completion = tok(row[field_output], add_special_tokens=False)["input_ids"] + [tok.eos_token_id]
    return prompt + completion, [-100] * len(prompt) + completion


SEQ_START = 4096   # the configured sequence_len the cap reduces; see docstring item 5



def _resolve_base(name):
    """Map a hub id to the staged, offline copy the container actually has."""
    if not name:
        return None
    if os.path.isdir(name):
        return name
    cand = "/cache/models/" + str(name).replace("/", "--")
    return cand if os.path.isdir(cand) else None


def sequence_cap(model, tok, seq_start=SEQ_START):
    """The first eval sequence_len the validator tries (docstring item 5).

    Upstream first sets sequence_len = ceil(max_position_embeddings / 2) whenever max_position_embeddings <
    2 * sequence_len (common.py:546-549), then takes the first of its candidates, which is min(that, cap) with
    cap = min(max_position_embeddings or 131072, tokenizer.model_max_length if < 1e6) (instruct_text.py:80-110).
    Every term matters on some model: a calibration point where a term cannot fire (e.g. a tokenizer that already caps
    below 4096) does not prove that term - check the cap on the model you are about to score, not only on the one you
    calibrated with. A parity harness that is right by luck is not a parity harness.
    """
    max_pos = getattr(model.config, "max_position_embeddings", None)
    start = seq_start
    if max_pos and max_pos < 2 * start:          # common.py:546-549
        start = math.ceil(max_pos / 2)
    if start <= 0:                               # evaluators/instruct_text.py:93-94 (_sequence_len_candidates)
        start = 2048
    if not isinstance(max_pos, int) or max_pos <= 0:
        max_pos = 131_072
    cap = max_pos
    tok_max = getattr(tok, "model_max_length", None)
    if isinstance(tok_max, int) and 0 < tok_max < 1_000_000:
        cap = min(cap, tok_max)
    return min(start, cap)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--heldout", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default="")
    ap.add_argument("--expect", type=float, default=None)
    ap.add_argument("--tolerance", type=float, default=0.01, help="fractional, default 1%%")
    ap.add_argument("--field-instruction", default="instruct")
    ap.add_argument("--field-output", default="output")
    ap.add_argument("--field-input", default="",
                    help="the task's field_input; empty means the task has no input column")
    ap.add_argument("--format", default="", help="the task's format; empty = upstream default")
    ap.add_argument("--no-input-format", default="", help="the task's no_input_format; empty = upstream default")
    ap.add_argument("--base", default="",
                    help="base model dir for a LoRA adapter submission; resolved from adapter_config.json when omitted")
    ap.add_argument("--device", default="",
                    help="cuda|cpu; default cuda when available. Always fp32 — see below.")
    args = ap.parse_args()

    import torch
    import torch.nn.functional as F
    from transformers import AutoTokenizer, AutoModelForCausalLM

    torch.set_num_threads(os.cpu_count() or 8)
    tok = AutoTokenizer.from_pretrained(args.model)
    # No attn_implementation is requested: transformers RAISES on an explicit implementation the model class does not
    # declare, and resolves one silently when asked for nothing. A measurement harness must never be the thing that
    # cannot load the model.
    # DEVICE: CPU is fine for a ~1B model; for 3B+ use a GPU (CPU becomes seconds per row). fp32 is KEPT on GPU: change
    # the precision only together with a fresh calibration - it moves the numbers, and a calibrated instrument changed
    # for no reason is no longer calibrated.
    # torch_dtype (not dtype=): accepted by both transformers 4.x and 5.x, so the harness also runs in an older venv for
    # a model the newer stack cannot forward.
    _dev = args.device or ("cuda" if torch.cuda.is_available() else "cpu")

    # ADAPTER-AWARE: a LoRA submission shipped as an ADAPTER (adapter_config.json naming its base, not merged) is base
    # weights plus a delta, and AutoModelForCausalLM.from_pretrained on that directory raises "Unrecognized model".
    # Without this branch the harness would be blind to an entire training mode.
    _adapter_cfg = os.path.join(args.model, "adapter_config.json")
    if os.path.isfile(_adapter_cfg):
        with open(_adapter_cfg, encoding="utf-8") as fh:
            _base_name = json.load(fh).get("base_model_name_or_path")
        _base = args.base or _resolve_base(_base_name)
        if not _base:
            raise SystemExit("adapter found but its base %r is not resolvable; pass --base"
                             % _base_name)
        print("adapter detected: base=%s + %s" % (_base, args.model), flush=True)
        from peft import PeftModel
        _bm = AutoModelForCausalLM.from_pretrained(_base, torch_dtype=torch.float32)
        model = PeftModel.from_pretrained(_bm, args.model).to(_dev)
        tok = AutoTokenizer.from_pretrained(_base)
    else:
        model = AutoModelForCausalLM.from_pretrained(args.model,
                                                     torch_dtype=torch.float32).to(_dev)
    model.eval()

    cap = sequence_cap(model, tok)
    rows = json.load(open(args.heldout))
    if args.limit:
        rows = rows[: args.limit]

    done = {}
    if args.out and os.path.exists(args.out):
        for line in open(args.out):
            line = line.strip()
            if line:
                try:
                    r = json.loads(line)
                    done[r["i"]] = r["ce"]
                except Exception:
                    pass
        print(f"resuming: {len(done)} rows already scored", flush=True)

    print(f"model={args.model}  rows={len(rows)}  sequence cap={cap}  "
          f"device={_dev} dtype=fp32  threads={torch.get_num_threads()}", flush=True)

    dropped = 0
    fh = open(args.out, "a") if args.out else None
    t0, n_new = time.time(), 0
    for i, row in enumerate(rows):
        if i in done:
            continue
        ids, labels = build_example(tok, row, args.field_instruction, args.field_output,
                                    args.field_input, args.format or None, args.no_input_format or None)
        if len(ids) > cap:
            dropped += 1          # the validator DROPS these, it does not truncate them
            continue
        if all(l == -100 for l in labels):
            dropped += 1          # empty output: filtered before scoring
            continue
        x = torch.tensor([ids], device=_dev)
        y = torch.tensor([labels], device=_dev)
        with torch.no_grad():
            logits = model(input_ids=x, attention_mask=torch.ones_like(x)).logits
        shift_logits = logits[:, :-1, :].float()
        shift_labels = y[:, 1:]
        per_token = F.cross_entropy(shift_logits.transpose(1, 2), shift_labels,
                                    ignore_index=-100, reduction="none")
        supervised = (shift_labels != -100).float()
        ce = ((per_token * supervised).sum(1) / supervised.sum(1)).item()
        done[i] = ce
        n_new += 1
        if fh:
            fh.write(json.dumps({"i": i, "len": len(ids), "ce": ce}) + "\n")
            fh.flush()
        if n_new % 50 == 0:
            v = list(done.values())
            print(f"  n={len(v):4} mean={sum(v)/len(v):.6f} "
                  f"({time.time()-t0:.0f}s, {(time.time()-t0)/n_new:.2f}s/row)", flush=True)
    if fh:
        fh.close()

    v = list(done.values())
    if not v:
        print("no rows scored — every row exceeded the sequence cap or had empty labels")
        return 2
    mean = sum(v) / len(v)
    sd = statistics.pstdev(v)
    se = sd / len(v) ** 0.5
    print(f"\nn={len(v)}  dropped={dropped}  mean={mean:.6f}  sd={sd:.4f}  se={se:.4f}")

    if args.expect is not None:
        delta = mean - args.expect
        frac = abs(delta) / abs(args.expect)
        print(f"expected {args.expect:.6f}  delta {delta:+.6f}  ({frac*100:.4f}%, "
              f"{abs(delta)/se:.3f} se)")
        if frac > args.tolerance:
            print(f"FAIL: outside tolerance {args.tolerance*100:.2f}% — the recipe no longer "
                  f"reproduces a known validator answer, so nothing measured with it is "
                  f"comparable to a validator number")
            return 1
        print("PASS: reproduces the known validator answer")
    return 0


if __name__ == "__main__":
    sys.exit(main())
