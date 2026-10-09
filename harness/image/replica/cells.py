#!/usr/bin/env python3
"""cells.py - read and validate the cells (queue) file that cell_run.sh and queue.sh execute. stdlib, python >= 3.8.

Format (TSV, see cells.example.tsv): line 1 is the lock line ('# lock ...', written by lock_cells.sh; '# lock UNBOUND'
before the lock), line 2 the header, then one row per cell. Rows starting with '#' are comments. File order is the
execution order.

  cell  enabled  tourn  task_id  trainer_image  hours  env  note
    cell           unique name [A-Za-z0-9][A-Za-z0-9._-]{0,63}
    enabled        1 = run, 0 = skip (the ONLY column that may change after the lock)
    tourn          the archive dir under $KIT_ROOT/data/ (tourn_<id>_<yyyymmdd>)
    task_id        the task's full uuid (data/<tourn>/<task_id>/ + api/<task_id>.json)
    trainer_image  a local docker image tag built from YOUR miner repo (see README: classic builder, --no-cache)
    hours          'task' = the task's own hours_to_complete (what production uses), or a positive number
    env            space-separated KEY=VALUE passed with -e to the trainer - NOT production (the validator sets no
                   custom env): only for A/B arms; credential, cache and GPU names are refused. Empty = production.
    note           free text

  validate --cells C            exit 0 iff every row is well-formed (all problems listed)
  list --cells C                one line per row: <cell> <TAB> <enabled>
  row --cells C --cell X        shell-quoted CELL_* variables of that row, for `eval`
  result --tsv F key=value ...  append one row to the results TSV (fixed columns RESULT_COLUMNS, flock'd; a file with
                                another header is refused, never appended to)
  latest --tsv F                the newest row per cell: <cell> TAB <status> TAB <exit_code> TAB <run_dir>
"""
import argparse
import datetime
import fcntl
import os
import re
import shlex
import sys

COLUMNS = ["cell", "enabled", "tourn", "task_id", "trainer_image", "hours", "env", "note"]
CELL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
TOURN_RE = re.compile(r"^tourn_[0-9a-f]+_[0-9]{8}$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
IMAGE_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]*(:[A-Za-z0-9._-]+)?(@sha256:[0-9a-f]{64})?$")
ENV_KEY_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
ENV_VAL_RE = re.compile(r"^[A-Za-z0-9._:/,+=@-]*$")
RESULT_COLUMNS = ["timestamp", "cell", "attempt", "task_id", "family", "status", "exit_code", "final", "text_mean",
                  "no_text_mean", "n_images", "train_rc", "timed_out", "train_wall_s", "hours", "trainer_image",
                  "image_id", "hf_repo", "hf_revision", "local_sha256", "hub_sha256", "fetch_sha256", "run_dir", "note"]
ENV_DENY = re.compile(r"^(HF_|HUGGING|WANDB_|TRANSFORMERS_CACHE$|CUDA_|NVIDIA_|PATH$|LD_|PYTHONPATH$|HOME$)")


def read(path):
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if not lines or not lines[0].startswith("#"):
        raise ValueError(f"{path}: line 1 must be the lock line ('# lock ...'; '# lock UNBOUND' before the lock)")
    if len(lines) < 2 or lines[1].split("\t") != COLUMNS:
        raise ValueError(f"{path}: line 2 must be the header {' '.join(COLUMNS)} (tab-separated)")
    rows = []
    for n, line in enumerate(lines[2:], 3):
        if not line.strip() or line.startswith("#"):
            continue
        f = line.split("\t")
        if len(f) != len(COLUMNS):
            raise ValueError(f"{path} L{n}: {len(f)} fields, the header has {len(COLUMNS)}")
        rows.append(dict(zip(COLUMNS, f), _line=n))
    return rows


def problems(rows):
    out, seen = [], set()
    for r in rows:
        where = f"L{r['_line']} {r['cell']!r}"
        if not CELL_RE.match(r["cell"]):
            out.append(f"{where}: bad cell name")
        if r["cell"] in seen:
            out.append(f"{where}: duplicate cell name")
        seen.add(r["cell"])
        if r["enabled"] not in ("0", "1"):
            out.append(f"{where}: enabled must be 0 or 1")
        if r["enabled"] != "1":
            continue
        if not TOURN_RE.match(r["tourn"]):
            out.append(f"{where}: tourn {r['tourn']!r} is not tourn_<id>_<yyyymmdd>")
        if not UUID_RE.match(r["task_id"]):
            out.append(f"{where}: task_id {r['task_id']!r} is not a task uuid")
        if not IMAGE_RE.match(r["trainer_image"]):
            out.append(f"{where}: trainer_image {r['trainer_image']!r} is not a docker image reference")
        if r["hours"] != "task":
            try:
                ok = float(r["hours"]) > 0
            except ValueError:
                ok = False
            if not ok:
                out.append(f"{where}: hours must be 'task' or a positive number")
        for tok in r["env"].split():
            k, sep, v = tok.partition("=")
            if not sep or not ENV_KEY_RE.match(k) or not ENV_VAL_RE.match(v):
                out.append(f"{where}: env token {tok!r} is not KEY=VALUE (KEY [A-Z_][A-Z0-9_]*, no spaces/shell chars)")
            elif ENV_DENY.match(k):
                out.append(f"{where}: env {k} is refused (credentials, caches and GPU selection belong to the runner)")
    return out


def cmd_result(a):
    row = {c: "" for c in RESULT_COLUMNS}
    for item in a.items:
        k, sep, v = item.partition("=")
        if not sep or k not in row:
            print(f"cells.py result: unknown column {k!r} (columns: {', '.join(RESULT_COLUMNS)})", file=sys.stderr)
            sys.exit(2)
        row[k] = v.replace("\t", " ").replace("\n", " ")
    row["timestamp"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    os.makedirs(os.path.dirname(os.path.abspath(a.tsv)), exist_ok=True)
    with open(a.tsv + ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        new = not os.path.isfile(a.tsv) or os.path.getsize(a.tsv) == 0
        if not new:
            with open(a.tsv) as fh:
                if fh.readline().rstrip("\n").split("\t") != RESULT_COLUMNS:
                    print(f"cells.py result: {a.tsv} has a foreign header - refusing to append", file=sys.stderr)
                    sys.exit(1)
        with open(a.tsv, "a") as fh:
            if new:
                fh.write("\t".join(RESULT_COLUMNS) + "\n")
            fh.write("\t".join(row[c] for c in RESULT_COLUMNS) + "\n")


def cmd_latest(a):
    if not os.path.isfile(a.tsv):
        return
    with open(a.tsv) as fh:
        head = fh.readline().rstrip("\n").split("\t")
        if head != RESULT_COLUMNS:
            print(f"cells.py latest: {a.tsv} has a foreign header", file=sys.stderr)
            sys.exit(1)
        latest = {}
        for line in fh:
            f = line.rstrip("\n").split("\t")
            if len(f) == len(RESULT_COLUMNS):
                r = dict(zip(RESULT_COLUMNS, f))
                latest[r["cell"]] = r
    for cell, r in latest.items():
        print(f"{cell}\t{r['status']}\t{r['exit_code']}\t{r['run_dir']}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("result")
    s.add_argument("--tsv", required=True)
    s.add_argument("items", nargs="*")
    s = sub.add_parser("latest")
    s.add_argument("--tsv", required=True)
    for name in ("validate", "list", "row"):
        s = sub.add_parser(name)
        s.add_argument("--cells", required=True)
        if name == "row":
            s.add_argument("--cell", required=True)
    a = ap.parse_args()
    if a.cmd in ("result", "latest"):
        return cmd_result(a) if a.cmd == "result" else cmd_latest(a)
    try:
        rows = read(a.cells)
    except (OSError, ValueError) as e:
        print(f"cells.py: {e}", file=sys.stderr)
        sys.exit(1)
    if a.cmd == "list":
        for r in rows:
            print(f"{r['cell']}\t{r['enabled']}")
        return
    bad = problems(rows)
    if a.cmd == "validate":
        for b in bad:
            print(f"cells.py: {b}", file=sys.stderr)
        print(f"CELLS_ROWS={len(rows)} CELLS_ENABLED={sum(r['enabled'] == '1' for r in rows)} CELLS_PROBLEMS={len(bad)}")
        sys.exit(1 if bad else 0)
    match = [r for r in rows if r["cell"] == a.cell]
    if len(match) != 1:
        print(f"cells.py: cell {a.cell!r} found {len(match)} time(s) in {a.cells}", file=sys.stderr)
        sys.exit(1)
    mine = [b for b in bad if f" {a.cell!r}" in b]
    if mine:
        for b in mine:
            print(f"cells.py: {b}", file=sys.stderr)
        sys.exit(1)
    for k in COLUMNS:
        print(f"CELL_{k.upper()}={shlex.quote(match[0][k])}")


if __name__ == "__main__":
    main()
