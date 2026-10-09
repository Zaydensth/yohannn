#!/usr/bin/env python3
"""verify_stage.py - read-only integrity check of a staged tournament archive, the ops kit and the repo checkout.

stage.sh runs it TWICE with the same code: on the workstation against the SOURCE archive before sending anything, and
on the box against what landed, because a transfer's exit code is not proof of delivery (long rsync/scp streams do
drop, and a resumed transfer can still leave a short file). Python >= 3.8, stdlib only; writes nothing.

  --tourn DIR        archive root (tourn_<id>_<date>/): every <task-uuid>/ dir must hold test_data.zip + training_data.zip
                     that pass a full CRC test (ZipFile.testzip, same check as unzip -t) and whose test split is
                     evaluable exactly as validator/evaluation/image_test_data.py:70-112 reads it (images + non-empty
                     captions); api/<task>.json must parse and carry model_type/model_id; artifacts/lora/MANIFEST.tsv
                     rows must all be status OK and every <task8>_<hk8>.safetensors must hash to the manifest sha256
                     (paths are rebuilt relative to --tourn: the manifest's own path column is a workstation path).
                     An archive in the tasks/<round>_<family>_<task8>/ layout (hf_archive_tournament.py) is read through
                     its <task-uuid> links (archive_layout.py link on the box, archive_layout.py view on the
                     workstation); with no link at all it FAILS naming that command, and every tasks/ dir must have its
                     own link (a task silently left out of the uuid view is a FAIL, archive_layout.py check).
                     COMPLETENESS against the archive's own manifest.json (the planned task list): every task_id of
                     manifest.json "tasks" must be there as a <task-uuid>/ dir or link (whose zips / api json the checks
                     above then read), no <task-uuid>/ dir may be missing from it, its "failures" list must be empty and
                     n_zip_ok must equal n_zip_expected when it records them. A missing manifest.json is a FAIL (the plan
                     cannot be checked) unless --allow-no-manifest (an archive that never had one: WARN). Checking only
                     the dirs that happen to be there would let a task that never landed pass unseen.
  --allow-no-manifest  an archive without manifest.json: completeness is a WARN, not a FAIL
  --pins-only        the archive carries artifacts/lora/PINS.tsv (fetch_artifacts.py pin) and no LoRA yet: check the
                     pins instead (every row pinned: task dir present, 40-hex commit, 64-hex LFS sha256, bytes > 0,
                     no PIN FAILED note) - the workstation side of a stage whose box fetches the anchors itself
  --no-lora          skip the LoRA section entirely (an archive staged for its task data only, LORA_VIA=none)
  --api-flat DIR     every task json must also exist there byte-identical (anchors.sh and board.py read the flat copy)
  --repo DIR         git checkout: HEAD must equal --expect-sha and the tree must be clean
  --repo-spec DIR=SHA   the same for several checkouts (repeatable)
  --ops DIR          bash -n on every *.sh, ast.parse on every *.py (no __pycache__ written)
  --no-hash          skip the LoRA sha256 pass (size is still checked)

Exit 0 = every check PASS, 1 = at least one FAIL (all are listed), 2 = usage error.
"""
import argparse
import ast
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")  # image_test_data.py:56
RESULTS = []


def rec(status, item, detail):
    RESULTS.append((status, item, detail))
    print(f"[verify] {status:<4} {item:<44} {detail}", flush=True)


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def evaluable_images(zpath):
    """Image count exactly as read_dataset()+decoded_images() would accept it; raises on what they would reject."""
    with zipfile.ZipFile(zpath) as z:
        names = z.namelist()
        if len(names) != len(set(names)):
            raise ValueError("duplicate archive entries")
        n = 0
        for name in sorted(names):
            if name.lower().endswith(EXTENSIONS):
                cap = str(Path(name).with_suffix(".txt"))
                text = z.read(cap).decode("utf-8") if cap in names else None
                if not text or not text.strip():
                    raise ValueError(f"image without caption: {name}")
                n += 1
        if n == 0:
            raise ValueError("no images")
        return n


def check_pins(root):
    pins = root / "artifacts" / "lora" / "PINS.tsv"
    if not pins.is_file():
        rec("FAIL", "lora pins", f"{pins} missing (run: python3 fetch_artifacts.py pin {root})")
        return
    rows = list(csv.DictReader(open(pins), delimiter="\t"))
    if not rows:
        rec("FAIL", "lora pins", "no rows")
        return
    for r in rows:
        item = f"pin {r.get('task8')}_{r.get('hk8')} ({r.get('role')} {r.get('model_type')})"
        why = []
        if "PIN FAILED" in (r.get("note") or ""):
            why.append(r["note"][:120])
        if not re.fullmatch(r"[0-9a-f]{40}", r.get("pinned_commit") or ""):
            why.append(f"pinned_commit {r.get('pinned_commit')!r}")
        if not re.fullmatch(r"[0-9a-f]{64}", r.get("lfs_sha256") or ""):
            why.append(f"lfs_sha256 {r.get('lfs_sha256')!r}")
        if not (r.get("bytes") or "").isdigit() or int(r["bytes"]) <= 0:
            why.append(f"bytes {r.get('bytes')!r}")
        if not (root / (r.get("task_id") or "-")).is_dir():
            why.append(f"no task dir {r.get('task_id')}")
        if why:
            rec("FAIL", item, "; ".join(why))
        else:
            rec("PASS", item, f"{r['file']} {r['bytes']} B @ {r['pinned_commit'][:12]} sha256 {r['lfs_sha256'][:16]}.. "
                f"(the box fetches it: fetch_artifacts.py)")


def check_complete(root, tasks, allow_no_manifest=False):
    """The archive holds every task its manifest.json plans, and nothing it does not (one PASS line, or a FAIL each)."""
    man = root / "manifest.json"
    if not man.is_file():
        rec("WARN" if allow_no_manifest else "FAIL", "archive completeness",
            f"no {man.name}: the planned task list cannot be checked"
            + (" (--allow-no-manifest)" if allow_no_manifest else " - an archive without one needs --allow-no-manifest"))
        return
    try:
        d = json.loads(man.read_text())
        planned = {}
        for t in d["tasks"]:
            tid = t["task_id"]
            if not UUID.match(tid or ""):
                raise ValueError(f"task_id {tid!r} is not a uuid")
            planned[tid] = f"{t.get('round') or '?'} {t.get('model_type_api') or t.get('model_type') or '?'}"
    except (OSError, ValueError, KeyError, TypeError) as e:
        rec("FAIL", "archive completeness", f"{man} unreadable or not the archive layout ({type(e).__name__}: {e})")
        return
    bad = 0
    if d.get("failures"):
        bad += 1
        rec("FAIL", "archive completeness", f"manifest.json lists {len(d['failures'])} archive failure(s): "
            f"{json.dumps(d['failures'])[:200]}")
    if d.get("n_zip_expected") is not None and d.get("n_zip_ok") != d.get("n_zip_expected"):
        bad += 1
        rec("FAIL", "archive completeness", f"manifest.json n_zip_ok={d.get('n_zip_ok')} != n_zip_expected={d.get('n_zip_expected')}")
    present = set(tasks)
    for tid in sorted(set(planned) - present):
        bad += 1
        rec("FAIL", f"{tid[:8]} planned", f"{tid} ({planned[tid]}) is in manifest.json but not in the archive (no <task-uuid>/ dir or link)")
    for tid in sorted(present - set(planned)):
        bad += 1
        rec("FAIL", f"{tid[:8]} unplanned", f"{tid}/ is in the archive but not in manifest.json")
    if not bad:
        rounds = {}
        for v in planned.values():
            rounds[v.split()[0]] = rounds.get(v.split()[0], 0) + 1
        rec("PASS", "archive completeness", f"{len(planned)}/{len(planned)} tasks of manifest.json present "
            f"({', '.join(f'{k} x{n}' for k, n in sorted(rounds.items()))}), failures=[]")


def check_tourn(root, api_flat, do_hash, lora="manifest", allow_no_manifest=False):
    root = Path(root)
    if not root.is_dir():
        rec("FAIL", "archive", f"{root} is not a directory")
        return
    tasks = sorted(p.name for p in root.iterdir() if p.is_dir() and UUID.match(p.name))
    layout = ""
    if (root / "tasks").is_dir():                       # the tasks/<round>_<family>_<task8>/ layout
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import archive_layout                           # kit-local (same dir as this file, on the workstation and the box)
        try:
            layout = archive_layout.detect(str(root))
            if layout == "tasks":
                n = len(archive_layout.mapping(str(root)))
                rec("FAIL", "archive", f"{root.name}: tasks/<round>_<family>_<task8>/ layout ({n} task dirs) and no "
                    f"<task-uuid>/ link - on the box: python3 archive_layout.py link --tourn {root} (stage.sh does); "
                    f"on the workstation: verify a view (archive_layout.py view --tourn {root} --out <empty dir>)")
                return
            if layout == "linked":
                for ok, tid, detail in archive_layout.check(str(root)):
                    rec("PASS" if ok else "FAIL", f"{tid[:8]} uuid link", detail)
        except archive_layout.LayoutError as e:
            rec("FAIL", "archive", f"{root.name}: tasks/ layout unmappable: {e}")
            return
    if not tasks:
        rec("FAIL", "archive", f"no <task-uuid>/ dirs under {root}")
        return
    rec("PASS" if (root / "ARCHIVE_DONE").exists() else "WARN", "archive", f"{root.name}: {len(tasks)} task dirs, "
        f"ARCHIVE_DONE {'present' if (root / 'ARCHIVE_DONE').exists() else 'ABSENT'}")
    check_complete(root, tasks, allow_no_manifest)
    for t in tasks:
        for zname in ("test_data.zip", "training_data.zip"):
            z = root / t / zname
            if not z.is_file():
                rec("FAIL", f"{t[:8]} {zname}", "missing")
                continue
            try:
                with zipfile.ZipFile(z) as zf:
                    bad = zf.testzip()
                if bad is not None:
                    rec("FAIL", f"{t[:8]} {zname}", f"CRC error in member {bad}")
                    continue
                if zname == "test_data.zip":
                    rec("PASS", f"{t[:8]} {zname}", f"{z.stat().st_size} B, {evaluable_images(z)} evaluable images")
                else:
                    rec("PASS", f"{t[:8]} {zname}", f"{z.stat().st_size} B, CRC ok")
            except (zipfile.BadZipFile, ValueError, KeyError, OSError) as e:
                rec("FAIL", f"{t[:8]} {zname}", f"{type(e).__name__}: {e}")
        aj = root / "api" / f"{t}.json"
        try:
            d = json.loads(aj.read_text())
            if d.get("task_id") != t or not d.get("model_type") or not d.get("model_id"):
                raise ValueError("task_id/model_type/model_id missing or inconsistent")
            rec("PASS", f"{t[:8]} api json", f"{d['model_type']} {d['model_id']} hours={d.get('hours_to_complete')}")
        except (OSError, ValueError) as e:
            rec("FAIL", f"{t[:8]} api json", f"{aj}: {type(e).__name__}: {e}")
            continue
        if api_flat:
            flat = Path(api_flat) / f"{t}.json"
            if not flat.is_file() or flat.read_bytes() != aj.read_bytes():
                rec("FAIL", f"{t[:8]} api flat copy", f"{flat} missing or differs from {aj}")
    if lora == "none":
        rec("PASS", "lora", "skipped (--no-lora: task data only)")
        return
    if lora == "pins":
        check_pins(root)
        return
    man = root / "artifacts" / "lora" / "MANIFEST.tsv"
    if not man.is_file():
        hint = ("PINS.tsv present: run fetch_artifacts.py on the box (stage.sh does, LORA_VIA=box)"
                if (root / "artifacts" / "lora" / "PINS.tsv").is_file() else "run fetch_artifacts.py on the workstation first")
        rec("FAIL", "lora manifest", f"{man} missing ({hint})")
        return
    rows = list(csv.DictReader(open(man), delimiter="\t"))
    if not rows:
        rec("FAIL", "lora manifest", "no rows")
        return
    for r in rows:
        item = f"lora {r['task8']}_{r['hk8']} ({r['role']} {r['model_type']})"
        if not r.get("status", "").startswith("OK"):
            rec("FAIL", item, f"manifest status {r.get('status')}")
            continue
        f = root / "artifacts" / "lora" / f"{r['task8']}_{r['hk8']}.safetensors"
        if not f.is_file():
            rec("FAIL", item, f"{f} missing")
            continue
        size = f.stat().st_size
        if str(size) != r["bytes"]:
            rec("FAIL", item, f"size {size} != manifest {r['bytes']}")
            continue
        if not do_hash:
            rec("PASS", item, f"{size} B (hash skipped)")
            continue
        digest = sha256_of(f)
        if digest != r["sha256"] or digest != r["lfs_sha256"]:
            rec("FAIL", item, f"sha256 {digest[:16]} != manifest {r['sha256'][:16]} / lfs {r['lfs_sha256'][:16]}")
        else:
            rec("PASS", item, f"{size} B sha256 {digest[:16]}.. == HF LFS oid (pinned {r['pinned_commit'][:12]})")


def check_repo(repo, expect):
    try:
        head = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", repo, "status", "--porcelain"], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as e:
        rec("FAIL", "repo", f"{repo}: {type(e).__name__}")
        return
    if expect and head != expect:
        rec("FAIL", "repo", f"HEAD {head} != expected {expect}")
    elif dirty:
        rec("FAIL", "repo", f"HEAD {head[:12]} but the tree is dirty: {dirty.splitlines()[:3]}")
    else:
        rec("PASS", "repo", f"{repo} HEAD {head} clean")


def check_ops(ops):
    ops = Path(ops)
    files = sorted(p for p in ops.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    bad = 0
    for p in files:
        if p.suffix == ".sh":
            r = subprocess.run(["bash", "-n", str(p)], capture_output=True, text=True)
            if r.returncode != 0:
                bad += 1
                rec("FAIL", f"ops {p.relative_to(ops)}", f"bash -n: {r.stderr.strip()[:160]}")
        elif p.suffix == ".py":
            try:
                ast.parse(p.read_text(), str(p))
            except SyntaxError as e:
                bad += 1
                rec("FAIL", f"ops {p.relative_to(ops)}", f"syntax: {e}")
    n_sh = sum(p.suffix == ".sh" for p in files)
    n_py = sum(p.suffix == ".py" for p in files)
    if not bad:
        rec("PASS", "ops syntax", f"{ops}: {n_sh} .sh bash -n ok, {n_py} .py parse ok")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tourn")
    ap.add_argument("--api-flat")
    ap.add_argument("--repo")
    ap.add_argument("--expect-sha")
    ap.add_argument("--repo-spec", action="append", default=[])
    ap.add_argument("--pins-only", action="store_true")
    ap.add_argument("--no-lora", action="store_true")
    ap.add_argument("--ops")
    ap.add_argument("--no-hash", action="store_true")
    ap.add_argument("--allow-no-manifest", action="store_true")
    a = ap.parse_args()
    if not (a.tourn or a.repo or a.repo_spec or a.ops):
        ap.error("nothing to verify: give --tourn, --repo/--repo-spec and/or --ops")
    if a.pins_only and a.no_lora:
        ap.error("--pins-only and --no-lora exclude each other")
    if a.tourn:
        check_tourn(a.tourn, a.api_flat, not a.no_hash, "pins" if a.pins_only else "none" if a.no_lora else "manifest",
                    a.allow_no_manifest)
    if a.repo:
        check_repo(a.repo, a.expect_sha)
    for spec in a.repo_spec:
        d, sep, sha = spec.rpartition("=")
        if not sep or not d or not re.fullmatch(r"[0-9a-f]{40}", sha):
            ap.error(f"--repo-spec {spec!r} is not DIR=<40-hex sha>")
        check_repo(d, sha)
    if a.ops:
        check_ops(a.ops)
    n_fail = sum(s == "FAIL" for s, _, _ in RESULTS)
    n_pass = sum(s == "PASS" for s, _, _ in RESULTS)
    print(f"[verify] summary: PASS={n_pass} FAIL={n_fail} WARN={sum(s == 'WARN' for s, _, _ in RESULTS)}", flush=True)
    sys.exit(1 if n_fail else 0)


if __name__ == "__main__":
    main()
