#!/usr/bin/env python3
"""hfmock/eval_fetch.py - the evaluator's candidate-LoRA fetch, WITHOUT the GPU part (DRY=1 and tests/ only).

Runs the REAL vendored G.O.D function vendor/god/validator/evaluation/image_artifacts.py materialize_model(api, repo,
None, <loras dir>) - exactly the call evaluators/diffusion.py:123 makes for every MODELS entry - against the fake
huggingface_hub (FAKEHUB_DIR), downloading into the HF cache named by HF_HUB_CACHE, and prints the same line the box's
shim prints after the real call (run_latent_eval.py:58-59):
  [latent-shim] lora <repo> -> <name> revision=<rev> file=<file> blob=<blob>
so hf_flow.py fetchcheck reads a DRY / test log exactly like a real one.

  FAKEHUB_DIR=... HF_HUB_CACHE=... python3 eval_fetch.py --repo OWNER/NAME --loras-dir DIR
"""
import argparse
import logging
import os
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)            # the fake huggingface_hub first - never the real one
import vendored  # noqa: E402


def load_image_artifacts():
    return vendored.load(vendored.IMAGE_ARTIFACTS, "god_image_artifacts")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--loras-dir", required=True)
    a = ap.parse_args()
    if not os.environ.get("FAKEHUB_DIR") or not os.environ.get("HF_HUB_CACHE"):
        sys.exit("eval_fetch.py: FAKEHUB_DIR and HF_HUB_CACHE must be set (hfmock only)")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s", stream=sys.stdout)
    import huggingface_hub
    if not huggingface_hub.__version__.endswith("hfmock"):
        sys.exit("eval_fetch.py: refusing - the imported huggingface_hub is not hfmock")
    ia = load_image_artifacts()
    directory = Path(a.loras_dir)
    try:
        name, prov = ia.materialize_model(huggingface_hub.HfApi(), a.repo, None, directory)
    except Exception as e:     # the evaluator's own wording for a failed candidate (diffusion.py:183)
        print(f"Image prediction evaluation failed ({type(e).__name__})", flush=True)
        print(f"  [hfmock] {e}", flush=True)
        sys.exit(3)
    blob = (directory / name).resolve().name
    print(f"[latent-shim] lora {a.repo} -> {name} revision={prov['revision']} file={prov['filename']} blob={blob}",
          flush=True)


if __name__ == "__main__":
    main()
