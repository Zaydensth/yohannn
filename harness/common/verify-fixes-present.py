#!/usr/bin/env python3
"""verify-fixes-present.py - TEMPLATE: does the ref you are about to submit really contain every fix you think it has?

Why: when the submission tree is assembled by SELECTIVE merges (`git checkout <sha> -- <dir>`, cherry-picks, a ship
branch rebuilt from parts) - a good way to keep dev tooling out of the tree the validator clones - one merge that drops
a fix is invisible: no conflict, no warning, and the tests stay green because the tests travelled with it. This closes
that gap: for every consequential fix, look for a MARKER that only exists when the fix is present - not "the file
exists", but the line that makes the fix work.

Markers live in a TSV you maintain next to your fixes (one row per fix, tab-separated):
    name <TAB> path/in/repo <TAB> literal marker string
Add a row in the same commit as the fix. See fixes.example.tsv.

Usage (reads the ref through `git show`, so it can check `main` without checking it out):
    python3 verify-fixes-present.py --markers fixes.tsv [REF]        REF defaults to main
Exit 0 = every marker present, 1 = something missing (do not submit that ref), 2 = the ref does not resolve or the
marker file is empty - an empty list proves nothing, so it is refused rather than reported as "all present".
"""
import argparse
import subprocess
import sys


def blob(ref, path):
    try:
        return subprocess.run(["git", "show", f"{ref}:{path}"], capture_output=True, text=True, check=True).stdout
    except subprocess.CalledProcessError:
        return None


def load(path):
    rows = []
    for n, line in enumerate(open(path, encoding="utf-8"), 1):
        if not line.strip() or line.startswith("#"):
            continue
        f = line.rstrip("\n").split("\t")
        if len(f) != 3 or not all(x.strip() for x in f):
            sys.exit(f"{path} L{n}: want 'name<TAB>path<TAB>marker', got {line.strip()[:80]!r}")
        rows.append(tuple(f))
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--markers", required=True)
    ap.add_argument("ref", nargs="?", default="main")
    a = ap.parse_args()
    if subprocess.run(["git", "rev-parse", "--verify", "-q", a.ref + "^{tree}"], capture_output=True).returncode != 0:
        print(f"  ABORT: '{a.ref}' does not resolve to a tree - every check below would report 'missing' for the wrong reason")
        return 2
    rows = load(a.markers)
    if not rows:
        print(f"  ABORT: {a.markers} lists no marker - an empty list cannot fail, so it proves nothing")
        return 2
    print(f"===== fixes present @ {a.ref} =====")
    missing = []
    for name, path, marker in rows:
        src = blob(a.ref, path)
        if src is None:
            print(f"  FILE MISSING   {name:<36} ({path})")
            missing.append(name)
        elif marker in src:
            print(f"  present        {name}")
        else:
            print(f"  MARKER MISSING {name:<36} ({path})")
            missing.append(name)
    print()
    if missing:
        print(f"  FAIL: {len(missing)} of {len(rows)} fixes are NOT in {a.ref}: {', '.join(missing)}")
        print("  A selective merge probably dropped them. Do not submit this ref.")
        return 1
    print(f"  {len(rows)} of {len(rows)} fixes present in {a.ref}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
