#!/usr/bin/env python3
"""hf_flow.py - the Hugging Face half of a validator-shaped cell (cell_run.sh), host side, python3 >= 3.8, stdlib only (no huggingface_hub on
the host: the UPLOAD itself is the production uploader image, the DOWNLOAD the production evaluator image).

Validator shape it mirrors (G.O.D file:line in vendor/god/VENDOR.md): the trainer writes
/app/checkpoints/<task>/<repo>/ -> the uploader container makes <HUGGINGFACE_USERNAME>/<repo> PUBLIC and uploads that
folder into checkpoints/ -> the evaluator resolves the repo's latest revision, select_lora picks one file, hf_hub_download
fetches it. This tool checks every hand-off of that chain by sha256 and deletes the repo after the score is read.

The token: ONLY from the token file (default /root/.hf_env, line HF_TOKEN=hf_..., mode 0600, owned by the caller).
It is read when a call needs it, sent only as an Authorization header, never printed, never in argv, never logged.
Output: shell-quoted KEY=value lines for `eval` (the kit's convention), human notes on stderr.

  tokencheck [--env-file F]                           HF_TOKEN_OK=1|0 HF_TOKEN_WHY  (file: present, regular, owner,
                                                      no group/other bits, one hf_ token); exit 0 iff OK. No network.
  whoami [--env-file F] [--api URL]                   HF_USER (the ACCOUNT NAME ONLY) HF_WRITE=1|0|fine HF_WHOAMI_HTTP;
                                                      exit 0 iff 200 and the token is not read-only
  newname --task-id UUID [--prefix P]                 HF_REPO_NAME=<P>-<task8>-<rand6>  (neutral: no cell, arm, hotkey;
                                                      P = --prefix or env HF_REPO_PREFIX, default "lora")
  localsha --dir D                                    relpath<TAB>bytes<TAB>sha256 of every file under D
  verify --repo U/R --local-dir D [--env-file F] [--api URL] [--subfolder checkpoints]
                                                      the upload as the evaluator will see it: revision, PUBLIC
                                                      (authenticated private=false AND anonymous HTTP 200), the file the
                                                      REAL select_lora picks, its hub sha256 == a local file's sha256
  fetchcheck --repo U/R --part-log LOG --hf-cache DIR --expect-sha256 X [--expect-revision REV]
                                                      the file the evaluator actually downloaded (its "[latent-shim] lora"
                                                      line -> HF cache snapshot) has sha256 X at revision REV
  delete --repo U/R [--env-file F] [--api URL] [--tries 3]
                                                      DELETE /api/repos/delete, then an authenticated GET must be 404
  anonget --repo U/R --rev REV --file PATH [--api URL] HF_ANON_SHA256 of an anonymous download (preflight: public read)
  mkdummy --out FILE                                  a valid ~4 KiB safetensors of random bytes (preflight upload)
  stmeta --file F                                     ST_META_KEYS=<n> ST_META_NAMES=<names> of __metadata__ (no values)
  ledger --tsv F key=value ...                        append one row to the HF ledger (flock'd; fixed columns)
  pending --tsv F [--include-kept]                    repos the ledger shows uploaded/created and never deleted - a
                                                      leftover (a failed delete, a killed cell). A repo whose last
                                                      row is event=kept (HF_DELETE_AFTER_SCORE=0: the operator's choice
                                                      to leave it public) is NOT pending; --include-kept lists it too
  kept --tsv F                                        the repos left public on purpose (event=kept, never deleted)
  sweep --tsv F [--include-kept] [--env-file F] [--api URL]
                                                      delete every pending repo (operator command), ledger rows;
                                                      --include-kept also deletes the kept ones
  endgate --tsv F --policy delete|keep [--no-sweep] [--env-file F] [--api URL]
                                                      The queue's END GATE. policy delete (HF_DELETE_AFTER_SCORE=1):
                                                      pending must be empty - else ONE sweep, then pending again; any
                                                      repo still public -> HF_ENDGATE=FAIL (exit 1) naming it. --no-sweep
                                                      (DRY: the per-cell mock hubs are gone) fails on any pending repo.
                                                      policy keep (=0): nothing is deleted; HF_ENDGATE=KEPT lists every
                                                      public repo (exit 0). Kept repos of an earlier run are listed,
                                                      never swept, never a FAIL.
                                                      FAIL-CLOSED - a ledger that exists but cannot be trusted
                                                      (unreadable, a header this tool never wrote, a row whose field
                                                      count is not the header's) is HF_ENDGATE=FAIL (exit 1) under
                                                      either policy: what is still public cannot be read from it.
                                                      (reading such a file as "no repo" would PASS silently.) pending / kept /
                                                      sweep refuse it the same way (exit 1).
"""
import argparse
import datetime
import fcntl
import hashlib
import json
import os
import re
import secrets
import shlex
import stat
import string
import struct
import sys
import tempfile
import time
import urllib.error
import urllib.request

KIT = os.path.dirname(os.path.abspath(__file__))
TOKEN_RE = re.compile(r"hf_[A-Za-z0-9]{20,}")
DEFAULT_API = os.environ.get("HF_HUB_API", "https://huggingface.co")
DEFAULT_ENV_FILE = os.environ.get("HF_ENV_FILE", "/root/.hf_env")
REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")
SHIM_RE = re.compile(r"\[latent-shim\] lora (\S+) -> (\S+) revision=(\S+) file=(\S+) blob=(\S+)")
LEDGER_COLUMNS = ["timestamp", "event", "cell", "attempt", "task_id", "label", "repo", "source", "revision", "selected",
                  "local_sha256", "hub_sha256", "fetch_sha256", "sha_match", "upload_start_utc", "upload_end_utc",
                  "deleted_utc", "exposure_s", "score_final_hf", "score_final_disk", "status", "note"]
UA = {"User-Agent": "sn56-harness-hf_flow/1"}


def utc():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def kv(key, value):
    print(f"{key}={shlex.quote('' if value is None else str(value))}")


def note(msg):
    print(f"[hf_flow] {msg}", file=sys.stderr, flush=True)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(8 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git_blob_sha1(path):
    with open(path, "rb") as fh:
        data = fh.read()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def load_select_lora():
    """The REAL select_lora (vendor/god/validator/evaluation/image_artifacts.py:36-46), loaded by hfmock/vendored.py:
    upstream bytes, compiled with the annotations future flag so python 3.8/3.9 accept it. Its module top imports
    only the stdlib - huggingface_hub is imported inside materialize_model, which this tool never calls."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("kit_vendored", os.path.join(KIT, "hfmock", "vendored.py"))
    v = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(v)
    return v.load(v.IMAGE_ARTIFACTS, "god_image_artifacts").select_lora


# ----------------------------------------------------------------------------------------------------- token + HTTP
class TokenError(Exception):
    pass


def check_token_file(path):
    """(ok, why). Mode bits are checked, not fixed: the user creates the file (umask 077; cat > FILE)."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return False, f"{path} does not exist - create it on the box: umask 077; cat > {path}  (one line HF_TOKEN=hf_..., then Ctrl-D)"
    except OSError as e:
        return False, f"{path}: {type(e).__name__}"
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        return False, f"{path} is not a regular file (a symlink or a directory is refused)"
    if st.st_uid != os.getuid():
        return False, f"{path} is owned by uid {st.st_uid}, not by the user running the kit (uid {os.getuid()})"
    if st.st_mode & 0o077:
        return False, f"{path} has mode {stat.S_IMODE(st.st_mode):o}; it must be 600 (chmod 600 {path})"
    try:
        with open(path) as fh:
            n = len(TOKEN_RE.findall(fh.read()))
    except OSError as e:
        return False, f"{path}: unreadable ({type(e).__name__})"
    if n == 0:
        return False, f"{path} holds no hf_... token (expected one line HF_TOKEN=hf_...)"
    if n > 1:
        return False, f"{path} holds {n} hf_ tokens; keep exactly one (which one is used must never be a guess)"
    return True, f"{path}: one token, mode 600, owner uid {st.st_uid}"


def read_token(path):
    ok, why = check_token_file(path)
    if not ok:
        raise TokenError(why)
    with open(path) as fh:
        return TOKEN_RE.search(fh.read()).group(0)


class Resp:
    def __init__(self, status, body, headers):
        self.status, self.body, self.headers = status, body, headers

    def json(self):
        try:
            return json.loads(self.body or b"{}")
        except ValueError:
            return {}


def http(method, url, token=None, payload=None, timeout=60, max_bytes=None):
    headers = dict(UA)
    data = None
    if token:
        headers["Authorization"] = "Bearer " + token
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return Resp(r.status, r.read(max_bytes) if max_bytes else r.read(), dict(r.headers))
    except urllib.error.HTTPError as e:
        try:
            body = e.read(4096)
        except Exception:
            body = b""
        return Resp(e.code, body, dict(e.headers or {}))
    except Exception as e:      # never str(e) of a request object: only the exception type and the host
        host = urllib.request.urlparse(url).netloc
        return Resp(f"{type(e).__name__}@{host}", b"", {})


def api_url(api, *parts, query=""):
    return api.rstrip("/") + "/" + "/".join(p.strip("/") for p in parts) + (("?" + query) if query else "")


# ----------------------------------------------------------------------------------------------------- commands
def cmd_tokencheck(a):
    ok, why = check_token_file(a.env_file)
    kv("HF_TOKEN_OK", 1 if ok else 0)
    kv("HF_TOKEN_WHY", why)
    sys.exit(0 if ok else 1)


def whoami(api, token):
    r = http("GET", api_url(api, "api", "whoami-v2"), token=token)
    if r.status != 200:
        return r.status, None, None
    d = r.json()
    role = ((d.get("auth") or {}).get("accessToken") or {}).get("role")
    write = {"write": "1", "read": "0", "fineGrained": "fine"}.get(role, "unknown")
    return 200, d.get("name"), write


def cmd_whoami(a):
    try:
        tok = read_token(a.env_file)
    except TokenError as e:
        kv("HF_WHOAMI_HTTP", "notoken"); kv("HF_USER", ""); kv("HF_WRITE", "0"); kv("HF_WHY", str(e))
        sys.exit(1)
    code, user, write = whoami(a.api, tok)
    kv("HF_WHOAMI_HTTP", code)
    kv("HF_USER", user or "")
    kv("HF_WRITE", write or "0")
    if code != 200 or not user:
        kv("HF_WHY", f"whoami-v2 answered {code} - the token is invalid, revoked or the Hub is unreachable")
        sys.exit(1)
    if write == "0":
        kv("HF_WHY", "the token is READ-only: the uploader needs a WRITE token (create a 'Write' token on huggingface.co/settings/tokens)")
        sys.exit(1)
    kv("HF_WHY", "ok" if write == "1" else "fine-grained token: write rights are proven only by hf_preflight.sh's dummy round trip")
    sys.exit(0)


def cmd_newname(a):
    if not re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", a.task_id):
        kv("HF_REPO_NAME", ""); note(f"newname: {a.task_id!r} is not a task UUID"); sys.exit(1)
    prefix = a.prefix or os.environ.get("HF_REPO_PREFIX", "") or "lora"
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,23}", prefix):
        kv("HF_REPO_NAME", ""); note(f"newname: prefix {prefix!r} must match [a-z0-9][a-z0-9-]{{0,23}}"); sys.exit(1)
    alphabet = string.ascii_lowercase + string.digits
    kv("HF_REPO_NAME", f"{prefix}-{a.task_id[:8]}-{''.join(secrets.choice(alphabet) for _ in range(6))}")


def local_manifest(d):
    out = []
    for dp, _dn, fns in os.walk(d):
        for fn in sorted(fns):
            p = os.path.join(dp, fn)
            if os.path.isfile(p) and not os.path.islink(p):
                out.append((os.path.relpath(p, d).replace(os.sep, "/"), os.path.getsize(p), sha256_file(p)))
    return sorted(out)


def cmd_localsha(a):
    for rel, size, sha in local_manifest(a.dir):
        print(f"{rel}\t{size}\t{sha}")


def cmd_verify(a):
    why = []
    try:
        tok = read_token(a.env_file)
    except TokenError as e:
        kv("HF_VERIFY_OK", 0); kv("HF_VERIFY_WHY", str(e)); sys.exit(1)
    if not REPO_RE.match(a.repo):
        kv("HF_VERIFY_OK", 0); kv("HF_VERIFY_WHY", f"bad repo id {a.repo!r}"); sys.exit(1)
    r = http("GET", api_url(a.api, "api", "models", a.repo, query="blobs=true"), token=tok)
    if r.status != 200:
        kv("HF_VERIFY_OK", 0); kv("HF_VERIFY_HTTP", r.status)
        kv("HF_VERIFY_WHY", f"model info answered {r.status} (X-Error-Code {r.headers.get('X-Error-Code', '-')})")
        sys.exit(1)
    info = r.json()
    rev, private = info.get("sha", ""), info.get("private")
    sib = info.get("siblings") or []
    names = [s.get("rfilename", "") for s in sib]
    anon = http("GET", api_url(a.api, "api", "models", a.repo)).status
    kv("HF_REVISION", rev)
    kv("HF_PRIVATE", "true" if private else "false")
    kv("HF_ANON_HTTP", anon)
    kv("HF_FILES", ",".join(n for n in names if n != ".gitattributes"))
    if private is not False:
        why.append(f"repo is private={private!r}: the production uploader creates PUBLIC repos (uploader.py:190)")
    if anon != 200:
        why.append(f"an anonymous read of the repo answered {anon}, not 200 (not public)")
    sub = a.subfolder.strip("/") + "/"
    under = [n for n in names if n.startswith(sub)]
    if not under:
        why.append(f"no file under {sub} (HF_REPO_SUBFOLDER) in the repo")
    try:
        selected = load_select_lora()(names)
    except ValueError as e:
        selected = ""
        why.append(f"select_lora refuses this repo ({e}) - the evaluator would fail it the same way")
    kv("HF_SELECTED", selected)
    hub_sha = ""
    if selected:
        s = next(x for x in sib if x.get("rfilename") == selected)
        lfs = s.get("lfs") or {}
        hub_sha = lfs.get("sha256", "")
        kv("HF_SELECTED_BYTES", s.get("size", ""))
        if not hub_sha:
            why.append(f"{selected} is not stored as LFS on the hub (no sha256 in the listing)")
    kv("HF_SELECTED_SHA256", hub_sha)
    local = local_manifest(a.local_dir) if os.path.isdir(a.local_dir) else []
    if not local:
        why.append(f"no local copy under {a.local_dir} to compare with")
    match = [rel for rel, _sz, sha in local if hub_sha and sha == hub_sha]
    kv("HF_LOCAL_MATCH", ",".join(match) or "-")
    if hub_sha and not match:
        why.append(f"the hub's {selected} (sha256 {hub_sha[:12]}) matches NO file of the local copy")
    # every hub LFS file of the upload must be a local file too (a foreign file means the repo is not just this run's)
    local_shas = {sha for _r, _s, sha in local}
    foreign = [x["rfilename"] for x in sib if x.get("rfilename", "").startswith(sub) and (x.get("lfs") or {}).get("sha256")
               and x["lfs"]["sha256"] not in local_shas]
    kv("HF_FOREIGN", ",".join(foreign) or "-")
    if foreign:
        why.append(f"hub files with no local twin: {','.join(foreign)}")
    kv("HF_VERIFY_OK", 0 if why else 1)
    kv("HF_VERIFY_WHY", "; ".join(why) or "ok")
    sys.exit(1 if why else 0)


def cmd_fetchcheck(a):
    why = []
    lines = []
    try:
        with open(a.part_log, errors="replace") as fh:
            lines = [m for m in (SHIM_RE.search(l) for l in fh) if m and m.group(1) == a.repo]
    except OSError:
        why.append(f"no evaluator log {a.part_log}")
    if not lines and not why:
        why.append(f"the evaluator log has no '[latent-shim] lora {a.repo} -> ...' line (it never resolved the repo)")
    rev = fname = blob = sha = ""
    if lines:
        _r, _name, rev, fname, blob = lines[-1].groups()
        path = os.path.join(a.hf_cache, "models--" + a.repo.replace("/", "--"), "snapshots", rev, fname)
        kv("HF_FETCH_PATH", path)
        if not os.path.exists(path):
            why.append(f"the evaluator's download is not in the HF cache: {path}")
        else:
            sha = sha256_file(path)
            real = os.path.realpath(path)
            if re.fullmatch(r"[0-9a-f]{64}", blob) and blob != sha:
                why.append(f"the cached blob is named {blob[:12]} but hashes to {sha[:12]} (corrupt cache)")
            if os.path.basename(real) != blob:
                why.append(f"the snapshot points at blob {os.path.basename(real)[:12]}, the evaluator named {blob[:12]}")
    kv("HF_FETCH_REVISION", rev)
    kv("HF_FETCH_FILE", fname)
    kv("HF_FETCH_BLOB", blob)
    kv("HF_FETCH_SHA256", sha)
    if sha and a.expect_sha256 and sha != a.expect_sha256:
        why.append(f"the evaluator fetched sha256 {sha[:12]}, the upload was {a.expect_sha256[:12]}")
    if rev and a.expect_revision and rev != a.expect_revision:
        why.append(f"the evaluator used revision {rev[:12]}, the verified upload is {a.expect_revision[:12]}")
    kv("HF_FETCH_OK", 0 if why else 1)
    kv("HF_FETCH_WHY", "; ".join(why) or "ok")
    sys.exit(1 if why else 0)


def delete_repo(api, tok, repo, tries):
    owner, name = repo.split("/", 1)
    last = ""
    for i in range(max(1, tries)):
        r = http("DELETE", api_url(api, "api", "repos", "delete"), token=tok,
                 payload={"name": name, "organization": owner, "type": "model"})
        if r.status in (200, 204, 404):
            # gone = the owner's own token can no longer read it: 404 RepoNotFound (an authenticated miss), or 401 -
            # what huggingface.co answers a request it cannot match to a repo (observed 2026-10: anonymous miss = 401); the
            # token itself was just accepted by the DELETE above, so a 401 here is not an auth failure
            gone = http("GET", api_url(api, "api", "models", repo), token=tok)
            if gone.status in (404, 401):
                return True, (f"deleted (DELETE {r.status}, owner GET -> {gone.status})" if r.status != 404
                              else f"already gone (DELETE 404, owner GET -> {gone.status})")
            last = f"DELETE answered {r.status} but the owner's GET still answers {gone.status}"
        else:
            last = f"DELETE answered {r.status}"
        if i + 1 < tries:
            time.sleep((2, 5, 10, 20)[min(i, 3)])
    return False, last


def cmd_delete(a):
    if not REPO_RE.match(a.repo):
        kv("HF_DELETED", 0); kv("HF_DELETE_WHY", f"bad repo id {a.repo!r}"); sys.exit(1)
    try:
        tok = read_token(a.env_file)
    except TokenError as e:
        kv("HF_DELETED", 0); kv("HF_DELETE_WHY", str(e)); sys.exit(1)
    ok, why = delete_repo(a.api, tok, a.repo, a.tries)
    kv("HF_DELETED", 1 if ok else 0)
    kv("HF_DELETED_UTC", utc() if ok else "")
    kv("HF_DELETE_WHY", why)
    sys.exit(0 if ok else 1)


def cmd_anonget(a):
    r = http("GET", api_url(a.api, a.repo, "resolve", a.rev, a.file))
    kv("HF_ANON_HTTP", r.status)
    kv("HF_ANON_SHA256", hashlib.sha256(r.body).hexdigest() if r.status == 200 else "")
    sys.exit(0 if r.status == 200 else 1)


def cmd_mkdummy(a):
    data = os.urandom(4096)
    hdr = json.dumps({"preflight.lora_down.weight": {"dtype": "F32", "shape": [32, 32], "data_offsets": [0, 4096]}},
                     separators=(",", ":")).encode()
    hdr += b" " * ((8 - len(hdr) % 8) % 8)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "wb") as fh:
        fh.write(struct.pack("<Q", len(hdr)) + hdr + data)
    kv("DUMMY_SHA256", sha256_file(a.out))
    kv("DUMMY_BYTES", os.path.getsize(a.out))


def cmd_stmeta(a):
    with open(a.file, "rb") as fh:
        n = struct.unpack("<Q", fh.read(8))[0]
        hdr = json.loads(fh.read(n))
    meta = hdr.get("__metadata__") or {}
    kv("ST_META_KEYS", len(meta))
    kv("ST_META_NAMES", ",".join(sorted(meta)))


def _ledger_lock(path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fh = open(path + ".lock", "w")
    fcntl.flock(fh, fcntl.LOCK_EX)
    return fh


def cmd_ledger(a):
    row = {c: "" for c in LEDGER_COLUMNS}
    for item in a.items:
        k, sep, v = item.partition("=")
        if not sep or k not in row:
            note(f"ledger: unknown column {k!r} (columns: {', '.join(LEDGER_COLUMNS)})"); sys.exit(2)
        row[k] = v.replace("\t", " ").replace("\n", " ")
    row["timestamp"] = utc()
    lock = _ledger_lock(a.tsv)
    try:
        new = not os.path.isfile(a.tsv) or os.path.getsize(a.tsv) == 0
        if not new:
            with open(a.tsv) as fh:
                head = fh.readline().rstrip("\n").split("\t")
            if head != LEDGER_COLUMNS:
                note(f"ledger: {a.tsv} has a foreign header - refusing to append"); sys.exit(1)
        with open(a.tsv, "a") as fh:
            if new:
                fh.write("\t".join(LEDGER_COLUMNS) + "\n")
            fh.write("\t".join(row[c] for c in LEDGER_COLUMNS) + "\n")
    finally:
        lock.close()


class LedgerError(Exception):
    """The HF ledger exists but cannot be trusted (the end gate fails closed on it)."""


def read_ledger(tsv):
    """The ledger's rows (dicts), [] when the file does not exist or is empty. LedgerError when it exists but cannot be
    trusted: unreadable (a directory, no permission, not UTF-8), a header this tool never wrote (cmd_ledger refuses to
    append to one), or a row whose field count is not the header's (cmd_ledger strips tabs/newlines from every value,
    so a well-formed row always has exactly len(LEDGER_COLUMNS) fields)."""
    if not os.path.lexists(tsv):
        return []
    try:
        with open(tsv, encoding="utf-8", errors="strict") as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError) as e:
        raise LedgerError(f"{tsv} unreadable ({type(e).__name__}: {e})")
    if not text:
        return []
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    if lines[0].split("\t") != LEDGER_COLUMNS:
        raise LedgerError(f"{tsv} has a header this tool never wrote ({lines[0][:80]!r}) - which repos are public cannot "
                          f"be read from it")
    rows = []
    for n, line in enumerate(lines[1:], 2):
        f = line.split("\t")
        if len(f) != len(LEDGER_COLUMNS):
            raise LedgerError(f"{tsv} line {n} has {len(f)} fields, the header {len(LEDGER_COLUMNS)} - a damaged ledger")
        rows.append(dict(zip(LEDGER_COLUMNS, f)))
    return rows


def repo_states(tsv):
    """{repo: pending | kept | deleted} from the ledger rows in file order. pending = uploaded/created and never deleted
    (uploading, uploaded, upload_failed, delete_failed rows); kept = the cell's last word was event=kept
    (HF_DELETE_AFTER_SCORE=0, left public on purpose); a row with deleted_utc = deleted, for good. LedgerError when
    the ledger cannot be trusted."""
    state = {}
    for r in read_ledger(tsv):
        repo = r.get("repo", "")
        if not repo or state.get(repo) == "deleted":
            continue
        if r.get("deleted_utc"):
            state[repo] = "deleted"
        elif r.get("event") == "kept":
            state[repo] = "kept"
        elif r.get("event") in ("uploading", "uploaded", "upload_failed", "delete_failed"):
            state[repo] = "pending"
    return state


def pending_repos(tsv, include_kept=False):
    want = ("pending", "kept") if include_kept else ("pending",)
    return sorted(k for k, v in repo_states(tsv).items() if v in want)


def kept_repos(tsv):
    return sorted(k for k, v in repo_states(tsv).items() if v == "kept")


def cmd_pending(a):
    try:
        repos = pending_repos(a.tsv, a.include_kept)
    except LedgerError as e:
        note(f"pending: {e}"); sys.exit(1)
    for repo in repos:
        print(repo)


def cmd_kept(a):
    try:
        repos = kept_repos(a.tsv)
    except LedgerError as e:
        note(f"kept: {e}"); sys.exit(1)
    for repo in repos:
        print(repo)


def _sweep(a, todo, event):
    """Delete every repo of `todo` (3 tries each), one ledger row each; returns the repos NOT deleted."""
    tok = read_token(a.env_file)
    left = []
    for repo in todo:
        ok, why = delete_repo(a.api, tok, repo, 3)
        note(f"{event} {repo}: {why}")
        cmd_ledger(argparse.Namespace(tsv=a.tsv, items=[f"event={event}", f"repo={repo}", f"deleted_utc={utc() if ok else ''}",
                                                         f"status={'deleted' if ok else 'DELETE_FAILED'}", f"note={why}"]))
        if not ok:
            left.append(repo)
    return left


def cmd_sweep(a):
    try:
        todo = pending_repos(a.tsv, a.include_kept)
    except LedgerError as e:
        note(f"sweep: {e} - nothing swept"); sys.exit(1)
    if not todo:
        note("sweep: nothing pending"); return
    try:
        left = _sweep(a, todo, "sweep")
    except TokenError as e:
        note(f"sweep: {e}"); sys.exit(1)
    sys.exit(1 if left else 0)


def cmd_endgate(a):
    kv("HF_ENDGATE_LEDGER", a.tsv)
    try:
        pend, kept = pending_repos(a.tsv), kept_repos(a.tsv)
    except LedgerError as e:
        kv("HF_ENDGATE_SWEPT", "")
        kv("HF_ENDGATE_LEFT", f"(ledger untrusted: {e})")
        kv("HF_ENDGATE", "FAIL")
        note(f"endgate: FAIL (fail-closed) - {e}; inspect it and the hub by hand (python3 {os.path.abspath(__file__)} "
             f"pending --tsv <a good ledger>)")
        sys.exit(1)
    kv("HF_ENDGATE_PENDING", len(pend))
    kv("HF_ENDGATE_KEPT", len(kept))
    if kept:
        note(f"endgate: {len(kept)} repo(s) left public on purpose (event=kept, HF_DELETE_AFTER_SCORE=0): {' '.join(kept)}")
    if a.policy == "keep":
        kv("HF_ENDGATE", "KEPT")
        kv("HF_ENDGATE_PUBLIC", " ".join(pend + kept))
        note(f"endgate: policy keep (HF_DELETE_AFTER_SCORE=0) - nothing deleted; {len(pend) + len(kept)} repo(s) public: "
             f"{' '.join(pend + kept) or '-'}")
        return
    swept, left = [], list(pend)
    if pend and not a.no_sweep:
        note(f"endgate: {len(pend)} repo(s) still public after the queue: {' '.join(pend)} - one sweep")
        try:
            not_deleted = _sweep(a, pend, "endgate_sweep")
        except TokenError as e:
            note(f"endgate: the sweep cannot read the token: {e}")
            not_deleted = list(pend)
        swept = [r for r in pend if r not in not_deleted]
        try:
            left = pending_repos(a.tsv)
        except LedgerError as e:
            left = [f"(ledger untrusted after the sweep: {e})"]
    kv("HF_ENDGATE_SWEPT", " ".join(swept))
    kv("HF_ENDGATE_LEFT", " ".join(left))
    if left:
        kv("HF_ENDGATE", "FAIL")
        note(f"endgate: FAIL - {len(left)} repo(s) still public{' (no sweep: --no-sweep)' if a.no_sweep else ' after one sweep'}: "
             f"{' '.join(left)} - delete by hand: python3 {os.path.abspath(__file__)} sweep --tsv {a.tsv}")
        sys.exit(1)
    kv("HF_ENDGATE", "PASS")
    note(f"endgate: PASS - no repo left public by this ledger{f' ({len(swept)} swept)' if swept else ''}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, fn, *args):
        s = sub.add_parser(name)
        s.set_defaults(fn=fn)
        for spec in args:
            spec(s)
        return s

    envf = lambda s: s.add_argument("--env-file", default=DEFAULT_ENV_FILE)
    api = lambda s: s.add_argument("--api", default=DEFAULT_API)
    add("tokencheck", cmd_tokencheck, envf)
    add("whoami", cmd_whoami, envf, api)
    add("newname", cmd_newname, lambda s: s.add_argument("--task-id", required=True),
        lambda s: s.add_argument("--prefix", default=""))
    add("localsha", cmd_localsha, lambda s: s.add_argument("--dir", required=True))
    add("verify", cmd_verify, envf, api, lambda s: s.add_argument("--repo", required=True),
        lambda s: s.add_argument("--local-dir", required=True), lambda s: s.add_argument("--subfolder", default="checkpoints"))
    add("fetchcheck", cmd_fetchcheck, lambda s: s.add_argument("--repo", required=True),
        lambda s: s.add_argument("--part-log", required=True), lambda s: s.add_argument("--hf-cache", required=True),
        lambda s: s.add_argument("--expect-sha256", default=""), lambda s: s.add_argument("--expect-revision", default=""))
    add("delete", cmd_delete, envf, api, lambda s: s.add_argument("--repo", required=True),
        lambda s: s.add_argument("--tries", type=int, default=3))
    add("anonget", cmd_anonget, api, lambda s: s.add_argument("--repo", required=True),
        lambda s: s.add_argument("--rev", required=True), lambda s: s.add_argument("--file", required=True))
    add("mkdummy", cmd_mkdummy, lambda s: s.add_argument("--out", required=True))
    add("stmeta", cmd_stmeta, lambda s: s.add_argument("--file", required=True))
    add("ledger", cmd_ledger, lambda s: s.add_argument("--tsv", required=True), lambda s: s.add_argument("items", nargs="*"))
    add("pending", cmd_pending, lambda s: s.add_argument("--tsv", required=True),
        lambda s: s.add_argument("--include-kept", action="store_true"))
    add("kept", cmd_kept, lambda s: s.add_argument("--tsv", required=True))
    add("sweep", cmd_sweep, envf, api, lambda s: s.add_argument("--tsv", required=True),
        lambda s: s.add_argument("--include-kept", action="store_true"))
    add("endgate", cmd_endgate, envf, api, lambda s: s.add_argument("--tsv", required=True),
        lambda s: s.add_argument("--policy", required=True, choices=("delete", "keep")),
        lambda s: s.add_argument("--no-sweep", action="store_true"))
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
