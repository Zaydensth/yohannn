#!/usr/bin/env python3
"""Regression tests for hf_archive_tournament.py - ZERO network, ZERO writes to any real archive.

Run: python3 test_hf_archive_tournament.py        (stdlib only, no pytest)

The first tests lock the costliest silent-failure class of this tool: an "archived" state that moves while zero files
were uploaded. If one tournament is wrongly marked archived, the real `watch` skips it, its 7-day presigned window
runs out, and its dataset is gone for good - the very failure this tool exists to prevent. Do not delete these tests
without replacing their guard.
"""
import argparse
import importlib.util
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

TOOL = Path(__file__).resolve().parent / "hf_archive_tournament.py"
_spec = importlib.util.spec_from_file_location("hat", TOOL)
hat = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hat)

FAKE_TID = "tourn_deadbeefdeadbeef_20260101"  # never on disk
FIXTURE_TID = "tourn_00000000cafef00d_20260101"
_results = []


def check(name, cond, detail=""):
    _results.append((name, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {name}{('  - ' + detail) if detail else ''}")


def make_fixture_archive(root):
    """A synthetic archive in the layout `fetch` writes, with a manifest.json `fetch --dry-run` re-verifies."""
    base = root / FIXTURE_TID
    tid = "11111111-0000-4000-8000-000000000001"
    d = base / "tasks" / f"R1_flux_{tid[:8]}"
    d.mkdir(parents=True)
    (base / "api").mkdir()
    for key, n in (("test_data", 2), ("training_data", 3)):
        with zipfile.ZipFile(d / f"{key}.zip", "w") as z:
            for i in range(n):
                z.writestr(f"img_{i}.png", b"\x89PNG" + bytes([i]))
                z.writestr(f"img_{i}.txt", f"caption {i}")
    task = {"task_id": tid, "model_type": "flux", "model_id": "example-org/base", "hours_to_complete": 1,
            "test_data": "https://example.invalid/t.zip", "training_data": "https://example.invalid/r.zip",
            "hotkey_details": []}
    (base / "api" / f"{tid}.json").write_text(json.dumps(task))
    details = {"tournament_id": FIXTURE_TID, "tournament_type": "image",
               "rounds": [{"round_number": 1, "is_final_round": False, "tasks": [{"task_id": tid}]}]}
    (base / "api" / "details.json").write_text(json.dumps(details))
    (base / "manifest.json").write_text(json.dumps({"tournament_id": FIXTURE_TID, "tasks": [{"task_id": tid}], "failures": []}))
    return base


def _stub_network(tid=FAKE_TID, rows=()):
    """Replace http_get_json: zero network, records the urls asked for."""
    calls = []

    def fake_get(url, timeout=30):
        calls.append(url)
        if "latest/details" in url:
            return {"image": {"tournament_id": tid, "status": "active"}}
        if "auditing/tasks?" in url:
            return list(rows)
        raise AssertionError(f"unexpected url in a test: {url}")

    hat.http_get_json = fake_get
    return calls


def _watch_args(dry_run=True):
    return argparse.Namespace(dry_run=dry_run, audit_limit=10, token_file=None, hf_user=None,
                              interval_min=1, once=True, state_file=None)


def _fresh_state():
    return {"last_image_tournament_id": None, "seen_task_ids": [], "archived_tournament_ids": []}


# ------------------------------------------------------------------ the core guard: the "archived" state

def test_dry_run_does_not_mark_archived(logp):
    """watch --dry-run on a NEW tournament (nothing on disk, nothing uploaded) must never record it as archived."""
    _stub_network()
    state = hat.watch_tick(_watch_args(dry_run=True), _fresh_state(), logp)
    check("dry-run does not mark archived", state["archived_tournament_ids"] == [],
          f"archived={state['archived_tournament_ids']}")


def test_fetch_dry_run_without_archive_is_not_rc0():
    """`fetch --dry-run` with nothing to verify must be non-zero: zero archived files is not success."""
    args = argparse.Namespace(tourn_id=FAKE_TID, dry_run=True, out_dir=None, refresh_api=False,
                              zips_only=False, force=False)
    rc = hat.cmd_fetch(args)
    check("fetch --dry-run without an archive -> rc != 0", rc != 0, f"rc={rc}")


def test_failed_tick_stays_candidate(logp):
    """After a failed tick the same tournament MUST be proposed again on the next tick."""
    _stub_network()
    state = {"last_image_tournament_id": FAKE_TID, "seen_task_ids": [], "archived_tournament_ids": []}
    dispatched = []
    orig_fetch = hat.cmd_fetch
    hat.cmd_fetch = lambda a: (dispatched.append(a.tourn_id), 3)[1]
    try:
        hat.watch_tick(_watch_args(dry_run=True), state, logp)
    finally:
        hat.cmd_fetch = orig_fetch
    check("a tick after a failure proposes the same tournament again", dispatched == [FAKE_TID], f"dispatched={dispatched}")


def test_archived_is_skipped(logp):
    """A tournament that WAS archived is not fetched again on every tick."""
    _stub_network()
    state = _fresh_state()
    state["archived_tournament_ids"] = [FAKE_TID]
    dispatched = []
    orig_fetch = hat.cmd_fetch
    hat.cmd_fetch = lambda a: (dispatched.append(a.tourn_id), 0)[1]
    try:
        hat.watch_tick(_watch_args(dry_run=True), state, logp)
    finally:
        hat.cmd_fetch = orig_fetch
    check("an archived tournament is not fetched again", dispatched == [], f"dispatched={dispatched}")


def test_failed_upload_does_not_mark_archived(logp):
    """cmd_upload's rc is honoured: a failed upload is never marked archived."""
    _stub_network()
    state = _fresh_state()
    orig_fetch, orig_up = hat.cmd_fetch, hat.cmd_upload
    hat.cmd_fetch = lambda a: 0
    hat.cmd_upload = lambda a: 1
    try:
        hat.watch_tick(_watch_args(dry_run=False), state, logp)
    finally:
        hat.cmd_fetch, hat.cmd_upload = orig_fetch, orig_up
    check("upload rc != 0 does not mark archived", state["archived_tournament_ids"] == [],
          f"archived={state['archived_tournament_ids']}")


def test_successful_upload_marks_archived(logp):
    """The positive side must still work - otherwise the guards above only switch the feature off."""
    _stub_network()
    state = _fresh_state()
    orig_fetch, orig_up = hat.cmd_fetch, hat.cmd_upload
    hat.cmd_fetch = lambda a: 0
    hat.cmd_upload = lambda a: 0
    try:
        hat.watch_tick(_watch_args(dry_run=False), state, logp)
    finally:
        hat.cmd_fetch, hat.cmd_upload = orig_fetch, orig_up
    check("a successful upload DOES mark archived (the feature is alive)",
          state["archived_tournament_ids"] == [FAKE_TID], f"archived={state['archived_tournament_ids']}")


# ------------------------------------------------------------------------- other dry-run invariants

def test_dry_run_writes_nothing(archive):
    """`fetch --dry-run` on an archive must not touch one byte of it - archive_run.log included (the provenance record
    of when the archive was really pulled)."""
    def snap():
        out = []
        for p in sorted(archive.rglob("*")):
            if p.is_file():
                st = p.stat()
                out.append(f"{p.relative_to(archive)}|{st.st_size}|{st.st_mtime_ns}")
        return out

    before = snap()
    args = argparse.Namespace(tourn_id=archive.name, dry_run=True, out_dir=str(archive), refresh_api=False,
                              zips_only=False, force=False)
    rc = hat.cmd_fetch(args)
    after = snap()
    check("fetch --dry-run on an archive: rc=0 and zero bytes changed", rc == 0 and before == after,
          f"rc={rc} n_files={len(before)} identical={before == after}")


def test_upload_dry_run_reads_no_token(archive):
    """`upload --dry-run` must neither import huggingface_hub nor read the token."""
    read_token_called = []
    orig = hat.read_token
    hat.read_token = lambda p: (read_token_called.append(p), "unused")[1]
    try:
        args = argparse.Namespace(tourn_id=archive.name, dry_run=True, archive_dir=str(archive),
                                  token_file=None, hf_user=None, skip_extracted=False)
        rc = hat.cmd_upload(args)
    finally:
        hat.read_token = orig
    check("upload --dry-run: rc=0 and the token is never read", rc == 0 and read_token_called == [],
          f"rc={rc} read_token_calls={len(read_token_called)}")


def test_repo_always_private_dataset():
    """A dataset archive is ALWAYS private. Locked in the source so it can never silently turn public."""
    src = TOOL.read_text()
    check("create_repo dataset + private=True is in the source",
          'repo_type="dataset", private=True, exist_ok=True' in src, "look for create_repo(... private=True ...)")


def main():
    with tempfile.TemporaryDirectory() as td:
        os.environ["SN56_ARCHIVE_ROOT"] = td           # nothing below may reach a real archive root
        hat.DEFAULT_ARCHIVE_ROOT = Path(td)
        logp = Path(td) / "tick.log"
        archive = make_fixture_archive(Path(td) / "archives")
        test_dry_run_does_not_mark_archived(logp)
        test_fetch_dry_run_without_archive_is_not_rc0()
        test_failed_tick_stays_candidate(logp)
        test_archived_is_skipped(logp)
        test_failed_upload_does_not_mark_archived(logp)
        test_successful_upload_marks_archived(logp)
        test_dry_run_writes_nothing(archive)
        test_upload_dry_run_reads_no_token(archive)
        test_repo_always_private_dataset()
    n_fail = sum(1 for _, ok, _ in _results if not ok)
    print(f"\n{len(_results) - n_fail}/{len(_results)} PASS, {n_fail} FAIL")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
