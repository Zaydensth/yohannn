"""run_latent_eval.py - entrypoint shim for the public G.O.D image evaluator.

Runs INSIDE god-image-eval:main-<sha7> (built by build_eval_image.sh from ops/docker/validator-diffusion.dockerfile at the
G.O.D commit you pinned) as `python -m run_latent_eval` from /app, with this file bind-mounted read-only at
/app/run_latent_eval.py. That is the same interpreter, cwd and sys.path[0] as the stock CMD /app/start.sh -> `python -m
validator.evaluation.evaluators.diffusion` (ops/docker/scripts/image_eval_entrypoint.sh:3).

File:line references were read at G.O.D main b49ba8644a0d (2026-10-09) and ComfyUI 694815f (the commit that
validator-diffusion.dockerfile pins); re-check them whenever you move GOD_SHA. Two knobs, everything else is the stock
evaluator:

1. LORA RESOLUTION ONLY: a MODELS entry "local/<name>" resolves to the staged file /lora_in/<name>.safetensors
   (read-only bind mount), symlinked into COMFY_ROOT/models/loras/ as sha256("local/<name>")[:24].safetensors.
   Every other materialize_model call (base model, text encoder, VAE, and real Hugging Face LoRA repos) runs the REAL
   image_artifacts.materialize_model unchanged. Why patching one name touches only LoRA resolution:
     evaluators/diffusion.py:19       `from validator.evaluation.image_artifacts import materialize_model` (own binding)
     evaluators/diffusion.py:123      its only use: the candidate LoRA, filename=None, directory models/loras
     image_artifacts.py:80-98         prepare_base -> image_artifacts.materialize_model (module global, untouched)
     image_flow_adapter.py:9,82,85    TE / VAE -> image_flow_adapter.materialize_model (own binding, untouched)
   install() asserts all three bindings before and after patching, so a refactor upstream fails loudly here.

2. LATENT_FP8_COMPUTE (default "native" = stock behaviour on this GPU). "off" makes
   comfy.model_management.supports_fp8_compute() answer False, which is what the PRODUCTION GPU answers: production
   image eval requests A100 (validator/evaluation/constants.py:39 BASILICA_GPU_MODELS=["A100"];
   validator/evaluation/runpod.py:161 "gpu": {"name": ["A100"]}). A100 = sm80, and ComfyUI
   comfy/model_management.py:1859-1881 returns False for major 8 / minor < 9; comfy/ops.py:1513 reads it through the
   module attribute, so pick_operations marks float8_e4m3fn/e5m2 "emulated" (ops.py:1524-1526) and fp8 weights are
   dequantized and multiplied in higher precision. On an sm89/sm90 GPU the same layers would use an fp8 GEMM instead.
   The numerical size of that difference depends on the model and the GPU: measure it on your own box before relying
   on either setting. Proof in the log: ComfyUI prints "Native ops: ... emulated ops: ..." (ops.py:1527) for every
   comfy_quant checkpoint; latent_tool.py collect turns it into the fp8_ops column and refuses a mismatch.

Warnings: diffusion.main() calls configure_eval_logging() (evaluation_logging.py:11-27), which REPLACES every root
handler with a stderr handler at INFO, so the comfy lines "lora key not loaded: ..." (comfy/lora.py:93) and
"NOT LOADED ..." (comfy/sd.py:120,219) are printed by the evaluator itself, prefixed "<asctime> WARNING root - ".
"""
import hashlib
import os
import re
import sys
from pathlib import Path

import validator.evaluation.evaluators.diffusion as diffusion
from validator.evaluation import image_artifacts
from validator.evaluation import image_flow_adapter

LORA_IN = Path("/lora_in")
LOCAL_PREFIX = "local/"
_LOCAL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_real_materialize = image_artifacts.materialize_model


def materialize_model(api, repo, filename, directory):
    """Replacement for diffusion.materialize_model (candidate LoRA resolution only)."""
    if not repo.startswith(LOCAL_PREFIX):
        name, provenance = _real_materialize(api, repo, filename, directory)
        blob = (Path(directory) / name).resolve().name
        print(f"[latent-shim] lora {repo} -> {name} revision={provenance['revision']} "
              f"file={provenance['filename']} blob={blob}", flush=True)
        return name, provenance
    local = repo[len(LOCAL_PREFIX):]
    if filename is not None or Path(directory).name != "loras" or not _LOCAL_NAME.match(local):
        raise ValueError("local/ repos are only valid as candidate LoRAs with a plain name")
    source = LORA_IN / f"{local}.safetensors"
    if not source.is_file():
        raise FileNotFoundError("staged local LoRA missing")
    name = hashlib.sha256(repo.encode()).hexdigest()[:24] + ".safetensors"
    target = Path(directory) / name
    Path(directory).mkdir(parents=True, exist_ok=True)
    if target.is_symlink() or target.exists():
        target.unlink()
    target.symlink_to(source.resolve())
    print(f"[latent-shim] lora {repo} -> {name} revision=local source={source} bytes={source.stat().st_size}",
          flush=True)
    return name, {"repo": repo, "revision": "local", "filename": "checkpoints/last.safetensors"}


def install():
    assert diffusion.materialize_model is _real_materialize, "diffusion.materialize_model is not the real one"
    assert image_flow_adapter.materialize_model is _real_materialize, "image_flow_adapter binding changed"
    assert callable(getattr(diffusion, "configure_eval_logging", None)), "diffusion.main no longer configures logging"
    diffusion.materialize_model = materialize_model
    # Base / text encoder / VAE resolution must still be the real function.
    assert image_artifacts.materialize_model is _real_materialize
    assert image_flow_adapter.materialize_model is _real_materialize

    fp8 = os.environ.get("LATENT_FP8_COMPUTE", "native")
    if fp8 == "off":
        sys.path.insert(0, os.environ.get("COMFY_ROOT", "/app/validator/evaluation/ComfyUI"))
        import comfy.model_management

        comfy.model_management.supports_fp8_compute = lambda device=None: False
    elif fp8 != "native":
        raise SystemExit("LATENT_FP8_COMPUTE must be 'native' or 'off'")
    print(f"[latent-shim] installed: local/ -> {LORA_IN}; fp8_compute={fp8}; "
          f"strata={os.environ.get('IMAGE_EVAL_STRATA', 'default')} noises={os.environ.get('IMAGE_EVAL_NOISES', 'default')} "
          f"batch={os.environ.get('IMAGE_EVAL_BATCH_SIZE', 'default')} models={os.environ.get('MODELS', '')}", flush=True)


if __name__ == "__main__":
    install()
    diffusion.main()
