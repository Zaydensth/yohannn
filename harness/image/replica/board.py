#!/usr/bin/env python3
"""board.py - per-task board from score_latent.sh result TSVs, joined with the OFFICIAL board of the archived task.

usage: python3 board.py [--tsv FILE ...] [--api DIR] [--task PREFIX ...] [--boss HK8] [--ours HK8]
       (--boss/--ours default to the env BOSS_HK8 / OURS_HK8: the first 8 characters of the hotkeys you want as the
       BOSS and OURS reference columns; empty = no such column)

  * rows: the LATEST row per (task, label) across all --tsv files (a re-score replaces an older one); FAILED rows are
    listed with their status, never silently dropped.
  * official: api/<task>.json hotkey_details[].test_loss for the hotkey whose 8-char prefix appears in the label
    (anchors.sh labels are ROLE_<task8>_<hk8>, e.g. BOSS_<task8>_<hk8>). delta_ln% = 100*ln(replica/official)
    is the harness error on that artifact (the calibration of your replica); it should be ~0 when the replica is
    faithful.
  * gap columns are 100*ln(x/y) against the TOP1 (the #1 entry of the task), BOSS and OURS replica rows of the same
    task (same harness, same draw of the metric, so harness bias cancels): negative = better (lower loss).
Stdlib only (python3 >= 3.8).
"""
import argparse
import csv
import glob
import json
import math
import os
import re
import sys


def load_rows(paths):
    latest = {}
    for path in paths:
        with open(path) as fh:
            for r in csv.DictReader(fh, delimiter="\t"):
                key = (r["task"], r["label"])
                if key not in latest or r["timestamp"] >= latest[key]["timestamp"]:
                    latest[key] = r
    return latest


def official_board(api_dir, task):
    hits = glob.glob(os.path.join(api_dir, f"{task}*.json"))
    if not hits:
        return None, {}
    d = json.load(open(hits[0]))
    board = {}
    for h in d.get("hotkey_details") or []:
        loss = h.get("test_loss")
        board[h["hotkey"][:8]] = {"rank": h.get("rank"), "loss": float(loss) if loss not in (None, "") else None}
    return d, board


def ln_pct(a, b):
    return 100.0 * math.log(a / b) if a and b and a > 0 and b > 0 else None


def fmt(x, spec="+.3f"):
    return "-" if x is None else format(x, spec)


def main():
    ap = argparse.ArgumentParser()
    root = os.environ.get("LATENT_ROOT", os.path.join(os.environ.get("KIT_ROOT", "/root/kit"), "latent"))
    ap.add_argument("--tsv", nargs="+", default=None)
    ap.add_argument("--api", default=os.environ.get("API_DIR", os.path.join(os.environ.get("KIT_ROOT", "/root/kit"), "api")))
    ap.add_argument("--task", nargs="*", default=[])
    ap.add_argument("--boss", default=os.environ.get("BOSS_HK8", ""))
    ap.add_argument("--ours", default=os.environ.get("OURS_HK8", ""))
    a = ap.parse_args()
    tsvs = a.tsv or sorted(glob.glob(os.path.join(root, "results_*.tsv")))
    if not tsvs:
        sys.exit("board.py: no result TSVs")
    rows = load_rows(tsvs)
    tasks = sorted({t for t, _ in rows if not a.task or any(t.startswith(p) for p in a.task)})
    calib = []
    for task in tasks:
        d, board = official_board(a.api, task)
        mine = [r for (t, _), r in rows.items() if t == task]
        ok = {r["label"]: float(r["final"]) for r in mine if r["status"] == "OK" and r["final"]}
        # an anchors.sh SOURCE=both label <label>__hf is the same anchor through HF - the references stay the local ones
        boss = next((v for k, v in ok.items() if a.boss and a.boss in k and k.startswith("BOSS") and not k.endswith("__hf")), None)
        ours = next((v for k, v in ok.items() if a.ours and a.ours in k and k.startswith("OURS") and not k.endswith("__hf")), None)
        top1 = next((v for k, v in ok.items() if k.startswith("TOP1_") and not k.endswith("__hf")), None)
        head = f"== {task[:8]}"
        if d:
            head += f" {d.get('model_type')} hours={d.get('hours_to_complete')} base={d.get('model_id')}"
        print(head)
        print(f"   {'label':<34} {'status':<26} {'replica':>10} {'text':>9} {'no_text':>9} {'official':>10} "
              f"{'delta_ln%':>9} {'vs_top1%':>8} {'vs_boss%':>8} {'vs_ours%':>8} {'fp8_ops':>8} {'wall_s':>6}")
        for r in sorted(mine, key=lambda r: (r["status"] != "OK", float(r["final"] or 9e9))):
            fin = float(r["final"]) if r["status"] == "OK" and r["final"] else None
            hk = next((h for h in board if re.search(re.escape(h), r["label"])), None)
            off = board.get(hk, {}).get("loss") if hk else None
            delta = ln_pct(fin, off)
            if delta is not None and r["label"].startswith(("BOSS", "OURS", "TOP1", "TOP2")):
                calib.append((task[:8], r["label"], delta))
            text = float(r["text_mean"]) if r["text_mean"] else None
            no_text = float(r["no_text_mean"]) if r["no_text_mean"] else None
            print(f"   {r['label'][:34]:<34} {r['status'][:26]:<26} {fmt(fin, '.6f'):>10} {fmt(text, '.5f'):>9} "
                  f"{fmt(no_text, '.5f'):>9} {fmt(off, '.6f'):>10} {fmt(delta):>9} {fmt(ln_pct(fin, top1), '+.2f'):>8} "
                  f"{fmt(ln_pct(fin, boss), '+.2f'):>8} "
                  f"{fmt(ln_pct(fin, ours), '+.2f'):>8} {r.get('fp8_ops', ''):>8} {r.get('wall_s', ''):>6}")
        for lab, v in sorted(ok.items()):       # the HF-vs-disk comparison of an anchor (SOURCE=both, once per family)
            if lab.endswith("__hf") and lab[:-4] in ok:
                print(f"   hf-vs-disk {lab[:-4]}: ln(hf/local) = {fmt(ln_pct(v, ok[lab[:-4]]), '+.4f')} % "
                      f"(the same pinned anchor: through its HF repo vs the sha256-checked local copy)")
    if calib:
        ds = [x[2] for x in calib]
        print(f"== harness calibration on anchors (replica vs official, ln%): n={len(ds)} "
              f"mean={sum(ds) / len(ds):+.3f} max_abs={max(abs(x) for x in ds):.3f}")
        for t, lab, dl in sorted(calib, key=lambda x: -abs(x[2])):
            print(f"   {t} {lab:<34} {dl:+.3f}")


if __name__ == "__main__":
    main()
