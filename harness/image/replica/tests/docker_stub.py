#!/usr/bin/env python3
"""tests/docker_stub.py - a CPU-only stand-in for the `docker` CLI, for tests/test_*.sh (never on a box).

It emulates exactly the docker calls the kit makes, with no GPU and no network beyond 127.0.0.1:
  image inspect -f FMT IMG   ids, the god.sha / sn56.repo_sha labels (STUB_GOD_SHA / STUB_REPO_SHA), repo digests;
                             an image listed in STUB_MISSING_IMAGES is "not on this box"
  run ... IMAGE ARGS         by image: the evaluator (god-image-eval*: `python -m run_latent_eval` scores every MODELS
                             entry with deterministic fake losses; an HF repo is FETCHED through the vendored upstream
                             materialize_model against the mock hub - hfmock/eval_fetch.py), `--entrypoint python -c`
                             probes (the eval weight, the uploader's sha256), the uploader (*trainer-uploader*: runs the
                             vendored upstream uploader.py against the mock hub with the container's env and volume
                             rewritten to host paths), the downloader (*trainer-downloader*: fetches --dataset into
                             <cache>/datasets/<task>_tourn.zip and writes a dummy base), anything else: exit 0
  create / start / inspect -f {{.State.Running}}|{{.State.ExitCode}} / logs / wait / kill / cp / rm
                             the trainer container: `start` writes a valid tiny LoRA to the named volume at
                             /app/checkpoints/<task>/<repo>/last.safetensors (unless STUB_TRAIN_NO_OUTPUT=1); it
                             "runs" for STUB_TRAIN_SECONDS and exits STUB_TRAIN_RC (default 0); `kill` makes it 137
  ps                         prints STUB_PS (default: nothing)
  network / info / version / pull / volume   succeed quietly
State: STUB_STATE (required) holds containers/<name>.json and volumes/<volume>/ (a named volume's host dir).
Knobs: STUB_FAKEHUB_DIR (the mock hub's storage), STUB_UPLOAD_RC, STUB_DL_RC, STUB_CREATE_RC, STUB_EVAL_FAIL (a repo
whose evaluation "fails"; '*' = every HF repo), STUB_COMFY_QUANT=1 (print ComfyUI's fp8 'Native ops' line, emulated
under LATENT_FP8_COMPUTE=off like an A100 would decide), STUB_FP8_NATIVE=1 (print native fp8 ops whatever the shim says).
"""
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import time
import urllib.request
import zipfile

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.environ.get("STUB_STATE") or sys.exit("docker_stub: STUB_STATE is not set")
os.makedirs(os.path.join(STATE, "containers"), exist_ok=True)
os.makedirs(os.path.join(STATE, "volumes"), exist_ok=True)
EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")


def h(s):
    return hashlib.sha256(s.encode()).hexdigest()


def cstate(name, data=None):
    p = os.path.join(STATE, "containers", name + ".json")
    if data is None:
        return json.load(open(p)) if os.path.isfile(p) else None
    json.dump(data, open(p, "w"))


def write_lora(path, seed):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = hashlib.sha256(seed.encode()).digest() * 128      # 4096 bytes
    hdr = json.dumps({"stub.lora_down.weight": {"dtype": "F32", "shape": [32, 32], "data_offsets": [0, 4096]}},
                     separators=(",", ":")).encode()
    hdr += b" " * ((8 - len(hdr) % 8) % 8)
    with open(path, "wb") as fh:
        fh.write(struct.pack("<Q", len(hdr)) + hdr + data)


def parse_run(argv):
    """docker run/create options -> (opts, image, args)."""
    opts = {"v": [], "e": [], "env_file": [], "name": "", "network": "", "entrypoint": "", "flags": []}
    with_value = {"--name", "-v", "--volume", "-e", "--env", "--env-file", "--network", "--entrypoint", "-w", "--cpus",
                  "--memory", "--gpus", "--shm-size", "--security-opt", "--cap-drop", "--label", "-u"}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in with_value:
            v = argv[i + 1]
            key = {"-v": "v", "--volume": "v", "-e": "e", "--env": "e", "--env-file": "env_file"}.get(a)
            if key:
                opts[key].append(v)
            elif a in ("--name", "--network", "--entrypoint"):
                opts[a.lstrip("-")] = v
            opts["flags"].append(f"{a} {v}")
            i += 2
        elif a.startswith("-"):
            opts["flags"].append(a)
            i += 1
        else:
            return opts, a, argv[i + 1:]
    sys.exit("docker_stub: no image in: " + " ".join(argv))


def host_of(opts, container_path):
    """The host path a container path maps to through -v (named volumes live under STATE/volumes)."""
    best = None
    for spec in opts["v"]:
        parts = spec.split(":")
        src, dst = parts[0], parts[1]
        if container_path == dst or container_path.startswith(dst.rstrip("/") + "/"):
            if best is None or len(dst) > len(best[1]):
                best = (src, dst)
    if best is None:
        return None
    src, dst = best
    if not src.startswith("/"):
        src = os.path.join(STATE, "volumes", src)
        os.makedirs(src, exist_ok=True)
    return src + container_path[len(dst.rstrip("/")):]


def env_of(opts):
    env = {}
    for f in opts["env_file"]:
        for line in open(f):
            k, sep, v = line.rstrip("\n").partition("=")
            if sep:
                env[k] = v
    for e in opts["e"]:
        k, sep, v = e.partition("=")
        env[k] = v if sep else os.environ.get(k, "")
    return env


def run_uploader(opts):
    env = env_of(opts)
    local = host_of(opts, env.get("LOCAL_FOLDER", ""))
    if os.environ.get("STUB_UPLOAD_RC"):
        print("stub uploader: forced failure", flush=True)
        return int(os.environ["STUB_UPLOAD_RC"])
    child = {k: v for k, v in os.environ.items() if not k.startswith(("HF_", "HUGGING"))}
    child.update({k: env[k] for k in ("HUGGINGFACE_TOKEN", "HUGGINGFACE_USERNAME", "MODEL", "TASK_ID",
                                      "EXPECTED_REPO_NAME", "HF_REPO_SUBFOLDER", "WANDB_LOGS_PATH") if k in env})
    child.update(LOCAL_FOLDER=local or "/nonexistent", FAKEHUB_DIR=os.environ["STUB_FAKEHUB_DIR"],
                 HOME=os.path.join(STATE, "home"))
    child.pop("WANDB_TOKEN", None)
    os.makedirs(child["HOME"], exist_ok=True)
    return subprocess.call([sys.executable, os.path.join(KIT, "hfmock", "vendored.py"),
                            os.path.join(KIT, "vendor", "god", "trainer", "containers", "uploader.py")], env=child)


def run_downloader(opts, args):
    a = dict(zip(args[::2], args[1::2]))
    cache = host_of(opts, "/cache")
    if os.environ.get("STUB_DL_RC"):
        return int(os.environ["STUB_DL_RC"])
    dst = os.path.join(cache, "datasets", f"{a['--task-id']}_tourn.zip")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with urllib.request.urlopen(a["--dataset"], timeout=30) as r, open(dst, "wb") as fh:
        fh.write(r.read())
    model = os.path.join(cache, "models", a["--model"].replace("/", "--"))
    os.makedirs(model, exist_ok=True)
    open(os.path.join(model, "stub.safetensors"), "wb").write(b"stub")
    print(f"stub downloader: {a['--dataset']} -> {dst}; base {a['--model']} ({a.get('--model-type')})", flush=True)
    return 0


def run_evaluator(opts, args):
    env = env_of(opts)
    if args[:2] == ["-m", "run_latent_eval"] or args[-2:] == ["-m", "run_latent_eval"]:
        pass
    elif args and args[0].endswith("warm_cache.py"):
        print(f"[warm] DONE family={args[1]} base_repo={args[2]} total_bytes=0 (stub)", flush=True)
        return 0
    else:
        return 0
    zpath = host_of(opts, env.get("DATASET", "/data/test_data.zip"))
    with zipfile.ZipFile(zpath) as z:
        n = sum(1 for x in z.namelist() if x.lower().endswith(EXTS))
    out = host_of(opts, env["EVALUATION_RESULTS_PATH"])
    hub_cache = host_of(opts, "/root/.cache/huggingface/hub")
    result = {"model_params_count": 1234567}
    if os.environ.get("STUB_COMFY_QUANT") == "1":
        emu = env.get("LATENT_FP8_COMPUTE", "native") == "off" and os.environ.get("STUB_FP8_NATIVE") != "1"
        print("Native ops: nvfp4" + (", emulated ops: float8_e4m3fn, float8_e5m2" if emu else ", float8_e4m3fn, float8_e5m2"),
              flush=True)
    for repo in [m for m in env.get("MODELS", "").split(",") if m]:
        if repo.startswith("local/"):
            print(f"[latent-shim] lora {repo} -> {h(repo)[:24]}.safetensors revision=local source=/lora_in", flush=True)
        else:
            fe = dict(os.environ, FAKEHUB_DIR=os.environ.get("STUB_FAKEHUB_DIR", ""), HF_HUB_CACHE=hub_cache)
            p = subprocess.run([sys.executable, os.path.join(KIT, "hfmock", "eval_fetch.py"), "--repo", repo,
                                "--loras-dir", os.path.join(STATE, "loras")], env=fe, capture_output=True, text=True)
            sys.stdout.write(p.stdout)
            if p.returncode != 0:
                result[repo] = "Image prediction evaluation failed (FetchError)"
                continue
        if repo == os.environ.get("STUB_EVAL_FAIL") or (os.environ.get("STUB_EVAL_FAIL") == "*" and not repo.startswith("local/")):
            print("Image prediction evaluation failed (RuntimeError)", flush=True)
            result[repo] = "Image prediction evaluation failed (RuntimeError)"
            continue
        loss = lambda tag, i: 0.05 + int(h(f"{repo}|{tag}|{i}")[:8], 16) / 0xFFFFFFFF * 0.1
        result[repo] = {"eval_loss": {"text_guided_losses": [loss("t", i) for i in range(n)],
                                      "no_text_losses": [loss("n", i) for i in range(n)]}, "is_finetune": True}
        print(f"Image prediction evaluation completed: {n} images", flush=True)
    with open(out, "w") as fh:
        json.dump(result, fh)
    return 0


def main(argv):
    if not argv:
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "image" and rest[:1] == ["inspect"]:
        fmt = rest[rest.index("-f") + 1] if "-f" in rest else ""
        img = rest[-1]
        if img in os.environ.get("STUB_MISSING_IMAGES", "").split():
            print(f"Error: No such image: {img}", file=sys.stderr)
            return 1
        if "god.sha" in fmt:
            print(os.environ.get("STUB_GOD_SHA", "<no value>") if "god-image-eval" in img else "<no value>")
        elif "sn56.repo_sha" in fmt:
            print(os.environ.get("STUB_REPO_SHA", "<no value>"))
        elif "RepoDigests" in fmt:
            print(f"{img.split(':')[0]}@sha256:{h(img)}")
        elif ".Id" in fmt:
            print(f"sha256:{h(img)}")
        return 0
    if cmd == "ps":
        out = os.environ.get("STUB_PS", "")
        if out:
            print(out)
        return 0
    if cmd in ("network", "info", "version", "pull", "volume"):
        if cmd == "network" and "-f" in rest:
            print("true")
        return 0
    if cmd in ("run", "create"):
        if cmd == "create" and os.environ.get("STUB_CREATE_RC"):
            return int(os.environ["STUB_CREATE_RC"])
        opts, image, args = parse_run(rest)
        if cmd == "create":
            cstate(opts["name"], {"image": image, "args": args, "opts": opts, "state": "created"})
            print(h(opts["name"])[:12])
            return 0
        if opts["entrypoint"] == "python" and args[:1] == ["-c"]:
            code = args[1]
            if "DIFFUSION_TEXT_GUIDED_EVAL_WEIGHT" in code:
                print("0.5")
            elif "uploader.py" in code:
                src = os.path.join(KIT, "vendor", "god", "trainer", "containers", "uploader.py")
                print(os.environ.get("STUB_UPLOADER_SHA") or hashlib.sha256(open(src, "rb").read()).hexdigest())
            return 0
        if "trainer-uploader" in image:
            return run_uploader(opts)
        if "trainer-downloader" in image:
            return run_downloader(opts, args)
        if "god-image-eval" in image or "image-evaluator" in image:
            return run_evaluator(opts, args)
        if args[-1:] == ["-L"]:
            print("GPU 0: STUB GPU (UUID: GPU-stub)")
        return 0
    name = rest[-1] if rest else ""
    st = cstate(name) if cmd in ("start", "inspect", "logs", "wait", "kill", "rm") else None
    if cmd == "start":
        if st is None:
            print(f"Error: No such container: {name}", file=sys.stderr)
            return 1
        a = st["args"]
        kv = dict(zip(a[::2], a[1::2]))
        if os.environ.get("STUB_TRAIN_NO_OUTPUT") != "1":
            vol = host_of(st["opts"], f"/app/checkpoints/{kv['--task-id']}/{kv['--expected-repo-name']}")
            write_lora(os.path.join(vol, "last.safetensors"), name)
        st.update(state="running", started=time.time())
        cstate(name, st)
        return 0
    if cmd == "inspect":
        fmt = rest[rest.index("-f") + 1] if "-f" in rest else ""
        if st is None:
            return 1
        running = st["state"] == "running" and time.time() - st.get("started", 0) < float(os.environ.get("STUB_TRAIN_SECONDS", "0"))
        if ".State.Running" in fmt:
            print("true" if running else "false")
        elif ".State.ExitCode" in fmt:
            print(137 if st.get("killed") else int(os.environ.get("STUB_TRAIN_RC", "0")))
        return 0
    if cmd == "logs":
        print(f"stub training log of {name}", flush=True)
        return 0
    if cmd == "wait":
        print(137 if (st or {}).get("killed") else int(os.environ.get("STUB_TRAIN_RC", "0")))
        return 0
    if cmd == "kill":
        if st:
            st.update(killed=True, state="exited")
            cstate(name, st)
        return 0
    if cmd == "rm":
        p = os.path.join(STATE, "containers", name + ".json")
        if os.path.exists(p):
            os.remove(p)
        return 0
    if cmd == "cp":
        src, dst = rest[-2], rest[-1]
        cname, _, cpath = src.partition(":")
        st = cstate(cname)
        if st is None:
            return 1
        host = host_of(st["opts"], cpath.rstrip("/.").rstrip("/"))
        if not host or not os.path.isdir(host):
            print(f"Error: Could not find the file {cpath} in container {cname}", file=sys.stderr)
            return 1
        os.makedirs(dst, exist_ok=True)
        for fn in os.listdir(host):
            s = os.path.join(host, fn)
            (shutil.copytree if os.path.isdir(s) else shutil.copy2)(s, os.path.join(dst, fn))
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
