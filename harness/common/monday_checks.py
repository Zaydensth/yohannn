#!/usr/bin/env python3
"""
monday_checks.py - pre-registration checks for an SN56 tournament entry (image or text). Repo-agnostic: every repo,
pin, image digest and file is an argument; nothing here knows your miner.

Run it before you register (image tournaments start Monday 13:00 UTC: validator/tournament/constants.py:21-22 at
G.O.D b49ba8644a0d). CPU + network only, anonymous, stdlib + `git` + `curl` subprocesses. No large downloads: docker
manifests, API JSON, raw text files and 1-byte range GETs only. Sequential and time-boxed. It never reads, prints or
needs a credential; `git fetch` / `ls-remote` against YOUR origin use the machine's existing git credential helper.

Checks (each row: PASS / FAIL / REVIEW / INFO / UNVERIFIABLE, and whether it blocks registration):
  1 upstream diff      gradients-ai/G.O.D <--upstream-base>..main on the paths that define the miner contract
                       (trainer/, validator/{evaluation,tasks,tournament}/, core/{constants,models}/, ops/docker/,
                       LICENSE.md, NOTICE): none -> INFO; any -> REVIEW (a human reads them; contract-shaped paths are
                       tagged: trainer CLI, mounts, dockerfile path, timeout, registration gate, downloader/cache).
  2 image digests      Docker Hub digests of the images production runs (--image repo:tag[=sha256:<digest or prefix>]):
                       matches the digest you recorded -> PASS; moved -> REVIEW for the downloader (cache layout) and
                       the uploader (it publishes your weights), INFO/REVIEW for the evaluator (REVIEW when check 1 shows
                       validator/evaluation/ changed). Without a reference the current digest is printed: record it.
  3 upstream constants --flag NAME[=EXPECTED][@path]: the value of an upstream constant on main (INFO; REVIEW when it
                       differs from EXPECTED). Track the constants your strategy depends on.
  4 LICENSE / NOTICE   <remote>/<branch> of --repo vs upstream main, normalised exactly like the validator
                       (validator/tournament/github_validation.py:131-132,155-156: rstrip every line). The validator
                       clones your DEFAULT branch for this check (github_validation.py:57-75, called at
                       tournament_manager.py:1111) - not the submitted commit. Mismatch -> FAIL (the entry is excluded).
  5 build assets       every huggingface.co/<repo>/resolve/<rev>/<file>.safetensors URL + sha256 pin found in the ship
                       SHA's dockerfiles (--dockerfile, default per --track from trainer/constants.py:1-12): hop 1
                       must be a 302 whose x-linked-etag equals the pin and the final ranged GET a 206; 401/403/404 or
                       etag != pin -> FAIL (a no-cache build would fail or fetch other bytes).
  6 PyPI drift         --freeze FILE (pip freeze of your BUILT trainer image) + --cutoff ISO (when it was built):
                       unpinned packages with a PyPI release after the cutoff -> INFO list (production rebuilds with no
                       cache for every task, so an unpinned dependency can move under you).
  7 ship on branch     `git ls-remote <remote> refs/heads/<branch>` == --ship-sha -> PASS, else INFO (before the push).
  8 commands           the push and the miner-endpoint check, PRINTED only - pushing and registering are yours to do.

Exit 0 iff no row is FAIL. REVIEW / UNVERIFIABLE never flip the exit code: a human decides those.
Usage:
  python3 monday_checks.py --repo <miner repo> --ship-sha <40-hex> [--track image|text] [--branch main]
      [--remote origin] [--upstream-base <40-hex G.O.D commit you last reviewed>]
      [--image gradientsio/image-evaluator:basilica=sha256:<...>] [--image gradientsio/trainer-downloader:latest=...]
      [--image gradientsio/trainer-uploader:latest=...] [--flag NAME=VALUE@path/in/upstream.py]
      [--dockerfile path/in/repo ...] [--freeze FILE --cutoff 2026-01-01T00:00:00Z] [--out DIR]
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import datetime, timezone

UPSTREAM_REPO = "gradients-ai/G.O.D"
SCOPE_PREFIXES = ("trainer/", "validator/evaluation/", "validator/tasks/", "validator/tournament/", "core/constants/",
                  "core/models/", "ops/docker/")
SCOPE_FILES = ("LICENSE.md", "NOTICE")
DOCKERFILES = {  # G.O.D trainer/constants.py:1-12 (b49ba8644a0d): the paths production looks for, new then legacy
    "image": ("ops/docker/standalone-image-trainer.dockerfile", "ops/docker/standalone-image-toolkit-trainer.dockerfile",
              "dockerfiles/standalone-image-trainer.dockerfile", "dockerfiles/standalone-image-toolkit-trainer.dockerfile"),
    "text": ("ops/docker/standalone-text-trainer.dockerfile", "dockerfiles/standalone-text-trainer.dockerfile"),
}
PYPI_TIMEOUT_S = 10
PYPI_BUDGET_S = 240


class Proc:
    def __init__(self, returncode, stdout=b"", stderr=b""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def run_git(repo, args, timeout=30):
    try:
        p = subprocess.run(["git", "-C", repo] + list(args), capture_output=True, timeout=timeout)
        return Proc(p.returncode, p.stdout, p.stderr)
    except subprocess.TimeoutExpired:
        return Proc(124, b"", b"timed out")
    except FileNotFoundError as e:
        return Proc(127, b"", str(e).encode())


def git_show(repo, rev, path):
    r = run_git(repo, ["show", f"{rev}:{path}"], timeout=20)
    return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else None


def git_rev_parse(repo, spec):
    r = run_git(repo, ["rev-parse", spec], timeout=15)
    return r.stdout.decode().strip() if r.returncode == 0 else None


def git_ls_tree(repo, rev):
    r = run_git(repo, ["ls-tree", "-r", "--name-only", rev])
    return [l for l in r.stdout.decode("utf-8", "replace").splitlines() if l.strip()] if r.returncode == 0 else []


def curl(url, headers=None, extra=None, timeout=20, follow=False):
    """One curl call -> {code, headers (lowercased), body, error, timed_out}. Never raises; an HTTP status is not an
    error (no -f)."""
    with tempfile.TemporaryDirectory(prefix="monday_curl_") as td:
        hp, bp = os.path.join(td, "h"), os.path.join(td, "b")
        cmd = ["curl", "-s", "-D", hp, "-o", bp, "-w", "%{http_code}"] + (["-L"] if follow else [])
        for h in headers or []:
            cmd += ["-H", h]
        cmd += list(extra or []) + ["--max-time", str(timeout), url]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 15)
        except subprocess.TimeoutExpired:
            return {"code": None, "headers": {}, "body": b"", "error": "local timeout", "timed_out": True}
        if p.returncode != 0:
            return {"code": None, "headers": {}, "body": b"", "error": f"curl exit {p.returncode}: {p.stderr.strip()[:200]}",
                    "timed_out": False}
        hdrs = {}
        try:
            for line in open(hp, errors="replace"):
                line = line.strip("\r\n")
                if line and not line.startswith("HTTP/") and ":" in line:
                    k, _, v = line.partition(":")
                    hdrs[k.strip().lower()] = v.strip()
        except OSError:
            pass
        body = open(bp, "rb").read() if os.path.exists(bp) else b""
        code = int(p.stdout.strip()) if p.stdout.strip().isdigit() else None
        return {"code": code, "headers": hdrs, "body": body, "error": None, "timed_out": False}


def transient(r):
    return r["timed_out"] or (r["error"] is not None and r["code"] is None) or r["code"] in (429, 500, 502, 503, 504)


def normalize_license(text):
    """validator/tournament/github_validation.py:131-132 (and :155-156 for NOTICE), verbatim logic."""
    return "\n".join(line.rstrip() for line in text.splitlines())


def row(rid, check, result, evidence, blocks, related=None):
    return {"id": rid, "check": check, "result": result, "evidence": evidence, "blocks": blocks, "related": related}


def run_check(rid, name, fn, *a):
    t0 = time.monotonic()
    print(f"[{rid}] {name} ...", file=sys.stderr, flush=True)
    try:
        out = fn(*a)
        r = row(rid, name, *out) if len(out) == 4 else row(rid, name, *out, None)
    except Exception as e:  # a crashed check is reported, never dropped
        r = row(rid, name, "UNVERIFIABLE", f"check crashed: {type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}", "N/A")
    r["elapsed_s"] = round(time.monotonic() - t0, 2)
    print(f"[{rid}] -> {r['result']} ({r['elapsed_s']}s)", file=sys.stderr, flush=True)
    return r


# ------------------------------------------------------------------------------------------------ 1 upstream diff
def contract_tags(fn):
    low, tags = fn.lower(), []
    if re.search(r"(^|/)trainer/.*(cli|__main__|argpars)", low) or low.endswith("cli.py"):
        tags.append("trainer-cli")
    if "runtime.py" in low or "mount" in low:
        tags.append("mounts")
    if low.startswith("ops/docker/") or "dockerfile" in low:
        tags.append("dockerfile")
    if re.search(r"timeout|budget", low):
        tags.append("timeout")
    if re.search(r"github_validation|obfuscation|tournament_manager", low):
        tags.append("registration-gate")
    if re.search(r"downloader|[_/]cache", low):
        tags.append("downloader-cache")
    return tags


def check1_upstream(base):
    if not base:
        return ("UNVERIFIABLE", "no --upstream-base: pass the G.O.D commit you last reviewed, so its diff to main can "
                "be listed", "N/A", [])
    r = curl(f"https://api.github.com/repos/{UPSTREAM_REPO}/compare/{base}...main",
             headers=["Accept: application/vnd.github+json"], timeout=25)
    files, commits, source = None, [], "github-compare-api"
    if r["code"] == 200:
        d = json.loads(r["body"])
        if d.get("status") not in ("identical", "ahead"):
            return ("UNVERIFIABLE", f"compare status {d.get('status')!r}: is {base[:10]} an ancestor of main?", "N/A", [])
        files = [(f.get("status", "?"), f.get("filename", "?")) for f in d.get("files") or []]
        commits = [f"{(c.get('sha') or '')[:10]} {((c.get('commit') or {}).get('message') or '').splitlines()[0][:90]}"
                   for c in d.get("commits") or []]
    else:
        tmp = tempfile.mkdtemp(prefix="monday_god_")
        try:
            env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
            ok = subprocess.run(["git", "clone", "-q", "--filter=blob:none", "--no-checkout",
                                 f"https://github.com/{UPSTREAM_REPO}.git", tmp], capture_output=True, timeout=180,
                                env=env).returncode == 0
            if ok:
                subprocess.run(["git", "-C", tmp, "fetch", "-q", "--filter=blob:none", "origin", base],
                               capture_output=True, timeout=90, env=env)
                ns = subprocess.run(["git", "-C", tmp, "diff", "--name-status", f"{base}..origin/main", "--"]
                                    + list(SCOPE_PREFIXES) + list(SCOPE_FILES), capture_output=True, text=True, timeout=60)
                if ns.returncode == 0:
                    files = [(l.split("\t")[0], l.split("\t")[-1]) for l in ns.stdout.splitlines() if "\t" in l]
                    lg = subprocess.run(["git", "-C", tmp, "log", "--oneline", f"{base}..origin/main"],
                                        capture_output=True, text=True, timeout=60)
                    commits = lg.stdout.splitlines()
                    source = "git-clone-fallback"
        except subprocess.TimeoutExpired:
            pass
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        if files is None:
            return ("UNVERIFIABLE", f"GitHub compare answered {r['code']} and the clone fallback failed", "N/A", [])
    scoped = [(st, fn, contract_tags(fn)) for st, fn in files
              if fn in SCOPE_FILES or any(fn.startswith(p) for p in SCOPE_PREFIXES)]
    lines = [f"source={source} base={base[:10]} head=main: {len(scoped)} in-scope file(s) of {len(files)} changed"]
    lines += [f"  {st} {fn}" + (f"  [{','.join(t)}]" if t else "") for st, fn, t in scoped[:80]]
    lines += [f"commits ({len(commits)}):"] + [f"  {c}" for c in commits[:50]]
    if not scoped:
        return ("INFO", "\n".join(lines), "NO", [])
    return ("REVIEW", "\n".join(lines), "NEEDS REVIEW", [(st, fn) for st, fn, _ in scoped])


# ------------------------------------------------------------------------------------------------ 2 image digests
def docker_digest(repo, tag):
    t = curl(f"https://auth.docker.io/token?service=registry.docker.io&scope=repository:{repo}:pull")
    if t["code"] != 200:
        return None, f"token request answered {t['code']}"
    tok = json.loads(t["body"]).get("token")
    accept = ("application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.oci.image.index.v1+json, "
              "application/vnd.docker.distribution.manifest.v2+json, application/vnd.oci.image.manifest.v1+json")
    m = curl(f"https://registry-1.docker.io/v2/{repo}/manifests/{tag}", headers=[f"Authorization: Bearer {tok}",
                                                                                   f"Accept: {accept}"])
    if m["code"] != 200:
        return None, f"manifest request answered {m['code']}"
    d = m["headers"].get("docker-content-digest")
    return (d, None) if d else (None, "no Docker-Content-Digest header")


def check2_image(spec, upstream_files):
    name, _, ref = spec.partition("=")
    repo, _, tag = name.partition(":")
    tag = tag or "latest"
    digest, err = docker_digest(repo, tag)
    if err:
        return ("UNVERIFIABLE", f"{repo}:{tag}: {err}", "N/A")
    ev = f"{repo}:{tag} digest={digest} reference={ref or '-'}"
    if not ref:
        return ("INFO", ev + " -- no reference given: record this digest after you reviewed the image", "NO")
    if digest.lower().startswith(ref.lower()):
        return ("PASS", ev + " -- unchanged", "NO")
    role = "downloader" if "downloader" in repo else "uploader" if "uploader" in repo else \
        "evaluator" if "evaluator" in repo else "other"
    if role == "evaluator":
        changed = any(fn.startswith("validator/evaluation/") for _, fn in upstream_files or [])
        return (("REVIEW", ev + " -- moved AND validator/evaluation/ changed upstream (check 1): re-read the evaluator",
                 "NEEDS REVIEW") if changed else
                ("INFO", ev + " -- moved, no validator/evaluation/ change upstream: likely a routine rebuild", "NO"))
    if role in ("downloader", "uploader"):
        why = "the /cache layout your trainer reads" if role == "downloader" else "what it publishes and how"
        return ("REVIEW", ev + f" -- moved: re-check {why}", "NEEDS REVIEW")
    return ("INFO", ev + " -- moved", "NO")


# ------------------------------------------------------------------------------------------------ 3 upstream constants
def check3_flag(spec):
    m = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)(?:=([^@]*))?(?:@(.+))?", spec)
    if not m:
        return ("UNVERIFIABLE", f"--flag {spec!r} is not NAME[=EXPECTED][@path]", "N/A")
    name, expected, path = m.group(1), m.group(2), m.group(3)
    paths = [path] if path else []
    if not paths:
        t = curl(f"https://api.github.com/repos/{UPSTREAM_REPO}/git/trees/main?recursive=1",
                 headers=["Accept: application/vnd.github+json"], timeout=25)
        if t["code"] != 200:
            return ("UNVERIFIABLE", f"no @path and the tree listing answered {t['code']}", "N/A")
        paths = [x["path"] for x in json.loads(t["body"]).get("tree", []) if x.get("type") == "blob"
                 and "constant" in x["path"].lower() and x["path"].endswith(".py")][:20]
    for p in paths:
        r = curl(f"https://raw.githubusercontent.com/{UPSTREAM_REPO}/main/{p}")
        if r["code"] == 200:
            v = re.search(rf"^{re.escape(name)}\s*=\s*(.+?)\s*(#.*)?$", r["body"].decode("utf-8", "replace"), re.M)
            if v:
                val = v.group(1)
                if expected is not None and val != expected:
                    return ("REVIEW", f"{name} = {val} at {p} (upstream main) - you expected {expected}", "NEEDS REVIEW")
                return ("INFO", f"{name} = {val} at {p} (upstream main)", "NO")
    return ("UNVERIFIABLE", f"{name} not found in {len(paths)} file(s) searched", "N/A")


# ------------------------------------------------------------------------------------------------ 4 LICENSE / NOTICE
def check4_license(repo, ship, remote, branch):
    f = run_git(repo, ["fetch", remote], timeout=60)
    note = "" if f.returncode == 0 else f" (WARNING: git fetch {remote} failed)"
    ref = f"{remote}/{branch}"
    ours = {n: git_show(repo, ref, n) for n in ("LICENSE.md", "NOTICE")}
    if any(v is None for v in ours.values()):
        return ("UNVERIFIABLE", f"cannot read {ref}:LICENSE.md / NOTICE in {repo}{note} (other accepted names exist: "
                "docs/miner.md:256-268 - adapt this check if you use one)", "N/A")
    up = {n: curl(f"https://raw.githubusercontent.com/{UPSTREAM_REPO}/main/{n}") for n in ("LICENSE.md", "NOTICE")}
    if any(r["code"] != 200 for r in up.values()):
        return ("UNVERIFIABLE", "cannot fetch the upstream LICENSE.md / NOTICE" + note, "N/A")
    eq = {n: normalize_license(ours[n]) == normalize_license(up[n]["body"].decode("utf-8", "replace")) for n in ours}
    same_blobs = all(git_rev_parse(repo, f"{ship}:{n}") == git_rev_parse(repo, f"{ref}:{n}") for n in ours)
    ev = (f"{ref} vs upstream main, normalised like github_validation.py: LICENSE.md equal={eq['LICENSE.md']}, "
          f"NOTICE equal={eq['NOTICE']}; ship SHA blobs == {ref} blobs: {same_blobs}{note}. The validator reads your "
          "DEFAULT branch for this check. Never edit these files to make them match - restore them from upstream.")
    return ("PASS", ev, "NO") if all(eq.values()) else ("FAIL", ev, "YES")


# ------------------------------------------------------------------------------------------------ 5 build assets
def parse_assets(text):
    pins = {fn: h.lower() for h, fn in re.findall(r"([0-9a-fA-F]{64})\s{1,4}(\S+\.safetensors)", text)}
    out, seen = [], set()
    for url in re.findall(r"https://huggingface\.co/\S+?/resolve/[0-9a-fA-F]+/\S+?\.safetensors", text):
        name = url.rsplit("/", 1)[-1]
        if name not in seen:
            seen.add(name)
            out.append({"name": name, "url": url, "pin": pins.get(name)})
    return out


def verify_asset(url, pin, tries=3, delay_s=60):
    r1 = r2 = None
    for i in range(1, tries + 1):
        r1 = curl(url, extra=["-r", "0-0"])
        r2 = curl(url, extra=["-r", "0-0"], follow=True, timeout=25)
        if not (transient(r1) or transient(r2)):
            break
        if i < tries:
            time.sleep(delay_s)
    etag = r1["headers"].get("x-linked-etag", "").strip().strip('"').lower()
    s = f"hop1={r1['code'] or r1['error']} etag={etag or 'none'} final={r2['code'] or r2['error']} pin={pin}"
    if transient(r1) or transient(r2):
        return "UNVERIFIABLE", s + " -- still transient"
    if r1["code"] in (401, 403, 404) or r2["code"] in (401, 403, 404):
        return "FAIL", s + " -- not downloadable anonymously (a no-cache build would fail)"
    if r1["code"] == 302 and etag and etag == pin and r2["code"] == 206:
        return "PASS", s
    if r1["code"] == 302 and etag and etag != pin:
        return "FAIL", s + " -- etag != pinned sha256 (re-uploaded upstream)"
    return "UNVERIFIABLE", s + " -- combination not covered; a human looks"


def check5_assets(repo, ship, dockerfiles):
    assets = []
    for df in dockerfiles:
        text = git_show(repo, ship, df)
        for a in parse_assets(text or ""):
            a["dockerfile"] = df
            assets.append(a)
    if not assets:
        return ("INFO", f"no pinned huggingface.co/.../resolve/<rev>/*.safetensors assets in {', '.join(dockerfiles)} "
                f"at {ship[:10]}", "NO")
    lines, results = [], []
    for a in assets:
        if not a["pin"]:
            results.append("UNVERIFIABLE")
            lines.append(f"  [UNVERIFIABLE] {a['name']} ({a['dockerfile']}): no sha256 pin beside its URL")
            continue
        v, d = verify_asset(a["url"], a["pin"])
        results.append(v)
        lines.append(f"  [{v}] {a['name']} ({a['dockerfile']}) {d}")
    overall = "PASS" if all(x == "PASS" for x in results) else "FAIL" if "FAIL" in results else "UNVERIFIABLE"
    blocks = "YES" if "FAIL" in results else "NO" if overall == "PASS" else "N/A"
    return (overall, f"{results.count('PASS')}/{len(results)} PASS at {ship[:10]}:\n" + "\n".join(lines), blocks)


# ------------------------------------------------------------------------------------------------ 6 PyPI drift
def pep503(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def check6_pypi(repo, ship, freezes, cutoff):
    if not freezes or not cutoff:
        return ("INFO", "skipped: give --freeze <pip freeze of your built trainer image> and --cutoff <its build time>",
                "NO")
    cut = datetime.fromisoformat(cutoff.replace("Z", "+00:00"))
    pkgs = {}
    for f in freezes:
        for line in open(f, errors="replace"):
            m = re.match(r"^([A-Za-z0-9][A-Za-z0-9_.\-]*)==(\S+)$", line.strip())
            if m:
                pkgs[pep503(m.group(1))] = (m.group(1), m.group(2))
    pinned = set()
    for p in [x for x in git_ls_tree(repo, ship) if "requirement" in x.lower()]:
        for line in (git_show(repo, ship, p) or "").splitlines():
            m = re.match(r"^\s*([A-Za-z0-9][A-Za-z0-9_.\-]*)\s*(==|>=|<=|~=|!=|>|<|\[|$)", line)
            if m and not line.strip().startswith("#"):
                pinned.add(pep503(m.group(1)))
    drift, skipped, t0 = [], [], time.monotonic()
    for norm, (raw, ver) in sorted((k, v) for k, v in pkgs.items() if k not in pinned):
        if time.monotonic() - t0 > PYPI_BUDGET_S:
            skipped.append(raw)
            continue
        r = curl(f"https://pypi.org/pypi/{norm}/json", timeout=PYPI_TIMEOUT_S)
        if r["code"] != 200:
            continue
        best = None
        for v, files in (json.loads(r["body"]).get("releases") or {}).items():
            for fi in files:
                ts = fi.get("upload_time_iso_8601")
                if ts:
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    if dt > cut and (best is None or dt > best[1]):
                        best = (v, dt)
        if best:
            drift.append(f"{raw} (installed {ver}; PyPI {best[0]} uploaded {best[1].isoformat()})")
    lines = [f"{len(pkgs)} frozen packages, {len(pkgs) - len([k for k in pkgs if k not in pinned])} pinned in the ship "
             f"SHA's requirements; DRIFT candidates after {cutoff}: {len(drift)}"] + [f"  DRIFT: {d}" for d in drift[:40]]
    if skipped:
        lines.append(f"  not queried (time budget): {', '.join(skipped[:20])}")
    return ("INFO", "\n".join(lines), "NO")


# ------------------------------------------------------------------------------------------------ 7, 8
def check7_on_branch(repo, ship, remote, branch):
    r = run_git(repo, ["ls-remote", remote, f"refs/heads/{branch}"])
    if r.returncode != 0:
        return ("UNVERIFIABLE", f"git ls-remote {remote} failed", "N/A")
    got = (r.stdout.decode().split() or [None])[0]
    if got == ship:
        return ("PASS", f"{remote}/{branch} = {got}", "NO")
    return ("INFO", f"{remote}/{branch} = {got}, --ship-sha = {ship}: expected before the push; re-run after it", "NO")


def commands_row(repo, ship, remote, branch, track):
    ev = ("PRINTED ONLY - pushing and registering are YOUR actions:\n"
          f"  git -C {repo} push {remote} {ship}:refs/heads/{branch}\n"
          f"  then make your miner's /training_repo/{track} handler answer this exact 40-hex commit "
          "(G.O.D miner/endpoints/training_repo.py; the validator asks for it, tournament_manager.py:1186, and "
          "excludes any commit that is not 40 hex, :1091-1096).")
    return row("8", "Push + miner endpoint (reference only)", "INFO", ev, "N/A")


def main(argv=None):
    p = argparse.ArgumentParser(description="Pre-registration checks for an SN56 tournament entry (repo-agnostic).")
    p.add_argument("--repo", required=True)
    p.add_argument("--ship-sha", required=True)
    p.add_argument("--track", choices=("image", "text"), default="image")
    p.add_argument("--branch", default="main")
    p.add_argument("--remote", default="origin")
    p.add_argument("--upstream-base", default="")
    p.add_argument("--image", action="append", default=[])
    p.add_argument("--flag", action="append", default=[])
    p.add_argument("--dockerfile", action="append", default=[])
    p.add_argument("--freeze", action="append", default=[])
    p.add_argument("--cutoff", default="")
    p.add_argument("--out", default="")
    a = p.parse_args(argv)
    if not re.fullmatch(r"[0-9a-f]{40}", a.ship_sha):
        p.error("--ship-sha must be 40 lowercase hex characters")
    if a.upstream_base and not re.fullmatch(r"[0-9a-f]{40}", a.upstream_base):
        p.error("--upstream-base must be a 40-hex G.O.D commit")
    repo = os.path.abspath(a.repo)
    now = datetime.now(timezone.utc)
    out = os.path.abspath(a.out or f"monday_checks_{now.strftime('%Y%m%dT%H%M%SZ')}")
    t0 = time.monotonic()
    rows = [run_check("1", "Upstream contract diff", check1_upstream, a.upstream_base)]
    up_files = rows[0].get("related") or []
    for i, spec in enumerate(a.image, 1):
        rows.append(run_check(f"2.{i}", f"Docker digest {spec.split('=')[0]}", check2_image, spec, up_files))
    for i, spec in enumerate(a.flag, 1):
        rows.append(run_check(f"3.{i}", f"Upstream constant {spec.split('=')[0].split('@')[0]}", check3_flag, spec))
    rows.append(run_check("4", "LICENSE.md / NOTICE vs upstream (default branch)", check4_license, repo, a.ship_sha,
                          a.remote, a.branch))
    rows.append(run_check("5", "Pinned HF build assets", check5_assets, repo, a.ship_sha,
                          a.dockerfile or list(DOCKERFILES[a.track])))
    rows.append(run_check("6", "PyPI drift (unpinned deps)", check6_pypi, repo, a.ship_sha, a.freeze, a.cutoff))
    rows.append(run_check("7", f"Ship SHA on {a.remote}/{a.branch}", check7_on_branch, repo, a.ship_sha, a.remote, a.branch))
    rows.append(commands_row(repo, a.ship_sha, a.remote, a.branch, a.track))
    code = 1 if any(r["result"] == "FAIL" for r in rows) else 0
    meta = {"generated_at": now.isoformat(), "repo": repo, "ship_sha": a.ship_sha, "track": a.track,
            "run_time_s": round(time.monotonic() - t0, 2), "exit_code": code}
    print(f"\n{'ID':<5} | {'CHECK':<48} | {'RESULT':<13} | BLOCKS")
    for r in rows:
        print(f"{r['id']:<5} | {r['check'][:48]:<48} | {r['result']:<13} | {r['blocks']}")
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "monday_checks.json"), "w") as fh:
        json.dump({"meta": meta, "checks": rows}, fh, indent=2)
    with open(os.path.join(out, "monday_checks.md"), "w") as fh:
        fh.write(f"# Pre-registration checks\n\n- generated: {meta['generated_at']}\n- ship SHA: `{a.ship_sha}`\n"
                 f"- exit code: {code} (0 iff no FAIL)\n\n| # | check | result | blocks |\n|---|---|---|---|\n")
        for r in rows:
            fh.write(f"| {r['id']} | {r['check']} | {r['result']} | {r['blocks']} |\n")
        for r in rows:
            fh.write(f"\n## {r['id']}. {r['check']} - {r['result']}\n\n```\n{r['evidence']}\n```\n")
    print(f"\nwrote {out}/monday_checks.md and .json; exit {code}")
    return code


if __name__ == "__main__":
    sys.exit(main())
