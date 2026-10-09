#!/usr/bin/env python3
"""fetch_artifacts.py - network only, anonymous, read-only on HF. Nothing is uploaded.

Two ways to know WHICH bytes an anchor is (pinned commit + file + size + LFS sha256), one download path:

  python3 fetch_artifacts.py pin ARCHIVE [--tasks t8,t8,...] [--boss HK8] [--ours HK8] [--top N]   (workstation)
      For an archive whose artifacts/ holds no pinned specs: reads api/<task>.json and writes
      ARCHIVE/artifacts/lora/PINS.tsv with one row per (task, role):
        TOP1..TOPN = the N valid entries with the lowest test_loss (> 0, finite) on the official board of the task
        (default N=1), BOSS = --boss (the current champion's 8-char hotkey prefix; '' = none), OURS = --ours (yours);
      a hotkey holding several roles is pinned ONCE, under the first of OURS, BOSS, TOP1, TOP2, ... (the other roles are
      named in the note column). Default tasks = the archive's R1 tasks (manifest.json round == "R1").
        python3 fetch_artifacts.py pin <archive_root>/tourn_<id>_<yyyymmdd> --top 2 --boss <hk8>
      Repo = hotkey_details[].repo. Commit = the newest commit of the repo dated <= the task's updated_at (when the
      validator finished scoring it) - HEAD when HEAD is older, which the PINS row says (head_matches); a repo pushed
      to after scoring is pinned to the scored commit, not to HEAD. File = select_lora on that commit's file list
      (G.O.D validator/evaluation/image_artifacts.py:36-46), size + LFS sha256 from
      GET /api/models/<repo>/revision/<commit>?blobs=true.
  python3 fetch_artifacts.py ARCHIVE                                                         (box or workstation)
      Downloads every pinned anchor into ARCHIVE/artifacts/lora/<task8>_<hk8>.safetensors from
      https://huggingface.co/<repo>/resolve/<pinned commit>/<file>, verifies size + sha256 against the pinned LFS oid
      before an atomic rename (a file that is already present and verifies is skipped), and writes
      ARCHIVE/artifacts/lora/MANIFEST.tsv - one row per anchor, never dropped; failures have status != OK.
      Pins come from ARCHIVE/artifacts/lora/PINS.tsv when it exists (HOTKEYS is then ignored: the pins name every
      anchor), else from per-artifact spec files ARCHIVE/artifacts/<R>_<task8>_<hk8>.json (hf_sha + select_lora +
      lfs_sha256), if your archiver writes them, filtered by HOTKEYS. The repo's CURRENT HEAD is recorded too: the
      evaluator resolves HEAD at eval time (image_artifacts.py:54-55), so head != pinned is flagged.

usage: python3 fetch_artifacts.py ARCHIVE_DIR
env:   HOTKEYS=<hk8>,<hk8>   (spec-file mode only)   BOSS_HK8 / OURS_HK8 (defaults of --boss / --ours and the role of a
       spec-file row)   HF_API_BASE [https://huggingface.co]
"""
import argparse
import csv
import datetime
import hashlib
import http.client
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HOTKEYS = [h for h in os.environ.get("HOTKEYS", "").split(",") if h]
ROLE = {h: r for h, r in ((os.environ.get("BOSS_HK8", ""), "BOSS"), (os.environ.get("OURS_HK8", ""), "OURS")) if h}
API = os.environ.get("HF_API_BASE", "https://huggingface.co").rstrip("/")
UA = {"User-Agent": "sn56-harness-fetch/1 (read-only)"}
COLS = ["task8", "hk8", "role", "round", "model_type", "task_id", "repo", "file", "pinned_commit", "head_commit",
        "head_matches", "bytes", "sha256", "lfs_sha256", "official_test_loss", "status", "path"]
PIN_COLS = ["task8", "hk8", "role", "round", "model_type", "task_id", "repo", "file", "pinned_commit", "pinned_date",
            "head_commit", "head_date", "head_matches", "scored_at", "bytes", "lfs_sha256", "official_test_loss",
            "official_rank", "note"]
ROLE_PRECEDENCE = ("OURS", "BOSS", "TOP1")


def get_json(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
        return json.loads(r.read())


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def select_lora(files):
    """G.O.D validator/evaluation/image_artifacts.py:36-46, verbatim logic."""
    files = sorted(f for f in files if f.startswith(("checkpoints/", "checkpoint/")) and f.endswith(".safetensors"))
    lasts = [f for f in files if Path(f).name == "last.safetensors"]
    if len(lasts) == 1:
        return lasts[0]
    numbered = [(int(m.group(1)), f) for f in files if (m := re.search(r"[-_](\d+)\.safetensors$", f))]
    if numbered:
        return max(numbered)[1]
    if len(files) == 1:
        return files[0]
    raise ValueError("No unambiguous LoRA checkpoint")


def download(url, dst, expect_size, attempts=4):
    """Fresh download per attempt; a short read (connection closed early, which urllib can report as a plain EOF)
    is retried instead of being accepted. The caller still verifies size + sha256."""
    tmp = dst.with_suffix(".part")
    t0 = time.time()
    h, n = hashlib.sha256(), 0
    for attempt in range(1, attempts + 1):
        h = hashlib.sha256()
        n = 0
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as r, open(tmp, "wb") as f:
                for chunk in iter(lambda: r.read(8 << 20), b""):
                    f.write(chunk)
                    h.update(chunk)
                    n += len(chunk)
        except (urllib.error.URLError, OSError, http.client.HTTPException) as e:  # IncompleteRead is an HTTPException
            print(f"[fetch]   attempt {attempt}: {type(e).__name__} after {n} bytes", flush=True)
            continue
        if n == expect_size:
            break
        print(f"[fetch]   attempt {attempt}: short read {n} of {expect_size} bytes, retrying", flush=True)
    return tmp, n, h.hexdigest(), time.time() - t0


# ---------------------------------------------------------------------------------------------------- pins
def _iso(s):
    return datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def _valid_loss(h):
    try:
        v = float(h.get("test_loss"))
    except (TypeError, ValueError):
        return None
    return v if v > 0 and v == v and v != float("inf") else None


def pin_archive(archive, tasks, boss, ours, top=1):
    role_precedence = ROLE_PRECEDENCE[:2] + tuple(f"TOP{i}" for i in range(1, top + 1))   # OURS, BOSS, TOP1, TOP2, ...
    out = archive / "artifacts" / "lora"
    out.mkdir(parents=True, exist_ok=True)
    rounds = {}
    man = archive / "manifest.json"
    if man.is_file():
        for t in json.load(open(man)).get("tasks") or []:
            rounds[t.get("task_id")] = t.get("round") or ""
    if not tasks:
        tasks = [t for t, r in rounds.items() if r == "R1"]
    task_ids = []
    for t8 in tasks:
        hits = sorted(p.stem for p in (archive / "api").glob("*.json")
                      if p.stem.startswith(t8) and re.fullmatch(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}", p.stem))
        if len(hits) != 1:
            sys.exit(f"[pin] task {t8}: {len(hits)} api json matches in {archive / 'api'}")
        task_ids.append(hits[0])
    rows, bad = [], 0
    for task in task_ids:
        d = json.load(open(archive / "api" / f"{task}.json"))
        board = d.get("hotkey_details") or []
        valid = sorted((h for h in board if _valid_loss(h) is not None), key=lambda h: _valid_loss(h))
        want = {}
        for i, h in enumerate(valid[:top], 1):
            want.setdefault(h["hotkey"][:8], set()).add(f"TOP{i}")
        for hk8, role in ((boss, "BOSS"), (ours, "OURS")):
            if hk8:
                want.setdefault(hk8, set()).add(role)
        scored_at = d.get("updated_at") or ""
        for hk8, roles in sorted(want.items()):
            role = next(r for r in role_precedence if r in roles)
            h = next((x for x in board if (x.get("hotkey") or "")[:8] == hk8), None)
            row = {c: "" for c in PIN_COLS}
            row.update(task8=task[:8], hk8=hk8, role=role, round=rounds.get(task, ""), model_type=d.get("model_type"),
                       task_id=task, scored_at=scored_at,
                       note=("also " + "+".join(sorted(roles - {role})) if len(roles) > 1 else ""))
            try:
                if h is None:
                    raise RuntimeError(f"hotkey {hk8} is not on the board of {task[:8]}")
                if _valid_loss(h) is None:
                    raise RuntimeError(f"hotkey {hk8} has no valid test_loss on {task[:8]} ({h.get('test_loss')!r})")
                repo = h.get("repo") or ""
                if not repo:
                    raise RuntimeError("no hotkey_details repo")
                row.update(repo=repo, official_test_loss=repr(_valid_loss(h)),
                           official_rank=str(1 + sum(1 for x in valid if _valid_loss(x) < _valid_loss(h))))
                q = urllib.parse.quote(repo, safe="/")
                commits = get_json(f"{API}/api/models/{q}/commits/main")
                if not commits:
                    raise RuntimeError("no commits")
                head = commits[0]
                pinned = head
                if scored_at:
                    cutoff = _iso(scored_at)
                    before = [c for c in commits if _iso(c["date"]) <= cutoff]
                    if not before:
                        raise RuntimeError(f"no commit dated <= scored_at {scored_at}")
                    pinned = max(before, key=lambda c: _iso(c["date"]))
                info = get_json(f"{API}/api/models/{q}/revision/{pinned['id']}?blobs=true")
                sibs = {s["rfilename"]: s for s in info.get("siblings") or []}
                sel = select_lora(list(sibs))
                meta = sibs[sel]
                lfs = (meta.get("lfs") or {}).get("sha256") or ""
                if not lfs or not meta.get("size"):
                    raise RuntimeError(f"{sel}: no LFS sha256/size in the revision listing")
                row.update(file=sel, pinned_commit=pinned["id"], pinned_date=pinned.get("date", ""),
                           head_commit=head["id"], head_date=head.get("date", ""),
                           head_matches=str(head["id"] == pinned["id"]), bytes=str(meta["size"]), lfs_sha256=lfs)
                print(f"[pin] {task[:8]} {role:<4} {hk8} rank={row['official_rank']} {sel} {meta['size']} B "
                      f"commit {pinned['id'][:12]} ({pinned.get('date', '')}) head_matches={row['head_matches']} "
                      f"sha256 {lfs[:16]}", flush=True)
            except (urllib.error.URLError, OSError, http.client.HTTPException, RuntimeError, KeyError, ValueError) as e:
                bad += 1
                row["note"] = (row["note"] + "; " if row["note"] else "") + f"PIN FAILED {type(e).__name__}: {str(e)[:160]}"
                print(f"[pin] {task[:8]} {role} {hk8} FAILED {type(e).__name__}: {e}", flush=True)
            rows.append(row)
    tmp = out / "PINS.tsv.tmp"
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=PIN_COLS, delimiter="\t", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, out / "PINS.tsv")
    print(f"[pin] rows={len(rows)} failed={bad} -> {out / 'PINS.tsv'}", flush=True)
    return 1 if bad or not rows else 0


def specs_from_pins(archive):
    out = []
    for r in csv.DictReader(open(archive / "artifacts" / "lora" / "PINS.tsv"), delimiter="\t"):
        if "PIN FAILED" in (r.get("note") or "") or not r.get("pinned_commit"):
            out.append((r, None))
            continue
        out.append((r, {"repo": r["repo"], "file": r["file"], "pinned_commit": r["pinned_commit"],
                        "lfs_sha256": r["lfs_sha256"], "size": int(r["bytes"]), "test_loss": r.get("official_test_loss")}))
    return out


def specs_from_archive_json(archive):
    out = []
    for spec in sorted(p for p in (archive / "artifacts").glob("R*_*_*.json")):
        d = json.load(open(spec))
        hk8 = d.get("hk8", "")
        if hk8 not in HOTKEYS:
            continue
        sel = (d.get("select_lora") or {}).get("selected")
        meta = next((f for f in d.get("files", []) if f.get("path") == sel), None)
        r = {"task8": d["task_id"][:8], "hk8": hk8, "role": ROLE.get(hk8, "OTHER"), "round": d.get("round"),
             "model_type": d.get("model_type"), "task_id": d["task_id"], "repo": d.get("repo"), "file": sel,
             "official_test_loss": d.get("test_loss")}
        if not sel or not meta or not (meta or {}).get("lfs_sha256") or not d.get("hf_sha"):
            out.append((r, None))
            continue
        out.append((r, {"repo": d.get("repo"), "file": sel, "pinned_commit": d.get("hf_sha"),
                        "lfs_sha256": meta["lfs_sha256"], "size": meta["size"], "test_loss": d.get("test_loss")}))
    return out


def fetch_archive(archive):
    out = archive / "artifacts" / "lora"
    out.mkdir(parents=True, exist_ok=True)
    pins = out / "PINS.tsv"
    if pins.is_file():
        if os.environ.get("HOTKEYS"):
            print(f"[fetch] pins mode ({pins}): HOTKEYS={os.environ['HOTKEYS']} is ignored - the pins name every anchor", flush=True)
        specs = specs_from_pins(archive)
    else:
        specs = specs_from_archive_json(archive)
    rows = []
    for r0, sp in specs:
        row = {c: "" for c in COLS}
        row.update({k: r0.get(k, "") for k in ("task8", "hk8", "role", "round", "model_type", "task_id", "repo", "file")})
        row["official_test_loss"] = r0.get("official_test_loss", "")
        dst = out / f"{row['task8']}_{row['hk8']}.safetensors"
        row["path"] = str(dst)
        try:
            if sp is None:
                raise RuntimeError("no usable pin (pins row failed, or the archive json lacks select_lora/lfs_sha256/hf_sha)")
            row.update(pinned_commit=sp["pinned_commit"], lfs_sha256=sp["lfs_sha256"], file=sp["file"], repo=sp["repo"])
            head = get_json(f"{API}/api/models/{urllib.parse.quote(sp['repo'], safe='/')}").get("sha", "")
            row["head_commit"] = head
            row["head_matches"] = str(head == row["pinned_commit"])
            if dst.exists() and dst.stat().st_size == sp["size"] and sha256_of(dst) == row["lfs_sha256"]:
                row.update(bytes=dst.stat().st_size, sha256=row["lfs_sha256"], status="OK(cached)")
            else:
                url = f"{API}/{sp['repo']}/resolve/{row['pinned_commit']}/{sp['file']}"
                tmp, n, digest, secs = download(url, dst, sp["size"])
                if n != sp["size"] or digest != row["lfs_sha256"]:
                    tmp.unlink(missing_ok=True)
                    raise RuntimeError(f"verify failed: bytes {n} vs {sp['size']}, sha {digest[:12]} vs {row['lfs_sha256'][:12]}")
                os.replace(tmp, dst)
                row.update(bytes=n, sha256=digest, status="OK")
                print(f"[fetch] {row['task8']}_{row['hk8']} {row['role']} {n/1e6:.1f} MB in {secs:.0f}s sha256={digest[:16]}", flush=True)
        except (urllib.error.URLError, OSError, http.client.HTTPException, RuntimeError, KeyError, ValueError) as e:
            row["status"] = f"FAILED({type(e).__name__}: {str(e)[:120]})"
            print(f"[fetch] {row['task8']}_{row['hk8']} {row['status']}", flush=True)
        rows.append(row)
    rows.sort(key=lambda r: (r["role"], r["task8"], r["hk8"]))
    tmp = out / "MANIFEST.tsv.tmp"
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS, delimiter="\t", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, out / "MANIFEST.tsv")
    bad = [r for r in rows if not r["status"].startswith("OK")]
    print(f"[fetch] rows={len(rows)} ok={len(rows) - len(bad)} failed={len(bad)} -> {out / 'MANIFEST.tsv'}")
    return 1 if bad or not rows else 0


def main():
    argv = sys.argv[1:]
    if argv[:1] == ["pin"]:
        ap = argparse.ArgumentParser(prog="fetch_artifacts.py pin")
        ap.add_argument("archive")
        ap.add_argument("--tasks", default="", help="comma-separated task8 prefixes (default: the archive's R1 tasks)")
        ap.add_argument("--boss", default=os.environ.get("BOSS_HK8", ""), help="8-char hotkey prefix of the champion")
        ap.add_argument("--ours", default=os.environ.get("OURS_HK8", ""), help="8-char prefix of your own hotkey")
        ap.add_argument("--top", type=int, default=1, help="pin TOP1..TOPN (the N best valid entries per task)")
        a = ap.parse_args(argv[1:])
        if not 1 <= a.top <= 9:
            ap.error("--top must be 1..9")
        sys.exit(pin_archive(Path(a.archive), [t for t in a.tasks.split(",") if t], a.boss, a.ours, a.top))
    if len(argv) != 1 or argv[0].startswith("-"):
        sys.exit("usage: fetch_artifacts.py ARCHIVE_DIR   |   fetch_artifacts.py pin ARCHIVE_DIR [--tasks ..] [--top N]")
    sys.exit(fetch_archive(Path(argv[0])))


if __name__ == "__main__":
    main()
