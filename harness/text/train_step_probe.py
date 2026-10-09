#!/usr/bin/env python3
"""train_step_probe.py - a pre-submit gate that really TRAINS (one optimizer step), instead of only importing.

Why: import / argparse / --help checks pass while the failures that zero a tournament run happen later - an
ImportError raised inside load_model rather than at module import, a TypeError when the Trainer is CONSTRUCTED (an
argument a new library version removed), a kernel error at the first OPTIMIZER STEP, a shape error at the first
FORWARD. Each of those ends the same way: the container exits, the artefact is base weights or nothing. A single
training step catches all four classes; an import check catches none of them.

This probe needs no GPU, no network and no downloaded weights: it builds a toy random model from a config and runs the
production-shaped path TrainingArguments -> Trainer -> forward -> backward -> optimizer step -> save, then checks the
saved artefact (files present, max_position_embeddings preserved, every tensor finite).

Run it INSIDE your built trainer image with the interpreter your entrypoint uses, mounted from outside the submission
tree (dev tooling stays out of the repo the validator clones), e.g.:
    docker run --rm -v "$PWD/harness/text:/probe:ro" --entrypoint python <your-image> /probe/train_step_probe.py
env: PROBE_OPTIM [adamw_torch] (use the optimizer your recipe uses), PROBE_HOOK [] = a python file defining
     check(output_dir) that raises on a bad artefact (plug in your own submission / artifact predicate here).
Exit 0 if it trained and saved a sane artefact; non-zero otherwise.
"""
import importlib.util
import json
import os
import sys
import tempfile
import traceback

FAIL = []


def step(label):
    def deco(fn):
        def wrapped(*a, **kw):
            try:
                out = fn(*a, **kw)
                print("  OK    %s" % label, flush=True)
                return out
            except BaseException as exc:
                FAIL.append("%s: %s: %s" % (label, type(exc).__name__, str(exc)[:200]))
                print("  FAIL  %s -> %s: %s" % (label, type(exc).__name__, str(exc)[:200]), flush=True)
                traceback.print_exc()
                raise
        return wrapped
    return deco


@step("build a toy model (llama, 2 layers) from a config - no download")
def build_tiny():
    import torch
    from transformers import AutoConfig, AutoModelForCausalLM
    cfg = AutoConfig.for_model("llama", vocab_size=256, hidden_size=64, intermediate_size=128, num_hidden_layers=2,
                               num_attention_heads=4, num_key_value_heads=4, max_position_embeddings=128)
    return AutoModelForCausalLM.from_config(cfg).to(torch.float32)


@step("construct the Trainer through the API production code uses")
def make_trainer(model, ds, outdir, optim):
    from transformers import Trainer, TrainingArguments
    args = TrainingArguments(output_dir=outdir, per_device_train_batch_size=2, num_train_epochs=1, max_steps=2,
                             logging_steps=1, save_strategy="no", report_to=[], learning_rate=1e-4, optim=optim,
                             use_cpu=True, bf16=False, fp16=False)
    if hasattr(args, "average_tokens_across_devices"):
        args.average_tokens_across_devices = False
    return Trainer(model=model, args=args, train_dataset=ds)


@step("a REAL optimizer step (forward + backward + step)")
def train(trainer):
    out = trainer.train()
    steps = int(trainer.state.global_step)
    if steps < 1:
        raise AssertionError("global_step=%d - no step actually ran" % steps)
    loss = float(out.training_loss)
    if loss != loss or loss in (float("inf"), float("-inf")):
        raise AssertionError("training_loss is not finite - non-finite gradients")
    print("    global_step=%d training_loss=%.4f" % (steps, loss), flush=True)


@step("save the artefact and check it (files, max_position_embeddings, finite tensors)")
def save_and_check(trainer, outdir):
    sub = os.path.join(outdir, "submission")
    trainer.save_model(sub)
    files = os.listdir(sub)
    weights = [f for f in files if f.endswith((".safetensors", ".bin"))]
    if "config.json" not in files or not weights:
        raise AssertionError("artefact incomplete: %s" % sorted(files))
    mpe = json.load(open(os.path.join(sub, "config.json"))).get("max_position_embeddings")
    if mpe != 128:
        raise AssertionError("max_position_embeddings read back as %r, want 128" % mpe)
    st = [f for f in weights if f.endswith(".safetensors")]
    if st:
        from safetensors.torch import load_file
        import torch
        for f in st:
            for name, t in load_file(os.path.join(sub, f)).items():
                if t.is_floating_point() and not torch.isfinite(t).all():
                    raise AssertionError("non-finite values in %s:%s" % (f, name))
    print("    files=%s mpe=%s" % (sorted(files), mpe), flush=True)
    return sub


@step("PROBE_HOOK check(output_dir)")
def hook(sub, path):
    spec = importlib.util.spec_from_file_location("probe_hook", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.check(sub)


def main():
    import torch
    optim = os.environ.get("PROBE_OPTIM", "adamw_torch")
    print("[train-step-probe] optim=%s torch=%s python=%s" % (optim, torch.__version__, sys.version.split()[0]), flush=True)
    outdir = tempfile.mkdtemp(prefix="train-step-probe-")
    try:
        model = build_tiny()
        ds = [{"input_ids": list(range(16)), "labels": list(range(16))} for _ in range(8)]
        trainer = make_trainer(model, ds, outdir, optim)
        train(trainer)
        sub = save_and_check(trainer, outdir)
        if os.environ.get("PROBE_HOOK"):
            hook(sub, os.environ["PROBE_HOOK"])
    except BaseException:
        pass
    if FAIL:
        print("\n[train-step-probe] FAIL:", flush=True)
        for f in FAIL:
            print("  - " + f, flush=True)
        return 1
    print("\n[train-step-probe] PASS - one real optimizer step ran and the artefact is sane", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
