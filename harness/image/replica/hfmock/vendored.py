"""hfmock/vendored.py - load or run the vendored G.O.D files (vendor/god/) on ANY python3 >= 3.8.

The files are byte-for-byte upstream (vendor/god/VENDOR.md) and use `X | None` annotations, which python < 3.10
evaluates at def time and rejects. Compiling them with the `annotations` future flag (PEP 563) turns every annotation
into a string and changes nothing else - neither file reads its own annotations - so the box's python3 (3.8+) and
macOS's /usr/bin/python3 (3.9) run the upstream code without a single edited byte.

The files are NOT shipped with this kit (upstream code, fetched at setup with its own license): run
vendor/fetch_god_vendor.sh once. A missing file raises FileNotFoundError naming that script.
"""
import __future__
import os
import runpy  # noqa: F401  (kept for readers: run_script mirrors runpy.run_path(..., run_name="__main__"))
import sys
import types

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENDOR = os.path.join(KIT, "vendor", "god")
IMAGE_ARTIFACTS = os.path.join(VENDOR, "validator", "evaluation", "image_artifacts.py")
UPLOADER = os.path.join(VENDOR, "trainer", "containers", "uploader.py")


def _code(path):
    if not os.path.isfile(path):
        raise FileNotFoundError(f"{path} is missing - fetch the upstream files first: bash vendor/fetch_god_vendor.sh "
                                f"(GOD_SHA=<the G.O.D commit you reviewed>)")
    with open(path, "rb") as fh:
        src = fh.read()
    return compile(src, path, "exec", flags=__future__.annotations.compiler_flag, dont_inherit=True)


def load(path, name):
    """The module object of a vendored file (module-level code runs once)."""
    mod = types.ModuleType(name)
    mod.__file__ = path
    sys.modules[name] = mod
    exec(_code(path), mod.__dict__)
    return mod


def run_script(path, argv=()):
    """Run a vendored file as __main__, like `python FILE ARGS` would."""
    sys.argv = [path, *argv]
    g = {"__name__": "__main__", "__file__": path, "__builtins__": __builtins__}
    exec(_code(path), g)


if __name__ == "__main__":       # python3 vendored.py FILE [ARGS...]  (DRY: the production uploader, no docker)
    if len(sys.argv) < 2:
        sys.exit("usage: vendored.py VENDORED_FILE [ARGS...]")
    target = os.path.abspath(sys.argv[1])
    if not target.startswith(VENDOR + os.sep):
        sys.exit(f"vendored.py runs vendor/god files only, not {target}")
    run_script(target, sys.argv[2:])
