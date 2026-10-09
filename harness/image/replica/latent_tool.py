#!/usr/bin/env python3
"""latent_tool.py - host-side helper for score_latent.sh (python3 >= 3.8, stdlib only).

  zipinfo ZIP      count test images exactly like read_dataset()+decoded_images() (G.O.D
                   validator/evaluation/image_test_data.py:70-112) and refuse if any image lacks a non-empty caption.
  stcheck FILE     sanity-check a .safetensors header (length, JSON, offsets vs file size); prints the tensor count.
  fileinfo FILE    prints "<inode> <bytes> <sha256>" (portable replacement for GNU stat -c / sha256sum).
  realpath PATH    canonical absolute path, the path need not exist (portable replacement for GNU realpath -m).
  rootckpt --repo OWNER/NAME --family F --manifest-dir MDIR [--api-base URL] [--no-fetch]
                   the evaluator's base choice (image_artifacts.py:80-98 prepare_base): ideogram4 always loads
                   Comfy-Org/Ideogram-4 (exit 0); every other family needs EXACTLY ONE root-level .safetensors
                   > 5 GiB in the task repo (exit 0, ROOT_FILE named), else prepare_base raises ValueError (exit 1,
                   ROOT_N = how many). exit 2 = the repo's file list is unavailable (the evaluator decides). Prints
                   one shell-quoted line ROOT_N=.. ROOT_FILE=.. GATED=.. reason=.. (GATED = the HF API `gated` field:
                   false | auto | manual | unknown). The file list is fetched ONCE from GET {api}/api/models/<repo>
                   ?blobs=true (public metadata, no token) into MDIR/<owner>--<name>.json and read from disk after.
  collect ...      merge per-container evaluator JSONs, parse container logs, append TSV rows.

Final loss is computed exactly as validator/scoring/tasks.py:309-329 (_calculate_weighted_loss_for_image_eval):
  text_avg = sum(text_guided_losses)/len(...) if list else 0
  no_text_avg = sum(no_text_losses)/len(...) if list else 0
  final = W*text_avg + (1-W)*no_text_avg,   W = DIFFUSION_TEXT_GUIDED_EVAL_WEIGHT (read from the image;
  0.5 at b49ba86, validator/evaluation/constants.py:6)
A model is NEVER dropped: every manifest row yields exactly one TSV row; non-OK rows have status FAILED(...).

Log parsing notes (file:line at G.O.D b49ba86 / ComfyUI 694815f, the commit that G.O.D's
ops/docker/validator-diffusion.dockerfile pins):
  * the evaluator configures root logging (evaluation_logging.py:11-27, format "%(asctime)s %(levelname)s
    %(name)s - %(message)s"), so comfy's "lora key not loaded:" warnings now carry that prefix; it is stripped before
    matching. print() lines (completed/failed/setup failed/frames) are unprefixed.
  * fp8_ops column + guard: ComfyUI logs "Native ops: ... , emulated ops: ..." (comfy/ops.py:1527) when it loads a
    comfy_quant checkpoint. With FP8_COMPUTE=off every such line must list float8_e4m3fn as EMULATED (the A100
    production decision); a native line under "off" (or an emulated one under "native") makes the row
    FAILED(FP8_MODE:...) instead of silently mixing numerics.
"""
import argparse
import csv
import datetime
import fcntl
import hashlib
import json
import math
import os
import re
import shlex
import struct
import sys
import urllib.request
import zipfile
from pathlib import Path

EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")  # image_test_data.py:56

TSV_COLUMNS = [
    "task", "label", "repo_or_path", "text_mean", "no_text_mean", "final", "n_images", "status",
    "strata", "noises", "batch", "image_id", "timestamp",
    "unloaded_keys", "fail_site", "fp8_compute", "per_repo", "exit_code", "wall_s",
    "artifact_sha256", "resolved", "evaluator_repo", "god_sha", "out_json", "log", "fp8_ops",
]

LOG_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} (?:DEBUG|INFO|WARNING|ERROR|CRITICAL) \S+ - ")
SHIM_LINE = re.compile(r"^\[latent-shim\] lora (\S+) -> (\S+) revision=(\S+)(.*)$")
DONE_LINE = re.compile(r"^Image prediction evaluation completed: (\d+) images$")         # diffusion.py:180
FAIL_LINE = re.compile(r"^Image prediction evaluation failed \((\w+)\)$")                 # diffusion.py:183-185
SETUP_FAIL_LINE = re.compile(r"^Image evaluation setup failed \((\w+)\)$")                 # diffusion.py:228
FRAME_LINE = re.compile(r"^  (\S+\.py):(\d+) in (\S+)$")                                   # diffusion.py:186-187
RUNTIME_MSG = ("mat1 and mat2", "expected scalar type", "Input type", "The size of tensor", "shape '",
               "CUDA out of memory", "Expected all tensors", "Expected tensor")            # diffusion.py:188-192
UNLOADED = ("lora key not loaded:", "NOT LOADED")  # comfy/lora.py:93; comfy/sd.py:120 "NOT LOADED x", :219 "NOT LOADED: x"
FP8_NAMES = ("float8_e4m3fn", "float8_e5m2")


def die(msg):
    print(f"latent_tool: {msg}", file=sys.stderr)
    sys.exit(1)


def message(line):
    """The log record's message: strips configure_eval_logging's prefix when present."""
    return LOG_PREFIX.sub("", line, count=1)


def cmd_zipinfo(args):
    with zipfile.ZipFile(args.zip) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            die("duplicate archive entries (read_dataset would raise)")
        n = 0
        for name in sorted(names):
            if name.lower().endswith(EXTENSIONS):
                caption = str(Path(name).with_suffix(".txt"))
                text = archive.read(caption).decode("utf-8") if caption in names else None
                if not text or not text.strip():
                    die(f"image without a non-empty caption: {name} (decoded_images would raise)")
                n += 1
    if n == 0:
        die("no images (read_dataset would raise)")
    print(n)


def cmd_stcheck(args):
    size = os.path.getsize(args.file)
    with open(args.file, "rb") as f:
        raw = f.read(8)
        if len(raw) != 8:
            die("file shorter than 8 bytes")
        n = struct.unpack("<Q", raw)[0]
        if n <= 0 or 8 + n > size:
            die(f"bad header length {n} for file size {size}")
        header = json.loads(f.read(n))
    tensors = {k: v for k, v in header.items() if k != "__metadata__"}
    if not tensors:
        die("no tensors")
    end = max(v["data_offsets"][1] for v in tensors.values())
    if 8 + n + end != size:
        die(f"data section mismatch: header says {8 + n + end} bytes, file has {size}")
    print(len(tensors))


def cmd_fileinfo(args):
    st = os.stat(args.file)
    h = hashlib.sha256()
    with open(args.file, "rb") as f:
        for chunk in iter(lambda: f.read(8 << 20), b""):
            h.update(chunk)
    print(st.st_ino, st.st_size, h.hexdigest())


def cmd_realpath(args):
    print(os.path.realpath(os.path.abspath(args.path)))


def _fmt(x):
    return "" if x is None else repr(float(x))


def parse_fp8(text):
    """'Native ops: a, b , emulated ops: c, d' -> ({a,b}, {c,d})."""
    body = text.split("Native ops:", 1)[1]
    native, _, emulated = body.partition("emulated ops:")
    split = lambda s: {x.strip(" ,") for x in s.split(",") if x.strip(" ,")}
    return split(native), split(emulated)


def parse_log(path, models):
    """Attribute log lines to models by evaluator order (diffusion.py:114-196 loops config['models'] in order;
    every iteration ends with exactly one 'completed' or 'failed' print)."""
    per = {m: {"unloaded": 0, "fail_site": "", "fail_msg": "", "shim": None, "done": None, "has_log": True}
           for m in models}
    setup_fail = None
    fp8 = set()    # "native" / "emulated" seen in this container's "Native ops:" lines
    k = 0          # index of the model currently being evaluated
    frames_for = None
    frames = []
    try:
        lines = open(path, errors="replace").read().splitlines()
    except FileNotFoundError:
        for m in models:
            per[m]["has_log"] = False
        return per, None, fp8

    def flush_frames():
        nonlocal frames_for, frames
        if frames_for is not None and frames_for < len(models) and frames:
            per[models[frames_for]]["fail_site"] = frames[-1]
        frames_for, frames = None, []

    for raw in lines:
        line = message(raw)
        if frames_for is not None:
            fm = FRAME_LINE.match(line)
            if fm:
                frames.append(f"{fm.group(1)}:{fm.group(2)}:{fm.group(3)}")
                continue
            if line.startswith(RUNTIME_MSG) and frames_for < len(models):
                per[models[frames_for]]["fail_msg"] = line[:160]
            flush_frames()
        if "Native ops:" in line:
            native, emulated = parse_fp8(line)
            if native & set(FP8_NAMES):
                fp8.add("native")
            if emulated & set(FP8_NAMES):
                fp8.add("emulated")
        current = models[k] if k < len(models) else None
        s = SHIM_LINE.match(line)
        if s and current is not None:
            per[current]["shim"] = s.group(0)
        if line.startswith(UNLOADED):
            if current is not None:
                per[current]["unloaded"] += 1
        d = DONE_LINE.match(line)
        if d:
            if current is not None:
                per[current]["done"] = int(d.group(1))
            k += 1
            continue
        f = FAIL_LINE.match(line)
        if f:
            frames_for, frames = k, []
            k += 1
            continue
        sf = SETUP_FAIL_LINE.match(line)
        if sf:
            setup_fail = sf.group(1)
    flush_frames()
    return per, setup_fail, fp8


def fp8_label(seen):
    if not seen:
        return "none"
    return "+".join(sorted(seen))


GIB5 = 5 * 1024 ** 3   # image_artifacts.py:91 "(f.size or 0) > 5 * 1024**3"


class ManifestUnavailable(Exception):
    def __init__(self, msg, gated="unknown"):
        super().__init__(msg)
        self.gated = gated


def _gated_label(value):
    if value is False or value is None:
        return "false"
    return str(value)


def repo_manifest(repo, manifest_dir, api_base, no_fetch=False):
    """(files [(path, size)], sha, gated) of a Hub repo. Recorded once under manifest_dir so a warm cache is judged with
    no network; raises ManifestUnavailable (carrying `gated` when the API answered)."""
    mpath = os.path.join(manifest_dir, repo.replace("/", "--") + ".json")
    if not os.path.isfile(mpath):
        if no_fetch:
            raise ManifestUnavailable(f"no recorded file list {mpath}")
        url = f"{api_base.rstrip('/')}/api/models/{repo}?blobs=true"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "sn56-harness-latent_tool/1"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                d = json.loads(resp.read().decode())
        except Exception as e:  # network, HTTP error, bad JSON - unverifiable, never a guess
            raise ManifestUnavailable(f"GET {url} failed ({type(e).__name__})")
        gated = _gated_label(d.get("gated", "unknown"))
        files = [[s["rfilename"], s.get("size")] for s in d.get("siblings") or [] if s.get("rfilename")]
        if not files or any(sz is None for _, sz in files):
            raise ManifestUnavailable(f"{url}: siblings without sizes", gated)
        os.makedirs(manifest_dir, exist_ok=True)
        tmp = mpath + ".tmp"
        with open(tmp, "w") as fh:
            json.dump({"repo": repo, "sha": d.get("sha", ""), "gated": gated, "source": url,
                       "fetched_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                       "files": files}, fh, indent=1)
        os.replace(tmp, mpath)
    try:
        man = json.load(open(mpath))
        files = [(str(fp), None if sz is None else int(sz)) for fp, sz in man.get("files") or []]
    except (OSError, ValueError, TypeError) as e:
        raise ManifestUnavailable(f"unreadable file list {mpath} ({type(e).__name__})")
    if not files:
        raise ManifestUnavailable(f"empty file list {mpath}", man.get("gated", "unknown"))
    return files, man.get("sha", ""), man.get("gated", "unknown")


def cmd_rootckpt(a):
    q = shlex.quote
    if a.family == "ideogram4":
        print("ROOT_N=1 ROOT_FILE=diffusion_models/ideogram4_fp8_scaled.safetensors GATED=not-checked "
              + "reason=" + q("ideogram4: the evaluator always loads Comfy-Org/Ideogram-4 (image_artifacts.py:83-85)"))
        return
    try:
        files, sha, gated = repo_manifest(a.repo, a.manifest_dir, a.api_base, a.no_fetch)
    except ManifestUnavailable as e:
        print(f"ROOT_N=unknown ROOT_FILE='' GATED={q(e.gated)} reason={q(str(e))}")
        sys.exit(2)
    roots = [(fp, sz) for fp, sz in files if fp.endswith(".safetensors") and "/" not in fp and (sz or 0) > GIB5]
    names = ",".join(f"{fp}({sz / 1024 ** 3:.2f}GiB)" for fp, sz in roots) or "-"
    if len(roots) == 1:
        why = f"one root checkpoint > 5 GiB in {a.repo}@{sha[:12]}: {names}"
        print(f"ROOT_N=1 ROOT_FILE={q(roots[0][0])} GATED={q(gated)} reason={q(why)}")
        return
    why = (f"prepare_base raises ValueError('Base model requires one unambiguous root-level ComfyUI checkpoint') "
           f"(image_artifacts.py:89-94): {len(roots)} root .safetensors > 5 GiB in {a.repo}@{sha[:12]}: {names}")
    print(f"ROOT_N={len(roots)} ROOT_FILE='' GATED={q(gated)} reason={q(why)}")
    sys.exit(1)


def cmd_collect(a):
    manifest = list(csv.DictReader(open(a.manifest), delimiter="\t"))
    runs = []
    for line in open(a.runs):
        if line.strip():
            part, models, log, rc, wall = line.rstrip("\n").split("\t")
            runs.append({"part": part, "models": models.split(","), "log": log, "rc": rc, "wall": wall})
    W = float(a.weight)

    merged, params_seen, info = {}, set(), {}
    for run in runs:
        part_json = Path(a.parts) / f"{run['part']}.json"
        data = json.load(open(part_json)) if part_json.exists() else {}
        if "model_params_count" in data:
            params_seen.add(data["model_params_count"])
            merged.setdefault("model_params_count", data["model_params_count"])
        per, setup_fail, fp8 = parse_log(run["log"], run["models"])
        for repo in run["models"]:
            if repo in data:
                merged[repo] = data[repo]
            info[repo] = {"result": data.get(repo), "rc": run["rc"], "wall": run["wall"], "log": run["log"],
                          "setup_fail": setup_fail, "fp8": fp8, **per[repo]}

    tmp = a.out_json + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(merged, fh, indent=2, allow_nan=False)
    os.replace(tmp, a.out_json)

    expect_seen = {"off": "emulated", "native": "native"}.get(a.fp8)   # None for any other label: no check
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows, meta_rows = [], []
    for r in manifest:
        repo = r["evaluator_repo"]
        i = info.get(repo, {"result": None, "rc": "NOT_RUN", "wall": "", "log": "", "setup_fail": None,
                            "unloaded": 0, "fail_site": "", "fail_msg": "", "shim": None, "done": None,
                            "has_log": False, "fp8": set()})
        res = i["result"]
        text = no_text = final = None
        n = 0
        if isinstance(res, dict) and isinstance(res.get("eval_loss"), dict):
            tg = res["eval_loss"].get("text_guided_losses") or []
            nt = res["eval_loss"].get("no_text_losses") or []
            text = sum(tg) / len(tg) if tg else 0
            no_text = sum(nt) / len(nt) if nt else 0
            final = W * text + (1 - W) * no_text
            n = len(tg)
            finite = all(math.isfinite(v) for v in list(tg) + list(nt))
            if tg and len(tg) == len(nt) == int(a.expected_images) and finite:
                status = "OK"
            else:
                status = (f"FAILED(BAD_RESULT:n_text={len(tg)},n_no_text={len(nt)},"
                          f"expected={a.expected_images},finite={finite})")
            wrong = i["fp8"] - {expect_seen} if expect_seen else set()
            if status == "OK" and wrong:
                status = f"FAILED(FP8_MODE:expected={a.fp8},seen={fp8_label(i['fp8'])})"
        elif isinstance(res, str):
            m = re.search(r"failed \((\w+)\)", res)
            status = f"FAILED({m.group(1) if m else res[:60]})"
        elif i["rc"] == "NOT_RUN":
            status = "FAILED(NOT_RUN)"
        elif i["setup_fail"]:
            status = f"FAILED(SETUP:{i['setup_fail']})"
        elif i["rc"] == "124":
            status = "FAILED(TIMEOUT:rc=124)"
        elif i["rc"] == "137":
            status = "FAILED(KILLED:rc=137)"  # SIGKILL: timeout --kill-after, OOM killer, or docker kill
        elif not i["has_log"]:
            status = f"FAILED(NO_LOG:rc={i['rc']})"
        else:
            status = f"FAILED(NO_RESULT:rc={i['rc']})"
        if r["kind"] == "local":
            sha, resolved = r["sha256"], r["staged"]
        else:
            shim = i["shim"] or ""
            mb = re.search(r"blob=(\S+)", shim)
            mf = re.search(r"file=(\S+)", shim)
            mr = re.search(r"revision=(\S+)", shim)
            sha = mb.group(1) if mb else ""
            resolved = f"{mr.group(1)}:{mf.group(1)}" if (mr and mf) else ""
        fail_site = i["fail_site"] + (f" | {i['fail_msg']}" if i["fail_msg"] else "")
        row = {
            "task": a.task, "label": r["label"], "repo_or_path": r["origin"],
            "text_mean": _fmt(text), "no_text_mean": _fmt(no_text), "final": _fmt(final), "n_images": str(n),
            "status": status, "strata": a.strata, "noises": a.noises, "batch": a.batch,
            "image_id": a.image_id, "timestamp": now,
            "unloaded_keys": str(i["unloaded"]), "fail_site": fail_site, "fp8_compute": a.fp8,
            "per_repo": a.per_repo, "exit_code": i["rc"], "wall_s": i["wall"], "artifact_sha256": sha,
            "resolved": resolved, "evaluator_repo": repo, "god_sha": a.god_sha, "out_json": a.out_json,
            "log": i["log"], "fp8_ops": fp8_label(i["fp8"]),
        }
        rows.append(row)
        meta_rows.append({k: row[k] for k in ("label", "evaluator_repo", "status", "final", "unloaded_keys",
                                              "fail_site", "exit_code", "wall_s", "artifact_sha256", "resolved",
                                              "fp8_ops")})

    new = not os.path.exists(a.tsv) or os.path.getsize(a.tsv) == 0
    with open(a.tsv, "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        if new:
            fh.write("\t".join(TSV_COLUMNS) + "\n")
        elif open(a.tsv).readline().rstrip("\n").split("\t") != TSV_COLUMNS:
            die(f"{a.tsv} has a different header; refusing to append")
        for row in rows:
            fh.write("\t".join(str(row[c]).replace("\t", " ").replace("\n", " ") for c in TSV_COLUMNS) + "\n")
        fcntl.flock(fh, fcntl.LOCK_UN)

    meta = {"task": a.task, "image_id": a.image_id, "god_sha": a.god_sha, "weight": W, "fp8_compute": a.fp8,
            "per_repo": a.per_repo, "strata": a.strata, "noises": a.noises, "batch": a.batch,
            "expected_images": int(a.expected_images), "model_params_count_seen": sorted(params_seen),
            "timestamp": now, "rows": meta_rows}
    with open(a.meta, "w") as fh:
        json.dump(meta, fh, indent=2)
    if len(params_seen) > 1:
        print(f"WARNING: containers disagree on model_params_count: {sorted(params_seen)}")
    for row in rows:
        print(f"{row['label']:<42} {row['status']:<34} final={row['final'] or '-':<22} "
              f"text={row['text_mean'] or '-':<22} no_text={row['no_text_mean'] or '-':<22} n={row['n_images']} "
              f"unloaded={row['unloaded_keys']} fp8_ops={row['fp8_ops']}")


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("zipinfo").add_argument("zip")
    sub.add_parser("stcheck").add_argument("file")
    sub.add_parser("fileinfo").add_argument("file")
    sub.add_parser("realpath").add_argument("path")
    r = sub.add_parser("rootckpt")
    r.add_argument("--repo", required=True)
    r.add_argument("--family", required=True)
    r.add_argument("--manifest-dir", required=True)
    r.add_argument("--api-base", default="https://huggingface.co")
    r.add_argument("--no-fetch", action="store_true")
    c = sub.add_parser("collect")
    for name in ("out-json", "parts", "runs", "manifest", "tsv", "meta", "task", "image-id", "god-sha", "fp8",
                 "per-repo", "weight", "expected-images", "strata", "noises", "batch"):
        c.add_argument("--" + name, required=True)
    args = p.parse_args()
    {"zipinfo": cmd_zipinfo, "stcheck": cmd_stcheck, "fileinfo": cmd_fileinfo, "realpath": cmd_realpath,
     "rootckpt": cmd_rootckpt, "collect": cmd_collect}[args.cmd](args)


if __name__ == "__main__":
    main()
