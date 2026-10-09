"""hfmock: the bits of huggingface_hub.utils the vendored G.O.D evaluator code may import."""
from . import EntryNotFoundError, HfHubHTTPError, RepositoryNotFoundError, RevisionNotFoundError  # noqa: F401


def enable_progress_bars():
    return None


def disable_progress_bars():
    return None
