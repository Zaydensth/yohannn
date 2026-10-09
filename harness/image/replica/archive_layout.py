#!/usr/bin/env python3
"""archive_layout.py - the tasks/ layout adaptor of a staged archive (python3 >= 3.8, stdlib only).

hf_archive_tournament.py fetch keeps every task under tasks/<round>_<family>_<task8>/ (e.g. R1_flux_0123abcd,
Boss_z-image_89abcdef), while verify_stage.py, stage.sh, cell_run.sh (TASK_DIR = data/<tourn>/<task_id>) and
score_latent.sh (task id = the dir's basename) read <archive>/<task_id>/ - a flat "uuid" layout (each task dir named by
its full task id directly under the archive root). This tool maps every tasks/ dir to its FULL task id and adapts the
layout without moving, renaming or copying a byte:

  detect --tourn DIR             LAYOUT=uuid | tasks | linked | empty  (linked = tasks/ plus the uuid links of `link`)
  map    --tourn DIR             task_id <TAB> tasks/<dir> <TAB> model_type <TAB> round, one line per task; exit 1 on any
                                 ambiguity (named on stderr): the task8 of the dir name must match exactly ONE
                                 api/<task_id>.json whose task_id field equals its file name and whose model_type equals
                                 the dir's family; manifest.json (when present) must agree on round and model_type
  link   --tourn DIR             DIR/<task_id> -> tasks/<dir> as RELATIVE symlinks (the archive moves with them; a docker
                                 bind mount of DIR/<task_id>/test_data.zip resolves through a directory symlink).
                                 Idempotent: an existing correct link is kept; a name that exists as anything else, or a
                                 link that points elsewhere, is refused (exit 1) - never overwritten. Run it on the BOX copy
                                 (stage.sh does, after the archive transfer), never on the workstation source: the source
                                 archive is also what ../archive/hf_archive_tournament.py uploads, and links there would add
                                 a second copy of every task under its uuid.
  view   --tourn DIR --out ROOT  ROOT/<basename DIR>/ = symlinks to every top-level entry of DIR plus <task_id> ->
                                 DIR/tasks/<dir> (absolute): a read-only VIEW for the workstation-side verify_stage.py run
                                 of stage.sh. DIR itself is not touched. ROOT/<basename DIR> must not exist yet.
  check  --tourn DIR             PASS/FAIL lines: every task of `map` has its link and every link resolves to its OWN
                                 tasks/ dir; exit 1 on any FAIL (verify_stage.py runs the same check).
"""
import argparse
import json
import os
import re
import sys

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
TASKS_DIR_RE = re.compile(r"^(?P<round>[A-Za-z][A-Za-z0-9]*)_(?P<family>[a-z0-9][a-z0-9-]*)_(?P<t8>[0-9a-f]{8})$")


class LayoutError(Exception):
    pass


def _uuid_entries(root):
    return sorted(n for n in os.listdir(root) if UUID_RE.match(n))


def _tasks_dirs(root):
    t = os.path.join(root, "tasks")
    if not os.path.isdir(t):
        return []
    return sorted(n for n in os.listdir(t) if os.path.isdir(os.path.join(t, n)) and not n.startswith("."))


def detect(root):
    if not os.path.isdir(root):
        raise LayoutError(f"{root} is not a directory")
    uu, td = _uuid_entries(root), _tasks_dirs(root)
    if td:
        return "linked" if uu else "tasks"
    return "uuid" if uu else "empty"


def mapping(root):
    """[(task_id, 'tasks/<dir>', model_type, round)] for every tasks/ dir; LayoutError lists every problem."""
    tdirs = _tasks_dirs(root)
    if not tdirs:
        raise LayoutError(f"{root}: no tasks/<round>_<family>_<task8>/ dirs")
    api = os.path.join(root, "api")
    by8 = {}
    for fn in sorted(os.listdir(api)) if os.path.isdir(api) else []:
        stem = fn[:-5] if fn.endswith(".json") else ""
        if UUID_RE.match(stem):
            by8.setdefault(stem[:8], []).append(stem)
    man = {}
    mp = os.path.join(root, "manifest.json")
    if os.path.isfile(mp):
        try:
            for t in json.load(open(mp)).get("tasks", []):
                # hf_archive_tournament.py writes "model_type"; older archivers wrote "model_type_api"
                man[t.get("task_id", "")] = (t.get("round", ""), t.get("model_type_api") or t.get("model_type") or "")
        except (OSError, ValueError, AttributeError) as e:
            raise LayoutError(f"{mp}: unreadable ({type(e).__name__}: {e})")
    out, bad = [], []
    for d in tdirs:
        m = TASKS_DIR_RE.match(d)
        if not m:
            bad.append(f"tasks/{d}: the name is not <round>_<family>_<task8>")
            continue
        ids = by8.get(m.group("t8"), [])
        if len(ids) != 1:
            bad.append(f"tasks/{d}: {len(ids)} api/<task_id>.json file(s) start with {m.group('t8')} (need exactly one)")
            continue
        tid = ids[0]
        try:
            j = json.load(open(os.path.join(api, f"{tid}.json")))
        except (OSError, ValueError) as e:
            bad.append(f"tasks/{d}: api/{tid}.json unreadable ({type(e).__name__})")
            continue
        if j.get("task_id") != tid:
            bad.append(f"tasks/{d}: api/{tid}.json carries task_id {j.get('task_id')!r}")
            continue
        if j.get("model_type") != m.group("family"):
            bad.append(f"tasks/{d}: api model_type {j.get('model_type')!r} != the dir's family {m.group('family')!r}")
            continue
        if man:
            rnd, mt = man.get(tid, ("", ""))
            if (rnd, mt) != (m.group("round"), m.group("family")):
                bad.append(f"tasks/{d}: manifest.json says round={rnd or '-'} model_type={mt or '-'} for {tid}")
                continue
        out.append((tid, f"tasks/{d}", m.group("family"), m.group("round")))
    if len({t for t, *_ in out}) != len(out):
        bad.append("two tasks/ dirs map to the same task id")
    if bad:
        raise LayoutError("; ".join(bad))
    return out


def check(root):
    """[(ok, task_id, detail)] - the link of every mapped task (verify_stage.py prints these)."""
    res = []
    for tid, rel, fam, rnd in mapping(root):
        p = os.path.join(root, tid)
        want = os.path.realpath(os.path.join(root, rel))
        if not os.path.lexists(p):
            res.append((False, tid, f"{rnd} {fam}: no {tid} link -> {rel} (run archive_layout.py link)"))
        elif os.path.realpath(p) != want:
            res.append((False, tid, f"{rnd} {fam}: {tid} resolves to {os.path.realpath(p)}, not {want}"))
        else:
            res.append((True, tid, f"{rnd} {fam}: {tid} -> {rel}"))
    return res


def cmd_detect(a):
    print(f"LAYOUT={detect(a.tourn)}")


def cmd_map(a):
    for row in mapping(a.tourn):
        print("\t".join(row))


def cmd_link(a):
    made = kept = 0
    bad = []
    for tid, rel, fam, rnd in mapping(a.tourn):
        p = os.path.join(a.tourn, tid)
        if os.path.islink(p):
            if os.readlink(p) == rel:
                kept += 1
                continue
            bad.append(f"{tid} is a symlink to {os.readlink(p)!r}, not {rel!r} - refusing to overwrite it")
            continue
        if os.path.lexists(p):
            bad.append(f"{tid} exists and is not a symlink - refusing to touch it")
            continue
        os.symlink(rel, p)
        made += 1
    print(f"[archive_layout] {a.tourn}: {made} link(s) made, {kept} already in place"
          + (f", {len(bad)} REFUSED" if bad else ""))
    if bad:
        for b in bad:
            print(f"[archive_layout] REFUSE: {b}", file=sys.stderr)
        sys.exit(1)


def cmd_view(a):
    src = os.path.abspath(a.tourn)
    rows = mapping(src)
    dst = os.path.join(os.path.abspath(a.out), os.path.basename(src.rstrip("/")))
    if os.path.lexists(dst):
        raise LayoutError(f"{dst} already exists - give an empty --out")
    os.makedirs(dst)
    ids = {t for t, *_ in rows}
    for n in sorted(os.listdir(src)):
        if n in ids:
            continue                       # an already-linked copy: the view makes its own absolute links below
        os.symlink(os.path.join(src, n), os.path.join(dst, n))
    for tid, rel, _f, _r in rows:
        os.symlink(os.path.join(src, rel), os.path.join(dst, tid))
    print(dst)


def cmd_check(a):
    res = check(a.tourn)
    for ok, tid, detail in res:
        print(f"[archive_layout] {'PASS' if ok else 'FAIL'} {tid[:8]} {detail}")
    sys.exit(0 if all(ok for ok, *_ in res) else 1)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("detect", cmd_detect), ("map", cmd_map), ("link", cmd_link), ("check", cmd_check)):
        s = sub.add_parser(name)
        s.add_argument("--tourn", required=True)
        s.set_defaults(fn=fn)
    s = sub.add_parser("view")
    s.add_argument("--tourn", required=True)
    s.add_argument("--out", required=True)
    s.set_defaults(fn=cmd_view)
    a = ap.parse_args()
    try:
        a.fn(a)
    except LayoutError as e:
        print(f"[archive_layout] REFUSE: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
