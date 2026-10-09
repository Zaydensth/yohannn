"""warm_cache.py FAMILY BASE_REPO - run INSIDE god-image-eval:main-<sha7> WITHOUT a GPU (warm_cache.sh starts it).

Calls the evaluator's REAL resolution code - ImageFlowAdapter(...) for the text encoder(s) + VAE
(image_flow_adapter.py:74-85) and prepare_base(...) for the diffusion checkpoint (image_artifacts.py:80-98), exactly
the calls load_base() makes (evaluators/diffusion.py:45-67) - so every weight a later scoring container needs is
already in the persistent HF hub cache that score_latent.sh mounts at /root/.cache/huggingface/hub. For z-image it
also fetches the diffusers VAE the evaluator encodes with (diffusion.py:56-62: AutoencoderKL.from_pretrained(repo,
subfolder="vae", revision=<resolved base revision>)). No comfy import, no CUDA. Prints what resolved (revision,
blob sha256 name, bytes) so a later mismatch against production can be traced. File:line at G.O.D b49ba8644a0d.
"""
import json
import os
import struct
import sys
import time
from pathlib import Path

from huggingface_hub import HfApi

from validator.evaluation.image_artifacts import prepare_base
from validator.evaluation.image_flow_adapter import ImageFlowAdapter

ROOT = Path(os.environ.get("COMFY_ROOT", "/app/validator/evaluation/ComfyUI"))
FAMILY = sys.argv[1]
TASK_REPO = sys.argv[2]


def describe(label, folder, name):
    link = ROOT / "models" / folder / name
    real = link.resolve()
    size = real.stat().st_size
    with open(real, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        header = json.loads(f.read(n))
    header.pop("__metadata__", None)
    quant = sum(1 for k in header if k.endswith(".comfy_quant"))
    dtypes = sorted({v["dtype"] for v in header.values()})
    print(f"[warm] {label}: {name} -> blob {real.name} bytes={size} ({size / 1024**3:.2f} GiB) "
          f"tensors={len(header)} dtypes={dtypes} comfy_quant_layers={quant}", flush=True)
    return size


t0 = time.time()
api = HfApi()
adapter = ImageFlowAdapter(FAMILY, api, ROOT)
print(f"[warm] {FAMILY} TE+VAE materialized in {time.time() - t0:.1f}s", flush=True)
t1 = time.time()
name, prov = prepare_base(api, {"model_id": TASK_REPO, "model_type": FAMILY}, ROOT)
print(f"[warm] {FAMILY} base materialized in {time.time() - t1:.1f}s: {prov}", flush=True)
total = sum(describe("text_encoder", "text_encoders", e) for e in adapter.encoders)
total += describe("vae", "vae", adapter.vae_name)
total += describe("base", "unet" if FAMILY == "flux" else "diffusion_models", name)
if FAMILY == "z-image":
    import torch
    from diffusers import AutoencoderKL

    AutoencoderKL.from_pretrained(TASK_REPO, subfolder="vae", revision=prov["revision"], torch_dtype=torch.float32)
    print(f"[warm] z-image diffusers VAE cached from {TASK_REPO}@{prov['revision']}/vae", flush=True)
print(f"[warm] DONE family={FAMILY} base_repo={TASK_REPO} total_bytes={total} ({total / 1024**3:.2f} GiB) "
      f"seconds={time.time() - t0:.1f}", flush=True)
