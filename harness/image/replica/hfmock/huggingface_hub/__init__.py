"""A FAKE `huggingface_hub` (hfmock - DRY=1 and tests/ only; never on the box's real path).

It exists so the REAL vendored G.O.D code - vendor/god/trainer/containers/uploader.py (the production uploader's
entrypoint) and vendor/god/validator/evaluation/image_artifacts.py (the evaluator's materialize_model + select_lora) -
runs unchanged, with no network, against the file-backed hub in hfmock/store.py (FAKEHUB_DIR). Only the surface those
two files touch is implemented: login, HfApi.create_repo / upload_folder / model_info / list_repo_files / delete_repo /
whoami, hf_hub_download. Anything else raises AttributeError, loudly. Refuses to work without FAKEHUB_DIR, so it can
never silently stand in for the real library.
"""
import os
import sys

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from store import HubError, Store, download_to_cache  # noqa: E402

__version__ = "0.0.0-hfmock"
_LOGIN_TOKEN = None


class HfHubHTTPError(Exception):
    def __init__(self, message, status=None, code=""):
        super().__init__(message)
        self.status_code, self.error_code = status, code


class RepositoryNotFoundError(HfHubHTTPError):
    pass


class RevisionNotFoundError(HfHubHTTPError):
    pass


class EntryNotFoundError(HfHubHTTPError):
    pass


def _raise(e):
    cls = {"RepoNotFound": RepositoryNotFoundError, "RevisionNotFound": RevisionNotFoundError,
           "EntryNotFound": EntryNotFoundError}.get(e.code, HfHubHTTPError)
    if e.status == 401 and cls is HfHubHTTPError:
        cls = RepositoryNotFoundError        # huggingface_hub maps an anonymous 401 on a repo to RepositoryNotFoundError
    raise cls(f"{e.status} {e}", e.status, e.code) from None


def _token(explicit=None):
    if isinstance(explicit, str) and explicit:
        return explicit
    if explicit is False:
        return None
    return _LOGIN_TOKEN or os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN") \
        or os.environ.get("HUGGINGFACE_TOKEN") or None


def login(token=None, **_kw):
    global _LOGIN_TOKEN
    if Store().who(token) is None:
        raise ValueError("Invalid user token.")
    _LOGIN_TOKEN = token


class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def __repr__(self):
        return f"{type(self).__name__}({self.__dict__!r})"


class BlobLfsInfo(_Obj):
    pass


class RepoSibling(_Obj):
    pass


class ModelInfo(_Obj):
    pass


def _model_info(d):
    sib = [RepoSibling(rfilename=s["rfilename"], size=s.get("size"), blob_id=s.get("blobId"),
                       lfs=BlobLfsInfo(**s["lfs"]) if s.get("lfs") else None) for s in d["siblings"]]
    return ModelInfo(id=d["id"], sha=d["sha"], private=d["private"], siblings=sib, created_at=d["createdAt"],
                     last_modified=d["lastModified"])


class HfApi:
    def __init__(self, endpoint=None, token=None, **_kw):
        self.token = token
        self.store = Store()

    def _tok(self, token):
        return _token(token if token is not None else self.token)

    def whoami(self, token=None, **_kw):
        who = self.store.who(self._tok(token))
        if who is None:
            raise HfHubHTTPError("401 Invalid user token.", 401)
        return {"type": "user", "name": who[0], "auth": {"accessToken": {"displayName": "hfmock", "role": who[1]}}}

    def create_repo(self, repo_id, *, token=None, private=None, repo_type=None, exist_ok=False, visibility=None, **_kw):
        if repo_type not in (None, "model"):
            raise ValueError("hfmock: model repos only")
        priv = bool(private) if visibility is None else visibility == "private"
        try:
            self.store.create(repo_id, self._tok(token), private=priv, exist_ok=exist_ok)
        except HubError as e:
            _raise(e)
        return f"https://huggingface.co/{repo_id}"

    def upload_folder(self, *, repo_id, folder_path, path_in_repo=None, commit_message=None, token=None,
                      repo_type=None, delete_patterns=None, ignore_patterns=None, allow_patterns=None, **_kw):
        if allow_patterns is not None:
            raise NotImplementedError("hfmock: allow_patterns is not used by the G.O.D uploader")
        try:
            rev = self.store.upload_folder(repo_id, self._tok(token), folder_path, path_in_repo=path_in_repo,
                                           delete_patterns=delete_patterns, ignore_patterns=ignore_patterns,
                                           commit_message=commit_message or "Upload folder using huggingface_hub")
        except HubError as e:
            _raise(e)
        return _Obj(commit_url=f"https://huggingface.co/{repo_id}/commit/{rev}", oid=rev)

    def model_info(self, repo_id, *, revision=None, files_metadata=False, token=None, **_kw):
        try:
            return _model_info(self.store.info(repo_id, self._tok(token), revision, blobs=files_metadata))
        except HubError as e:
            _raise(e)

    def list_repo_files(self, repo_id, *, revision=None, repo_type=None, token=None, **_kw):
        try:
            return [s["rfilename"] for s in self.store.info(repo_id, self._tok(token), revision)["siblings"]]
        except HubError as e:
            _raise(e)

    def delete_repo(self, repo_id, *, token=None, repo_type=None, missing_ok=False, **_kw):
        try:
            self.store.delete(repo_id, self._tok(token))
        except HubError as e:
            if not (missing_ok and e.code == "RepoNotFound"):
                _raise(e)


def hf_hub_download(repo_id, filename, *, revision=None, token=None, cache_dir=None, repo_type=None, **_kw):
    if repo_type not in (None, "model"):
        raise ValueError("hfmock: model repos only")
    try:
        return download_to_cache(Store(), repo_id, filename, revision, _token(token), cache_dir)
    except HubError as e:
        _raise(e)
