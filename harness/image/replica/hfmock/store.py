"""hfmock/store.py - the file-backed stand-in for the Hugging Face Hub that DRY=1 and tests/ use (never the box's real
path). One storage directory (FAKEHUB_DIR) is shared by two front ends:
  * hfmock/huggingface_hub/  - a fake `huggingface_hub` package, so the REAL vendored G.O.D code
                               (vendor/god/trainer/containers/uploader.py, vendor/god/validator/evaluation/image_artifacts.py)
                               runs unchanged against it;
  * hfmock/mock_hub.py       - a loopback HTTP server speaking the REST shapes hf_flow.py uses against huggingface.co
                               (whoami-v2, /api/models/<repo>[?blobs=true], /api/repos/create, /api/repos/delete,
                               /<repo>/resolve/<rev>/<path>).
Semantics copied from what was read on 2026-10-07 (huggingface_hub 1.18.0 hf_api.py + live anonymous calls to
huggingface.co): a public repo answers anyone; a missing or private repo answers an ANONYMOUS caller 401 (no
X-Error-Code), an authenticated caller 404 X-Error-Code: RepoNotFound; .safetensors / .bin / .pt / .pth / .h5 / .zip
files and anything > 10 MB are LFS (siblings[].lfs = {sha256, size, pointerSize}); upload_folder's delete_patterns are
matched relative to path_in_repo and never delete .gitattributes. Layout:
  FAKEHUB_DIR/accounts.json                 {"tokens": {"<token>": {"name": "<user>", "role": "write|read"}}}
  FAKEHUB_DIR/repos/<owner>/<name>/meta.json {"private": bool, "sha": "<rev>", "revs": [...], "created": iso}
  FAKEHUB_DIR/repos/<owner>/<name>/revs/<rev>/<path>    every file of that revision (small files: tests and DRY only)
  FAKEHUB_DIR/events.log                    one line per create / commit / delete / download (UTC, repo, what)
"""
import datetime
import fnmatch
import hashlib
import json
import os
import shutil

LFS_PATTERNS = ("*.safetensors", "*.bin", "*.pt", "*.pth", "*.h5", "*.zip", "*.ckpt", "*.gguf", "*.npz")
LFS_MIN_BYTES = 10 * 1024 * 1024
GITATTRIBUTES = "".join(f"{p} filter=lfs diff=lfs merge=lfs -text\n" for p in LFS_PATTERNS)
DEFAULT_IGNORE = (".git", ".git/*", "*/.git", "**/.git/**", ".cache/huggingface", ".cache/huggingface/*",
                  "*/.cache/huggingface", "**/.cache/huggingface/**")


class HubError(Exception):
    def __init__(self, status, message, code=""):
        super().__init__(message)
        self.status, self.code = status, code


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _git_blob_sha1(path):
    data = open(path, "rb").read()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _dirwild(p):
    return p + "*" if p.endswith("/") else p


class Store:
    def __init__(self, root=None):
        self.root = root or os.environ.get("FAKEHUB_DIR")
        if not self.root:
            raise HubError(500, "FAKEHUB_DIR is not set (hfmock is the DRY/test hub only)")
        os.makedirs(os.path.join(self.root, "repos"), exist_ok=True)

    # ---------------------------------------------------------------- accounts
    def _accounts(self):
        p = os.path.join(self.root, "accounts.json")
        if not os.path.isfile(p):
            return {"tokens": {}}
        with open(p) as fh:
            return json.load(fh)

    def add_token(self, token, name, role="write"):
        acc = self._accounts()
        acc.setdefault("tokens", {})[token] = {"name": name, "role": role}
        with open(os.path.join(self.root, "accounts.json"), "w") as fh:
            json.dump(acc, fh)

    def who(self, token):
        """(name, role) or None. The token itself is never written anywhere but accounts.json."""
        if not token:
            return None
        rec = self._accounts().get("tokens", {}).get(token)
        return (rec["name"], rec.get("role", "write")) if rec else None

    # ---------------------------------------------------------------- repos
    def _dir(self, repo_id):
        owner, _, name = repo_id.partition("/")
        if not owner or not name or "/" in name or owner.startswith(".") or name.startswith("."):
            raise HubError(400, f"invalid repo id {repo_id!r}")
        return os.path.join(self.root, "repos", owner, name)

    def _meta(self, repo_id):
        p = os.path.join(self._dir(repo_id), "meta.json")
        if not os.path.isfile(p):
            return None
        with open(p) as fh:
            return json.load(fh)

    def _save_meta(self, repo_id, meta):
        with open(os.path.join(self._dir(repo_id), "meta.json"), "w") as fh:
            json.dump(meta, fh, indent=1)

    def event(self, what, repo_id, detail=""):
        with open(os.path.join(self.root, "events.log"), "a") as fh:
            fh.write(f"{_now()}\t{what}\t{repo_id}\t{detail}\n")

    def visible(self, repo_id, token):
        """The meta of a repo this caller may read, else HubError 401 (anonymous) / 404 (authenticated) - the
        status huggingface.co gives (2026-10-07, read live)."""
        meta = self._meta(repo_id)
        who = self.who(token)
        if meta is not None and (not meta["private"] or (who and who[0] == repo_id.split("/")[0])):
            return meta
        if who is None:
            raise HubError(401, "Invalid username or password.")
        raise HubError(404, "Repository Not Found", "RepoNotFound")

    def _writer(self, repo_id, token):
        who = self.who(token)
        if who is None:
            raise HubError(401, "Invalid username or password.")
        if who[1] != "write":
            raise HubError(403, "You don't have the rights to write to this repo (read-only token)")
        if who[0] != repo_id.split("/")[0]:
            raise HubError(403, f"You don't have the rights to create a model under the namespace {repo_id.split('/')[0]}")
        return who

    def create(self, repo_id, token, private=False, exist_ok=False):
        self._writer(repo_id, token)
        d = self._dir(repo_id)
        if os.path.isfile(os.path.join(d, "meta.json")):
            if exist_ok:
                return
            raise HubError(409, "You already created this model repo", "RepoExists")
        os.makedirs(d, exist_ok=True)
        meta = {"private": bool(private), "sha": "", "revs": [], "created": _now()}
        self._save_meta(repo_id, meta)
        # a new repo carries .gitattributes from its first commit, like the Hub's initial commit
        tmp = os.path.join(d, ".init")
        os.makedirs(tmp, exist_ok=True)
        with open(os.path.join(tmp, ".gitattributes"), "w") as fh:
            fh.write(GITATTRIBUTES)
        self._commit(repo_id, {".gitattributes": os.path.join(tmp, ".gitattributes")}, [], "initial commit")
        shutil.rmtree(tmp, ignore_errors=True)
        self.event("create", repo_id, "private" if private else "public")

    def delete(self, repo_id, token):
        self._writer(repo_id, token)
        d = self._dir(repo_id)
        if not os.path.isfile(os.path.join(d, "meta.json")):
            raise HubError(404, "Repository Not Found", "RepoNotFound")
        shutil.rmtree(d)
        self.event("delete", repo_id)

    def files(self, repo_id, rev):
        base = os.path.join(self._dir(repo_id), "revs", rev)
        out = []
        for dp, _dn, fns in os.walk(base):
            for fn in fns:
                out.append(os.path.relpath(os.path.join(dp, fn), base).replace(os.sep, "/"))
        return sorted(out)

    def file_path(self, repo_id, rev, path):
        p = os.path.join(self._dir(repo_id), "revs", rev, path)
        if not os.path.isfile(p):
            raise HubError(404, f"Entry not found: {path}", "EntryNotFound")
        return p

    def siblings(self, repo_id, rev):
        out = []
        for f in self.files(repo_id, rev):
            p = self.file_path(repo_id, rev, f)
            size = os.path.getsize(p)
            rec = {"rfilename": f, "blobId": _git_blob_sha1(p), "size": size}
            if size > LFS_MIN_BYTES or any(fnmatch.fnmatch(os.path.basename(f), pat) for pat in LFS_PATTERNS):
                rec["lfs"] = {"sha256": _sha256(p), "size": size, "pointerSize": 134}
            out.append(rec)
        return out

    def resolve_rev(self, meta, revision):
        if revision in (None, "", "main"):
            return meta["sha"]
        if revision in meta["revs"]:
            return revision
        raise HubError(404, f"Invalid rev id: {revision}", "RevisionNotFound")

    def info(self, repo_id, token, revision=None, blobs=False):
        meta = self.visible(repo_id, token)
        rev = self.resolve_rev(meta, revision)
        sib = self.siblings(repo_id, rev)
        if not blobs:
            sib = [{"rfilename": s["rfilename"]} for s in sib]
        return {"id": repo_id, "modelId": repo_id, "sha": rev, "private": meta["private"], "gated": False,
                "disabled": False, "createdAt": meta["created"], "lastModified": meta.get("modified", meta["created"]),
                "siblings": sib}

    def _commit(self, repo_id, adds, deletes, message):
        """adds {path_in_repo: local file}, deletes [path_in_repo]; a new revision = copy of the parent +/- changes."""
        meta = self._meta(repo_id)
        parent = meta["sha"]
        d = self._dir(repo_id)
        h = hashlib.sha1((parent + message + _now()).encode())
        for k in sorted(adds):
            h.update(k.encode() + _sha256(adds[k]).encode())
        for k in sorted(deletes):
            h.update(b"-" + k.encode())
        rev = h.hexdigest()
        new = os.path.join(d, "revs", rev)
        if parent:
            shutil.copytree(os.path.join(d, "revs", parent), new)
        else:
            os.makedirs(new)
        for k in deletes:
            p = os.path.join(new, k)
            if os.path.isfile(p):
                os.remove(p)
        for k, src in adds.items():
            dst = os.path.join(new, k)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, dst)
        meta["sha"] = rev
        meta["revs"].append(rev)
        meta["modified"] = _now()
        self._save_meta(repo_id, meta)
        self.event("commit", repo_id, f"rev={rev} add={len(adds)} del={len(deletes)} msg={message}")
        return rev

    def upload_folder(self, repo_id, token, folder_path, path_in_repo=None, delete_patterns=None,
                      ignore_patterns=None, commit_message="Upload folder"):
        """huggingface_hub.HfApi.upload_folder semantics (hf_api.py 1.18.0 _prepare_upload_folder_additions /
        _prepare_upload_folder_deletions): every FILE under folder_path (recursive, sorted), ignore_patterns (+ the
        library's DEFAULT_IGNORE_PATTERNS) matched with fnmatch on the path relative to folder_path ('x/' -> 'x/*'),
        then prefixed with path_in_repo; delete_patterns matched on remote paths relative to path_in_repo, never
        .gitattributes, never a path that is being added."""
        self._writer(repo_id, token)
        meta = self._meta(repo_id)
        if meta is None:
            raise HubError(404, "Repository Not Found", "RepoNotFound")
        folder = os.path.abspath(folder_path)
        if not os.path.isdir(folder):
            raise HubError(400, f"Provided path: '{folder_path}' is not a directory")
        ign = [_dirwild(p) for p in list(ignore_patterns or []) + list(DEFAULT_IGNORE)]
        rel = []
        for dp, _dn, fns in os.walk(folder):
            for fn in fns:
                r = os.path.relpath(os.path.join(dp, fn), folder).replace(os.sep, "/")
                if not any(fnmatch.fnmatch(r, p) for p in ign):
                    rel.append(r)
        prefix = (path_in_repo.strip("/") + "/") if path_in_repo and path_in_repo not in (".", "./") else ""
        adds = {prefix + r: os.path.join(folder, r) for r in sorted(rel)}
        deletes = []
        if delete_patterns:
            pats = [_dirwild(p) for p in ([delete_patterns] if isinstance(delete_patterns, str) else delete_patterns)]
            for f in self.files(repo_id, meta["sha"]):
                if prefix and not f.startswith(prefix):
                    continue
                relf = f[len(prefix):]
                if f != ".gitattributes" and f not in adds and any(fnmatch.fnmatch(relf, p) for p in pats):
                    deletes.append(f)
        return self._commit(repo_id, adds, deletes, commit_message)


def cache_root():
    """Where hf_hub_download writes: HF_HUB_CACHE, else HUGGINGFACE_HUB_CACHE, else $HF_HOME/hub, else
    ~/.cache/huggingface/hub (huggingface_hub constants)."""
    for k in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE"):
        if os.environ.get(k):
            return os.environ[k]
    if os.environ.get("HF_HOME"):
        return os.path.join(os.environ["HF_HOME"], "hub")
    return os.path.expanduser("~/.cache/huggingface/hub")


def download_to_cache(store, repo_id, filename, revision, token, cache_dir=None):
    """The huggingface_hub cache layout: models--<o>--<n>/blobs/<etag> (sha256 for LFS files, git blob sha1 otherwise),
    snapshots/<rev>/<filename> -> ../../blobs/<etag> (relative symlink), refs/<branch> only for a branch revision."""
    meta = store.visible(repo_id, token)
    rev = store.resolve_rev(meta, revision)
    src = store.file_path(repo_id, rev, filename)
    sib = next(s for s in store.siblings(repo_id, rev) if s["rfilename"] == filename)
    etag = sib["lfs"]["sha256"] if "lfs" in sib else sib["blobId"]
    root = os.path.join(cache_dir or cache_root(), "models--" + repo_id.replace("/", "--"))
    blob = os.path.join(root, "blobs", etag)
    os.makedirs(os.path.dirname(blob), exist_ok=True)
    if not os.path.isfile(blob):
        shutil.copyfile(src, blob + ".incomplete")
        os.replace(blob + ".incomplete", blob)
    snap = os.path.join(root, "snapshots", rev, filename)
    os.makedirs(os.path.dirname(snap), exist_ok=True)
    if os.path.lexists(snap):
        os.remove(snap)
    os.symlink(os.path.relpath(blob, os.path.dirname(snap)), snap)
    if revision in (None, "", "main"):
        os.makedirs(os.path.join(root, "refs"), exist_ok=True)
        with open(os.path.join(root, "refs", "main"), "w") as fh:
            fh.write(rev)
    store.event("download", repo_id, f"rev={rev} file={filename} etag={etag}")
    return snap
