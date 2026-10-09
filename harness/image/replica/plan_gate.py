#!/usr/bin/env python3
"""plan_gate.py - bind a cells (queue) file to a LOCKED plan, and check that binding before any GPU work.

TEMPLATE / method, not a decision rule: it encodes only "no experiment without a locked, strategist-approved plan".
Python >= 3.8, stdlib only. Subcommands:

  lockcheck --plan P --slug S --fable STAMP [--ledger L] [--allow-bypass]
        the plan is ready to bind: prints PLAN_SHA256= GO_LINE= LOCKCHECK=PASS (shell-quoted), else LOCKCHECK=REFUSE and
        every reason (exit 1). STAMP must be the stamp of the binding line (below).
  rowshash --cells C
        sha256 of the cells file's rows (every line after line 1; the `enabled` column, when present, is left out so
        that switching a cell off - a stop, never a new experiment - does not need a new lock)
  bind --cells C --plan-sha256 X --fable STAMP --slug S --rows-sha256 R [--pin-sha SHA] [--rebind] [--dry-run]
        write line 1 of the cells file: "# lock plan_sha256=.. fable=.. slug=.. rows_sha256=.. [pin_sha=..]" (atomic).
        A binding that already carries OTHER values is refused unless --rebind (a new lock = a new cells revision).
  gate --plan P --cells C --slug S [--ledger L] [--pin-dir DIR] [--allow-bypass]
        what queue.sh runs before every real run: GATE=PASS (exit 0) or GATE=REFUSE + reasons (exit 1).
  binding --cells C
        BIND_PIN_SHA= BIND_STATE=bound|unbound|malformed (for stage.sh: the commit to ship)

Plan rules (all on the plan text; "frozen" = everything above the first line starting with '## RESULTS'):
  * every line that carries 'FABLE-PLAN:' (also behind '>' '-' '*' '+' '1.' '1)' prefixes or a backtick) must read
    'FABLE-PLAN: FABLE <yyyymmdd-hhmm> PLAN=<slug> POINT=<lock|rescope|gonogo|postmortem>
     VERDICT=<GO|NO-GO|REVISI|UNAVAILABLE|BYPASS-USER> ...' - one that cannot be classified refuses (fail-closed);
  * the BINDING line: above '## RESULTS', PLAN=<slug>, POINT=lock, VERDICT=GO (VERDICT=BYPASS-USER too, only with
    --allow-bypass: the user's own direct order); the newest stamp binds;
  * any line of PLAN=<slug> with VERDICT=NO-GO or REVISI stamped AT or after the binding stamp - in the plan (RESULTS
    included) or in the ledger - ends the lock (an equal stamp cannot be ordered: it refuses);
  * exactly one line starting 'Status: **LOCKED**' above '## RESULTS'; no 'DRAFT' above '## RESULTS';
  * with --ledger: the ledger holds the line 'FABLE <stamp> PLAN=<slug> POINT=lock VERDICT=GO' (or BYPASS-USER).
Cells rules: line 1 binds plan_sha256 (sha256 of the frozen bytes), fable (= the binding stamp), slug, rows_sha256 (=
rowshash now) and optionally pin_sha (40 hex; with --pin-dir the checkout's HEAD must equal it, tree clean).
"""
import argparse
import hashlib
import os
import re
import shlex
import subprocess
import sys

VERDICTS = ("GO", "NO-GO", "REVISI", "UNAVAILABLE", "BYPASS-USER")
POINTS = ("lock", "rescope", "gonogo", "postmortem")
ANY_RE = re.compile(r"^\s*(?:(?:[>*+-]|\d+[.)])\s*)*`?FABLE-PLAN:")
BODY_RE = re.compile(r"^\s*(?:(?:[>*+-]|\d+[.)])\s*)*`?FABLE-PLAN:\s*FABLE (\d{8}-\d{4}) PLAN=(\S+) POINT=(\S+) "
                     r"VERDICT=([A-Z-]+)(?=\s|$)")
LEDGER_RE = re.compile(r"^FABLE (\d{8}-\d{4}) PLAN=(\S+) POINT=(\S+) VERDICT=([A-Z-]+)(?=\s|$)")
STATUS_RE = re.compile(r"^Status: \*\*LOCKED\*\*")
STAMP_RE = re.compile(r"\d{8}-\d{4}")
LATER = ("NO-GO", "REVISI")


def frozen_bytes(raw):
    m = re.search(rb"(?m)^## RESULTS", raw)
    return raw[:m.start()] if m else raw


def plan_sha256(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(frozen_bytes(fh.read())).hexdigest()


def parse_plan(text):
    lines = text.splitlines()
    n_frozen = len(frozen_bytes(text.encode()).decode(errors="replace").splitlines())
    parsed, bad = [], []
    for i, line in enumerate(lines):
        if not ANY_RE.match(line):
            continue
        m = BODY_RE.match(line)
        if not m or m.group(3) not in POINTS or m.group(4) not in VERDICTS:
            bad.append(f"L{i + 1}: {line.strip()[:100]}")
            continue
        parsed.append({"i": i, "line": line.strip(), "stamp": m.group(1), "plan": m.group(2), "point": m.group(3),
                       "verdict": m.group(4), "frozen": i < n_frozen})
    return lines, n_frozen, parsed, bad


def lock_facts(text, slug, allow_bypass=False):
    """(reasons, binding_line, stamp)."""
    lines, n_frozen, parsed, bad = parse_plan(text)
    reasons = []
    if bad:
        reasons.append("malformed FABLE-PLAN line(s) - each must read 'FABLE-PLAN: FABLE <yyyymmdd-hhmm> PLAN=<slug> "
                       "POINT=<lock|rescope|gonogo|postmortem> VERDICT=<GO|NO-GO|REVISI|UNAVAILABLE|BYPASS-USER>': "
                       + "; ".join(bad))
    ok_verdicts = ("GO", "BYPASS-USER") if allow_bypass else ("GO",)
    binds = [p for p in parsed if p["frozen"] and p["plan"] == slug and p["point"] == "lock" and p["verdict"] in ok_verdicts]
    line, stamp = "", ""
    if not binds:
        reasons.append(f"no binding line: want 'FABLE-PLAN: FABLE <stamp> PLAN={slug} POINT=lock VERDICT=GO' above "
                       "'## RESULTS' (no experiment without a locked plan)"
                       + ("" if allow_bypass else "; a BYPASS-USER line binds only with --allow-bypass"))
    else:
        b = max(binds, key=lambda p: (p["stamp"], p["i"]))
        line, stamp = b["line"], b["stamp"]
        later = [p for p in parsed if p is not b and p["plan"] == slug and p["verdict"] in LATER and p["stamp"] >= stamp]
        if later:
            reasons.append(f"a later word on PLAN={slug} (NO-GO/REVISI stamped at or after the binding {stamp}) ends the "
                           "lock: " + "; ".join(f"L{p['i'] + 1} {p['stamp']} POINT={p['point']} VERDICT={p['verdict']}"
                                                for p in later))
    status = [i + 1 for i, l in enumerate(lines[:n_frozen]) if STATUS_RE.match(l)]
    if len(status) != 1:
        reasons.append(f"the frozen text has {len(status)} line(s) starting 'Status: **LOCKED**' (want exactly one)"
                       + (f" at L{status}" if status else ""))
    drafts = [f"L{i + 1}" for i, l in enumerate(lines[:n_frozen]) if "DRAFT" in l]
    if drafts:
        reasons.append(f"'DRAFT' in the frozen text ({', '.join(drafts[:5])}) - a locked plan carries none")
    return reasons, line, stamp


def ledger_reasons(ledger, slug, stamp, allow_bypass=False, need_go=True):
    try:
        lines = open(ledger, errors="replace").read().splitlines()
    except OSError as e:
        return [f"ledger {ledger} unreadable ({type(e).__name__})"]
    reasons, go = [], False
    ok_verdicts = ("GO", "BYPASS-USER") if allow_bypass else ("GO",)
    for i, line in enumerate(lines, 1):
        m = LEDGER_RE.match(line)
        if not m or m.group(2) != slug:
            continue
        if m.group(1) == stamp and m.group(3) == "lock" and m.group(4) in ok_verdicts:
            go = True
        if m.group(4) in LATER and m.group(1) >= stamp:
            reasons.append(f"ledger {ledger} L{i}: {m.group(1)} POINT={m.group(3)} VERDICT={m.group(4)} is a later word "
                           f"on PLAN={slug} - the lock bound at {stamp} no longer stands")
    if need_go and not go:
        reasons.append(f"ledger {ledger} has no line 'FABLE {stamp} PLAN={slug} POINT=lock VERDICT=GO' (the verdict "
                       "goes to the ledger AND into the plan)")
    return reasons


def rows_sha256(cells):
    with open(cells, encoding="utf-8") as fh:
        lines = fh.read().split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    body = lines[1:]
    if not body:
        raise ValueError(f"{cells} has no header/rows after line 1")
    header = body[0].split("\t")
    drop = header.index("enabled") if "enabled" in header else None
    out = []
    for line in body:
        f = line.split("\t")
        if drop is not None and not line.startswith("#") and len(f) == len(header):
            f = f[:drop] + f[drop + 1:]
        out.append("\t".join(x.rstrip() for x in f))
    return hashlib.sha256("\n".join(out).encode()).hexdigest()


def read_binding(cells):
    try:
        with open(cells, encoding="utf-8") as fh:
            head = fh.readline().rstrip("\n")
    except OSError:
        return None, {}
    toks = dict(re.findall(r"\b(plan_sha256|fable|slug|rows_sha256|pin_sha)=(\S+)", head))
    return head, toks


def git_head(d):
    try:
        sha = subprocess.run(["git", "-C", d, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", d, "status", "--porcelain"], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "", ""
    return sha, dirty


def kv(k, v):
    print(f"{k}={shlex.quote(str(v))}")


def cmd_lockcheck(a):
    text = open(a.plan, errors="replace").read()
    reasons, line, stamp = lock_facts(text, a.slug, a.allow_bypass)
    if not STAMP_RE.fullmatch(a.fable):
        reasons.append(f"--fable {a.fable!r} is not a yyyymmdd-hhmm stamp")
    elif stamp and a.fable != stamp:
        reasons.append(f"--fable {a.fable}: the plan's binding line is stamped {stamp}")
    if a.ledger and STAMP_RE.fullmatch(a.fable):
        reasons += ledger_reasons(a.ledger, a.slug, a.fable, a.allow_bypass)
    if reasons:
        print(f"LOCKCHECK=REFUSE plan={a.plan}")
        for r in reasons:
            print(f"  - {r}")
        sys.exit(1)
    kv("PLAN_SHA256", plan_sha256(a.plan))
    kv("GO_LINE", line[:300])
    kv("LOCKCHECK", "PASS")


def cmd_rowshash(a):
    print(rows_sha256(a.cells))


def cmd_bind(a):
    for name, rx in (("--plan-sha256", r"[0-9a-f]{64}"), ("--rows-sha256", r"[0-9a-f]{64}"), ("--fable", r"\d{8}-\d{4}")):
        val = getattr(a, name[2:].replace("-", "_"))
        if not re.fullmatch(rx, val):
            sys.exit(f"plan_gate.py bind: {name} {val!r} is malformed")
    if a.pin_sha and not re.fullmatch(r"[0-9a-f]{40}", a.pin_sha):
        sys.exit(f"plan_gate.py bind: --pin-sha {a.pin_sha!r} is not 40 hex (a short SHA is never a pin)")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", a.slug):
        sys.exit(f"plan_gate.py bind: --slug {a.slug!r} is malformed")
    now = rows_sha256(a.cells)
    if now != a.rows_sha256:
        sys.exit(f"plan_gate.py bind: the rows changed since they were hashed ({a.rows_sha256[:12]}.. -> {now[:12]}..)")
    want = f"# lock plan_sha256={a.plan_sha256} fable={a.fable} slug={a.slug} rows_sha256={a.rows_sha256}" \
           + (f" pin_sha={a.pin_sha}" if a.pin_sha else "")
    with open(a.cells, encoding="utf-8") as fh:
        lines = fh.read().split("\n")
    head, toks = read_binding(a.cells)
    if head == want:
        kv("BIND", "already bound to these values - nothing to do")
        return
    if toks and not a.rebind:
        sys.exit(f"plan_gate.py bind: {a.cells} is already bound to other values ({head[:120]}) - a new lock is a new "
                 "cells revision: --rebind")
    if a.dry_run:
        kv("BIND", f"DRY-RUN: would write line 1: {want}")
        return
    lines[0] = want
    tmp = a.cells + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    os.replace(tmp, a.cells)
    kv("BIND", f"line 1 written: {want}")


def cmd_gate(a):
    reasons, notes = [], []
    if not os.path.isfile(a.plan):
        reasons.append(f"no plan file {a.plan}")
        text, line, stamp = "", "", ""
    else:
        text = open(a.plan, errors="replace").read()
        r, line, stamp = lock_facts(text, a.slug, a.allow_bypass)
        reasons += r
    if a.ledger:
        if os.path.isfile(a.ledger):
            if stamp:
                reasons += ledger_reasons(a.ledger, a.slug, stamp, a.allow_bypass)
        else:
            reasons.append(f"no ledger copy at {a.ledger} (stage.sh ships it beside the plan)")
    head, toks = read_binding(a.cells)
    if head is None:
        reasons.append(f"cannot read {a.cells}")
    elif not all(k in toks for k in ("plan_sha256", "fable", "slug", "rows_sha256")):
        reasons.append(f"{a.cells} is not bound to a locked plan (line 1 lacks plan_sha256= fable= slug= rows_sha256=: "
                       "run lock_cells.sh at LOCK time)")
    else:
        if os.path.isfile(a.plan) and toks["plan_sha256"] != plan_sha256(a.plan):
            reasons.append("the plan text above '## RESULTS' changed after the lock (plan_sha256 differs) - a new lock")
        if stamp and toks["fable"] != stamp:
            reasons.append(f"the cells file names fable={toks['fable']}, the plan's binding line is {stamp}")
        if toks["slug"] != a.slug:
            reasons.append(f"the cells file names slug={toks['slug']}, the gate was asked for {a.slug}")
        try:
            now = rows_sha256(a.cells)
        except (OSError, ValueError) as e:
            now = f"unreadable ({e})"
        if toks["rows_sha256"] != now:
            reasons.append(f"the cells rows changed after the lock (rows_sha256 {toks['rows_sha256'][:12]}.. -> "
                           f"{now[:12]}..) - only `enabled` may change; anything else is a new lock")
        pin = toks.get("pin_sha", "")
        if pin and not re.fullmatch(r"[0-9a-f]{40}", pin):
            reasons.append(f"pin_sha={pin!r} is not 40 hex")
        elif pin and a.pin_dir:
            sha, dirty = git_head(a.pin_dir)
            if not sha:
                reasons.append(f"{a.pin_dir} is not a git checkout (stage.sh ships the pinned commit there)")
            elif sha != pin:
                reasons.append(f"{a.pin_dir} HEAD is {sha[:12]}, the lock pins {pin[:12]} - re-stage the pinned commit")
            elif dirty:
                reasons.append(f"{a.pin_dir} is at the pin but its tree is dirty")
            else:
                notes.append(f"{a.pin_dir} HEAD = pin_sha {pin[:12]}, clean")
        elif not pin:
            notes.append("no pin_sha in the binding: the trainer commit is not bound by the lock")
    if reasons:
        print(f"GATE=REFUSE plan={a.plan} cells={a.cells}")
        for r in reasons:
            print(f"  - {r}")
        for n in notes:
            print(f"  note: {n}")
        sys.exit(1)
    print(f"GATE=PASS plan={a.plan} slug={a.slug} cells={a.cells} go={line[:200]}")
    for n in notes:
        print(f"  note: {n}")


def cmd_binding(a):
    head, toks = read_binding(a.cells)
    if head is None or not toks:
        kv("BIND_STATE", "unbound"); kv("BIND_PIN_SHA", ""); return
    pin = toks.get("pin_sha", "")
    if pin and not re.fullmatch(r"[0-9a-f]{40}", pin):
        kv("BIND_STATE", "malformed"); kv("BIND_PIN_SHA", pin); sys.exit(1)
    kv("BIND_STATE", "bound"); kv("BIND_PIN_SHA", pin)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("lockcheck"); s.set_defaults(fn=cmd_lockcheck)
    for x in ("--plan", "--slug", "--fable"):
        s.add_argument(x, required=True)
    s.add_argument("--ledger", default=""); s.add_argument("--allow-bypass", action="store_true")
    s = sub.add_parser("rowshash"); s.set_defaults(fn=cmd_rowshash); s.add_argument("--cells", required=True)
    s = sub.add_parser("bind"); s.set_defaults(fn=cmd_bind)
    for x in ("--cells", "--plan-sha256", "--fable", "--slug", "--rows-sha256"):
        s.add_argument(x, required=True)
    s.add_argument("--pin-sha", default=""); s.add_argument("--rebind", action="store_true")
    s.add_argument("--dry-run", action="store_true")
    s = sub.add_parser("gate"); s.set_defaults(fn=cmd_gate)
    for x in ("--plan", "--cells", "--slug"):
        s.add_argument(x, required=True)
    s.add_argument("--ledger", default=""); s.add_argument("--pin-dir", default="")
    s.add_argument("--allow-bypass", action="store_true")
    s = sub.add_parser("binding"); s.set_defaults(fn=cmd_binding); s.add_argument("--cells", required=True)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
