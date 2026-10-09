#!/usr/bin/env python3
"""hf_archive_tournament.py - archive an SN56 image tournament's datasets to a PRIVATE Hugging Face dataset repo, for good.

The problem: `test_data` / `training_data` in the task API are presigned URLs that EXPIRE after 7 days
(`X-Amz-Expires=604800`). Once a tournament is a week old its held-out data cannot be pulled from anywhere again, and
with it goes every chance to replay that tournament offline. This tool (a) pulls and verifies those datasets to local
disk, then (b) uploads them to a PRIVATE HF dataset repo `<hf_user>/sn56-image-<tourn_id>` so the copy is permanent.

Subcommands:
  fetch <tourn_id> [--dry-run]      pull api details + every task json, download test_data/training_data, verify
                                    zip + sha256 + image<->caption pairing, write manifest.json / MANIFEST.md /
                                    boards.tsv / ARCHIVE_DONE. Idempotent: a cached api json and a valid zip are not
                                    downloaded again. --dry-run: ZERO network calls and ZERO disk writes: an archive
                                    already on disk is re-read and re-verified (sha256 + unzip + pairing); with nothing
                                    on disk it prints what it WOULD fetch and exits non-zero (nothing was archived).
  upload <tourn_id> [--dry-run]     upload the whole archive dir to a PRIVATE HF dataset repo (repo_type=dataset,
                                    private=True). Token ONLY from a file (--token-file; default ~/.hf_env then
                                    /root/.hf_env; `HF_TOKEN=hf_...` or a bare token) or the env HF_TOKEN. Idempotent:
                                    large files whose hub LFS sha256 already matches are SKIPPED; small metadata files
                                    (manifest / boards / api json) are always refreshed. One commit per call.
                                    --dry-run: ZERO HF calls (huggingface_hub not even imported) - a file list only.
  backfill [--dry-run]              upload every tourn_* dir under the archive root. Dirs larger than --max-size-gb
                                    (default 5.0) are SKIPPED with a note unless --include-large - this tool does not
                                    decide on its own whether a large archive should be trimmed.
  watch [--interval-min N] [--once] [--dry-run]
                                    poll for a NEW image tournament, then fetch + upload it. Two public signals:
                                    (1) GET /tournament/latest/details, key "image" - cheap, but observed to LAG (it can
                                    keep pointing at the last COMPLETED tournament while a new one already runs -
                                    INFERRED from one observation, not from the route's code); (2) GET
                                    /auditing/tasks?limit=N, new ImageTask rows, the tourn_id taken from the pattern
                                    `tournament-(tourn_[0-9a-f]+_\\d{8})-` in trained_model_repository /
                                    hotkey_details[].repo - which works only once some repo is recorded. --once = one tick
                                    (for tests); without it a loop with sleep(interval_min*60), meant to run detached in
                                    screen/tmux on a box. Fetch by hand as soon as you know a tournament exists: `watch`
                                    is an extra safety net, never the only one.

The token is never printed, logged or sent anywhere but to huggingface_hub itself. Trained MODEL repos are not this
tool's business: the validator's own uploader makes them public (G.O.D trainer/containers/uploader.py:190). This tool
archives DATASETS only, and a dataset repo is ALWAYS private here (test_hf_archive_tournament.py locks that in).
Archive root: env SN56_ARCHIVE_ROOT (default ~/sn56-archives). Dependencies: stdlib only for fetch / listing / watch;
`upload` and `backfill` (not --dry-run) need `pip install -U huggingface_hub`.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

API_BASE = "https://api.gradients.io"
DEFAULT_ARCHIVE_ROOT = Path(os.path.expanduser(os.environ.get("SN56_ARCHIVE_ROOT", "~/sn56-archives")))
DEFAULT_WATCH_STATE = DEFAULT_ARCHIVE_ROOT / ".hf_archive_watch_state.json"
TOURN_ID_RE = re.compile(r"^tourn_[0-9a-f]+_\d{8}$")
TOURN_REPO_RE = re.compile(r"tournament-(tourn_[0-9a-f]+_\d{8})-")
IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tiff"}
DEFAULT_MAX_BACKFILL_GB = 5.0


# --------------------------------------------------------------------------------------- common

def now_iso():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def log_line(log_path, msg):
    line = f"[{now_iso()}] {msg}"
    print(line, flush=True)
    if log_path:
        log_path = Path(log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a") as f:
            f.write(line + "\n")


def read_token(path_opt):
    """Same convention as hf_cleanup.py: the token comes ONLY from a file or the env, never from a CLI argument (a
    CLI argument shows up in `ps` and in shell history)."""
    cands = [path_opt] if path_opt else [os.path.expanduser("~/.hf_env"), "/root/.hf_env"]
    for p in cands:
        if p and os.path.isfile(p):
            with open(p) as f:
                txt = f.read()
            m = re.search(r"hf_[A-Za-z0-9]{20,}", txt)
            if m:
                return m.group(0)
    tok = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")
    if tok and tok.startswith("hf_"):
        return tok
    sys.exit(
        "no token found: create ~/.hf_env holding `HF_TOKEN=hf_...` (chmod 600), "
        "or use --token-file / env HF_TOKEN. A token is never taken as a CLI argument."
    )


def http_get_json(url, timeout=30):
    req = Request(url, headers={"User-Agent": "hf_archive_tournament/1 (SN56 harness)"})
    try:
        with urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except HTTPError as e:
        raise RuntimeError(f"GET {url} -> HTTP {e.code}") from e
    except URLError as e:
        raise RuntimeError(f"GET {url} -> {e}") from e


def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def human_gb(nbytes):
    return nbytes / 1e9


def round_label(round_number, is_final):
    return "Boss" if is_final else f"R{round_number}"


def unzip_verify(path):
    try:
        with zipfile.ZipFile(path) as z:
            return z.testzip() is None
    except Exception:
        return False


def analyze_zip_pairing(zip_path, trigger_word=None):
    """Read the image<->caption pairing STRAIGHT from the zip (no extraction) - used by the dry-run (zero disk writes)
    and by the normal path alike, so the two paths can never disagree."""
    try:
        with zipfile.ZipFile(zip_path) as z:
            names = [n for n in z.namelist() if not n.endswith("/") and "__MACOSX" not in n
                     and not os.path.basename(n).startswith(".")]
            images, captions = {}, {}
            for n in names:
                d, fn = os.path.split(n)
                stem, ext = os.path.splitext(fn)
                ext = ext.lower()
                if ext in IMG_EXT:
                    images[(d, stem)] = n
                elif ext == ".txt":
                    captions[(d, stem)] = n
            img_keys, cap_keys = set(images), set(captions)
            paired = img_keys & cap_keys
            cap_words, cap_with_trigger = [], 0
            for k in paired:
                try:
                    txt = z.read(captions[k]).decode("utf-8", errors="replace")
                except Exception:
                    txt = ""
                cap_words.append(len(txt.split()))
                if trigger_word and trigger_word in txt:
                    cap_with_trigger += 1
            return {
                "n_images": len(img_keys), "n_captions": len(cap_keys), "n_paired": len(paired),
                "n_images_without_caption": len(img_keys - cap_keys),
                "n_captions_without_image": len(cap_keys - img_keys),
                "avg_caption_words": round(sum(cap_words) / len(cap_words), 1) if cap_words else None,
                "captions_with_trigger": cap_with_trigger if trigger_word else None,
                "error": None,
            }
    except Exception as e:
        return {"n_images": None, "n_captions": None, "n_paired": None,
                "n_images_without_caption": None, "n_captions_without_image": None,
                "avg_caption_words": None, "captions_with_trigger": None,
                "error": f"{type(e).__name__}: {e}"}


def extract_zip(zip_path, dest_dir):
    dest_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(dest_dir)


def curl_download_retry(url, dst: Path, tag, log_path, retries=3):
    attempts = []
    for attempt in range(1, retries + 1):
        if dst.exists():
            dst.unlink()
        dst.parent.mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        r = subprocess.run(
            ["curl", "-sS", "-A", "Mozilla/5.0", "--max-time", "600", "-o", str(dst),
             "-w", "HTTPCODE=%{http_code} SIZE=%{size_download}", url],
            capture_output=True, text=True,
        )
        dt_s = time.time() - t0
        stdout = r.stdout.strip()
        attempts.append({"attempt": attempt, "returncode": r.returncode, "stdout": stdout})
        log_line(log_path, f"{tag}: attempt {attempt} rc={r.returncode} {stdout} wall={dt_s:.1f}s")
        http_ok = "HTTPCODE=200" in stdout
        if r.returncode == 0 and http_ok and dst.exists() and dst.stat().st_size > 0 and unzip_verify(dst):
            return True, attempts
        if attempt < retries:
            time.sleep(3 * attempt)
    return False, attempts


# ------------------------------------------------------------------------------------- fetch

def fetch_json_cached(url, cache_path: Path, dry_run: bool, refresh: bool, log_path):
    """One path for live AND dry-run. dry-run: ZERO network - the cache if present, else None + a 'would fetch' log
    line. Live: the cache unless refresh, else GET + save to cache_path."""
    if cache_path.exists() and (dry_run or not refresh):
        try:
            return json.loads(cache_path.read_text())
        except Exception as e:
            log_line(log_path, f"cache {cache_path} corrupt ({e}), {'skipped (dry-run)' if dry_run else 'fetching again'}")
            if dry_run:
                return None
    if dry_run:
        log_line(log_path, f"[dry-run] would GET {url} -> {cache_path} (NOT run, zero network)")
        return None
    data = http_get_json(url)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(data, indent=2))
    log_line(log_path, f"GET {url} -> {cache_path} ({cache_path.stat().st_size} B)")
    return data


def fetch_zip_cached(url, dst: Path, tag, dry_run: bool, log_path, retries=3):
    cached_ok = dst.exists() and dst.stat().st_size > 0 and unzip_verify(dst)
    if cached_ok:
        log_line(log_path, f"{tag}: cache valid (sha256+unzip OK), skip")
        return {"ok": True, "source": "cache", "size_bytes": dst.stat().st_size,
                "sha256": sha256_file(dst), "unzip_ok": True}
    if dry_run:
        exists = dst.exists()
        log_line(log_path, f"[dry-run] {tag}: {'cache PRESENT but invalid' if exists else 'NOT on disk'} - "
                            f"would download {url} (NOT run, zero network)")
        return {"ok": False, "source": "dry-run-would-fetch",
                "size_bytes": dst.stat().st_size if exists else 0,
                "sha256": sha256_file(dst) if exists else None,
                "unzip_ok": exists and unzip_verify(dst)}
    ok, attempts = curl_download_retry(url, dst, tag, log_path, retries)
    size = dst.stat().st_size if dst.exists() else 0
    return {"ok": ok, "source": "downloaded", "attempts": attempts, "size_bytes": size,
            "sha256": sha256_file(dst) if ok else None, "unzip_ok": ok}


def build_manifest_for_task(t, tid, label, round_number, dd: Path, dry_run, log_path, do_extract):
    task8 = tid[:8]
    rec = {
        "task_id": tid, "round": label, "round_number": round_number,
        "model_type": t.get("model_type"), "model_id": t.get("model_id"),
        "hours_to_complete": t.get("hours_to_complete"), "trigger_word": t.get("trigger_word"),
        "ds": t.get("ds"), "created_at": t.get("created_at"), "status": t.get("status"),
        "is_organic": t.get("is_organic"), "task_type": t.get("task_type"),
        "n_hotkey_details": len(t.get("hotkey_details") or []),
        "files": {},
    }
    for key in ("test_data", "training_data"):
        url = t.get(key)
        dst = dd / f"{key}.zip"
        tag = f"{label}_{rec['model_type']}_{task8}/{key}.zip"
        if not url:
            rec["files"][key] = {"ok": False, "error": "NO_URL"}
            continue
        entry = fetch_zip_cached(url, dst, tag, dry_run, log_path)
        if entry.get("ok") and do_extract and not dry_run:
            extract_zip(dst, dd / key)
        if entry.get("ok") or (dry_run and dst.exists()):
            entry["analysis"] = analyze_zip_pairing(dst, t.get("trigger_word")) if dst.exists() else None
        rec["files"][key] = entry
    return rec


def render_manifest_md(tourn_id, details, manifest_tasks, path: Path):
    lines = [f"# MANIFEST - {tourn_id}", "", f"Created/updated: {now_iso()}", "",
             f"winner_hotkey: `{details.get('winner_hotkey')}`  base_winner_hotkey: `{details.get('base_winner_hotkey')}`",
             "", "| task_id | round | model_type | hours | test zip ok | train zip ok | n_paired (test/train) |",
             "|---|---|---|---|---|---|---|"]
    for m in manifest_tasks:
        tf, rf = m["files"].get("test_data", {}), m["files"].get("training_data", {})
        pt = (tf.get("analysis") or {}).get("n_paired")
        pr = (rf.get("analysis") or {}).get("n_paired")
        lines.append(f"| `{m['task_id'][:8]}` | {m['round']} | {m['model_type']} | {m['hours_to_complete']} "
                      f"| {tf.get('ok')} | {rf.get('ok')} | {pt}/{pr} |")
    path.write_text("\n".join(lines) + "\n")


def render_boards_tsv(details, path: Path, task_jsons):
    cols = ["task_id", "round", "model_type", "hours_to_complete", "hotkey", "rank", "test_loss",
            "score_reason", "repo"]
    rows = [cols]
    round_label_by_task = {}
    for r in details.get("rounds", []):
        lbl = round_label(r["round_number"], r.get("is_final_round", False))
        for t in r.get("tasks", []):
            round_label_by_task[t["task_id"]] = lbl
    for tid, t in task_jsons.items():
        lbl = round_label_by_task.get(tid, "?")
        for hd in (t.get("hotkey_details") or []):
            rows.append([tid, lbl, t.get("model_type"), t.get("hours_to_complete"), hd.get("hotkey"),
                         hd.get("rank"), hd.get("test_loss"), hd.get("score_reason"), hd.get("repo")])
    with open(path, "w") as f:
        for row in rows:
            f.write("\t".join("" if v is None else str(v) for v in row) + "\n")


def cmd_fetch(args):
    tourn_id = args.tourn_id
    if not TOURN_ID_RE.match(tourn_id):
        log_line(None, f"WARNING: {tourn_id!r} does not match tourn_<hex>_<yyyymmdd> - going on, "
                       f"but it may not be a valid tournament_id")
    base = Path(args.out_dir) if args.out_dir else DEFAULT_ARCHIVE_ROOT / tourn_id
    api_dir, tasks_dir = base / "api", base / "tasks"
    dry = args.dry_run
    # --dry-run must write ZERO bytes: log_path=None makes log_line() print to stdout only and never append to the
    # archive's archive_run.log (the provenance record of REAL fetches - "when was this archive actually pulled").
    # test_hf_archive_tournament.py checks that a dry-run leaves every byte of an archive untouched.
    log_path = None if dry else (base / "archive_run.log")

    log_line(log_path, f"=== fetch {tourn_id} dry_run={dry} base={base} ===")
    details = fetch_json_cached(f"{API_BASE}/tournament/{tourn_id}/details", api_dir / "details.json",
                                 dry, args.refresh_api, log_path)
    if details is None:
        print(f"[dry-run] no api/details.json on disk and a dry-run does not fetch it - "
              f"nothing to verify. Run without --dry-run first for a new tournament." if dry
              else "FAILED: cannot fetch details.json")
        # rc=3 (NOT 0) when a dry-run has nothing on disk: a 0 here would read as "fetch succeeded" to watch_tick,
        # which would then mark the tournament ARCHIVED without anything uploaded (locked by
        # test_hf_archive_tournament.py::test_dry_run_does_not_mark_archived). Zero archived files is never success.
        return 1 if not dry else 3
    if details.get("tournament_type") not in (None, "image") and not args.force:
        print(f"REFUSE: tournament_type={details.get('tournament_type')!r}, not 'image'. "
              f"This tool archives the image track. --force archives anyway.")
        return 1

    task_order, round_info = [], {}
    for r in details.get("rounds", []):
        lbl = round_label(r["round_number"], r.get("is_final_round", False))
        for t in r.get("tasks", []):
            tid = t["task_id"]
            round_info[tid] = {"round_number": r["round_number"], "label": lbl}
            task_order.append(tid)

    manifest_tasks, task_jsons, failures = [], {}, []
    for tid in task_order:
        tj = fetch_json_cached(f"{API_BASE}/auditing/tasks/{tid}", api_dir / f"{tid}.json",
                                dry, args.refresh_api, log_path)
        if tj is None:
            failures.append({"task_id": tid, "reason": "no_api_json" + ("_dry_run" if dry else "")})
            continue
        task_jsons[tid] = tj
        rinfo = round_info[tid]
        dd = tasks_dir / f"{rinfo['label']}_{tj.get('model_type')}_{tid[:8]}"
        rec = build_manifest_for_task(tj, tid, rinfo["label"], rinfo["round_number"], dd, dry, log_path,
                                       do_extract=not args.zips_only)
        manifest_tasks.append(rec)
        for key, entry in rec["files"].items():
            if not entry.get("ok") and entry.get("source") != "dry-run-would-fetch":
                failures.append({"task_id": tid, "file": key, "reason": entry.get("error", "download_failed")})

    if dry:
        n_cached_ok = sum(1 for m in manifest_tasks for e in m["files"].values() if e.get("source") == "cache" and e.get("ok"))
        n_would_fetch = sum(1 for m in manifest_tasks for e in m["files"].values() if e.get("source") == "dry-run-would-fetch")
        print(f"[dry-run] {tourn_id}: {len(manifest_tasks)} task(s) from the manifest/API cache, "
              f"{n_cached_ok} zip(s) valid on disk (sha256+unzip re-verified, zero network), "
              f"{n_would_fetch} WOULD be downloaded without --dry-run. Zero files written.")
        for f in failures:
            print(f"  note: {f}")
        return 0

    base.mkdir(parents=True, exist_ok=True)
    manifest = {"tournament_id": tourn_id, "generated_at": now_iso(), "tasks": manifest_tasks, "failures": failures}
    (base / "manifest.json").write_text(json.dumps(manifest, indent=2))
    render_manifest_md(tourn_id, details, manifest_tasks, base / "MANIFEST.md")
    render_boards_tsv(details, base / "boards.tsv", task_jsons)
    if not failures:
        (base / "ARCHIVE_DONE").write_text(json.dumps({"finished_at": now_iso(), "n_tasks": len(manifest_tasks),
                                                        "n_failures": 0}, indent=2))
    log_line(log_path, f"=== fetch {tourn_id} done: {len(manifest_tasks)} task(s), {len(failures)} failure(s) ===")
    print(f"fetch {tourn_id}: {len(manifest_tasks)} task(s), {len(failures)} failure(s) -> {base}")
    return 0 if not failures else 2


# ------------------------------------------------------------------------------------ upload

def iter_archive_files(base_dir: Path, skip_extracted=False):
    for root, dirs, files in os.walk(base_dir):
        rootp = Path(root)
        if skip_extracted and rootp.name in ("test_data", "training_data") and (rootp.parent.parent == base_dir / "tasks" or rootp.parent.parent.name == "tasks"):
            dirs[:] = []
            continue
        for fn in files:
            if fn == ".DS_Store":
                continue
            p = rootp / fn
            yield p.relative_to(base_dir), p, p.stat().st_size


def cmd_upload(args):
    tourn_id = args.tourn_id
    base = Path(args.archive_dir) if args.archive_dir else DEFAULT_ARCHIVE_ROOT / tourn_id
    if not base.is_dir():
        print(f"REFUSE: {base} does not exist")
        return 1
    files = sorted(iter_archive_files(base, skip_extracted=args.skip_extracted), key=lambda x: str(x[0]))
    total_bytes = sum(sz for _, _, sz in files)
    log_path = base / "hf_upload.log"

    if args.dry_run:
        user = args.hf_user or "<hf_user: --hf-user or the real token's account>"
        repo_id = f"{user}/sn56-image-{tourn_id}"
        print(f"[dry-run] repo_id (would be created private=True, repo_type=dataset): {repo_id}")
        print(f"[dry-run] {len(files)} file(s), total {human_gb(total_bytes):.3f} GB, from {base}")
        for rel, _, sz in files:
            print(f"  {sz/1e6:8.2f} MB  {rel}")
        print(f"[dry-run] zero HF calls, zero token reads, huggingface_hub not imported.")
        return 0

    try:
        from huggingface_hub import CommitOperationAdd, HfApi
    except ImportError:
        sys.exit("pip install -U huggingface_hub")
    api = HfApi(token=read_token(args.token_file))
    hf_user = args.hf_user or api.whoami().get("name")
    repo_id = f"{hf_user}/sn56-image-{tourn_id}"
    api.create_repo(repo_id=repo_id, repo_type="dataset", private=True, exist_ok=True)
    log_line(log_path, f"repo {repo_id} ready (private dataset)")

    # idempotency: a large (LFS) file whose hub sha256 already matches -> skip. Small metadata files (manifest /
    # boards / api json: non-LFS git blobs without a sha256 in the sibling listing) -> always refreshed.
    try:
        info = api.repo_info(repo_id, repo_type="dataset", files_metadata=True)
        remote_sha = {s.rfilename: (s.lfs or {}).get("sha256") for s in (info.siblings or []) if s.lfs}
    except Exception as e:
        log_line(log_path, f"repo_info failed ({e}), treating the repo as empty")
        remote_sha = {}

    ops, skipped, metadata_suffixes = [], 0, (".json", ".md", ".tsv", ".log", ".txt")
    for rel, abspath, sz in files:
        relstr = str(rel)
        is_metadata = relstr.endswith(metadata_suffixes) and sz < 2_000_000
        if not is_metadata and relstr in remote_sha:
            local_sha = sha256_file(abspath)
            if local_sha == remote_sha[relstr]:
                skipped += 1
                continue
        ops.append(CommitOperationAdd(path_in_repo=relstr, path_or_fileobj=str(abspath)))

    if not ops:
        log_line(log_path, f"{repo_id}: already up to date, 0 file(s) uploaded, {skipped} skipped")
        print(f"up to date: https://huggingface.co/datasets/{repo_id} (0 uploaded, {skipped} skipped)")
        return 0
    api.create_commit(repo_id=repo_id, repo_type="dataset", operations=ops,
                       commit_message=f"archive {tourn_id}: {len(ops)} files, {now_iso()}")
    url = f"https://huggingface.co/datasets/{repo_id}"
    log_line(log_path, f"{repo_id}: uploaded {len(ops)} file(s), skipped {skipped}, -> {url}")
    print(f"UPLOADED {len(ops)} file(s) ({skipped} skipped as identical) -> {url}")
    return 0


# ---------------------------------------------------------------------------------- backfill

def cmd_backfill(args):
    root = Path(args.eval_data_root) if args.eval_data_root else DEFAULT_ARCHIVE_ROOT
    dirs = sorted(d for d in root.iterdir() if d.is_dir() and d.name.startswith("tourn_"))
    print(f"backfill: {len(dirs)} tourn_* dir(s) under {root}")
    for d in dirs:
        size_b = sum(sz for _, _, sz in iter_archive_files(d))
        gb = human_gb(size_b)
        if gb > args.max_size_gb and not args.include_large:
            print(f"  SKIP  {d.name}  {gb:.2f} GB  (> --max-size-gb {args.max_size_gb}; --include-large "
                  f"includes it - this tool does not decide that on its own)")
            continue
        print(f"  {'[dry-run] ' if args.dry_run else ''}upload {d.name}  {gb:.2f} GB")
        sub_args = argparse.Namespace(tourn_id=d.name, archive_dir=str(d), dry_run=args.dry_run,
                                       token_file=args.token_file, hf_user=args.hf_user,
                                       skip_extracted=args.skip_extracted)
        cmd_upload(sub_args)


# -------------------------------------------------------------------------------------- watch

def load_watch_state(path: Path):
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return {"last_image_tournament_id": None, "seen_task_ids": [], "archived_tournament_ids": []}


def save_watch_state(path: Path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2))


def watch_tick(args, state, log_path):
    """One tick: two public signals, then fetch+upload (or a dry-run of both) for a new tourn_id. Split out of
    cmd_watch so it can be tested directly (--once) without the sleep() loop."""
    new_ids = []
    try:
        latest = http_get_json(f"{API_BASE}/tournament/latest/details")
        img = latest.get("image") or {}
        tid = img.get("tournament_id")
        log_line(log_path, f"signal1 /tournament/latest/details image.tournament_id={tid} "
                           f"status={img.get('status')} (last_seen={state.get('last_image_tournament_id')})")
        # The idempotency gate is archived_tournament_ids (what WAS archived), NOT last_image_tournament_id (what was
        # last seen): gating on "seen" would drop a tournament whose first tick failed and never retry it. A
        # tournament stays a candidate on every tick until its upload really succeeded.
        if tid and tid not in (state.get("archived_tournament_ids") or []):
            new_ids.append(tid)
        if tid:
            state["last_image_tournament_id"] = tid  # only for the log line above
    except Exception as e:
        log_line(log_path, f"signal1 FAILED: {e!r}")

    try:
        rows = http_get_json(f"{API_BASE}/auditing/tasks?limit={args.audit_limit}")
        n_image_rows = 0
        for row in rows:
            if row.get("task_type") != "ImageTask":
                continue
            n_image_rows += 1
            if row["task_id"] in state["seen_task_ids"]:
                continue
            state["seen_task_ids"].append(row["task_id"])
            blob = (row.get("trained_model_repository") or "") + " " + " ".join(
                str((hd or {}).get("repo") or "") for hd in (row.get("hotkey_details") or []))
            m = TOURN_REPO_RE.search(blob)
            if m and m.group(1) not in new_ids:
                new_ids.append(m.group(1))
        state["seen_task_ids"] = state["seen_task_ids"][-2000:]  # cap the state's growth
        log_line(log_path, f"signal2 /auditing/tasks?limit={args.audit_limit}: {n_image_rows} ImageTask row(s) on "
                           f"this page (image rows are a small share of the combined feed - see the README)")
    except Exception as e:
        log_line(log_path, f"signal2 FAILED: {e!r}")

    for tid in new_ids:
        if tid in state.get("archived_tournament_ids", []):
            continue
        log_line(log_path, f"NEW tournament detected: {tid} -> fetch+upload (dry_run={args.dry_run})")
        fetch_args = argparse.Namespace(tourn_id=tid, dry_run=args.dry_run, out_dir=None,
                                         refresh_api=False, zips_only=False, force=False)
        rc = cmd_fetch(fetch_args)
        if rc != 0:
            log_line(log_path, f"fetch {tid} rc={rc}, upload DEFERRED - still a candidate on the next tick")
            continue
        upload_args = argparse.Namespace(tourn_id=tid, dry_run=args.dry_run, archive_dir=None,
                                          token_file=args.token_file, hf_user=args.hf_user,
                                          skip_extracted=False)
        urc = cmd_upload(upload_args)
        if urc != 0:
            # cmd_upload's rc is honoured: zero files uploaded must never be recorded as archived.
            log_line(log_path, f"upload {tid} rc={urc}, NOT marked archived - still a candidate on the next tick")
            continue
        if args.dry_run:
            # a dry-run must never change what the next REAL run does: marking it archived here would make the real
            # watch skip that tournament forever while its 7-day presigned window runs out - the very failure this
            # tool exists to prevent.
            log_line(log_path, f"[dry-run] {tid} NOT marked archived (a dry-run never moves the state)")
            continue
        state.setdefault("archived_tournament_ids", []).append(tid)
        log_line(log_path, f"{tid} marked archived (upload succeeded)")
    return state


def cmd_watch(args):
    state_path = Path(args.state_file) if args.state_file else DEFAULT_WATCH_STATE
    log_path = DEFAULT_ARCHIVE_ROOT / "hf_archive_watch.log"
    state = load_watch_state(state_path)
    log_line(log_path, f"=== watch start interval={args.interval_min}min once={args.once} dry_run={args.dry_run} ===")
    while True:
        state = watch_tick(args, state, log_path)
        if args.dry_run:
            # the same invariant as `fetch --dry-run`: a dry-run never moves the state a real run reads (it would
            # use up signal 2's seen_task_ids).
            log_line(log_path, f"[dry-run] state NOT written to {state_path}")
        else:
            save_watch_state(state_path, state)
        if args.once:
            return 0
        log_line(log_path, f"sleeping {args.interval_min} min (meant to run detached in screen/tmux on a box)")
        time.sleep(args.interval_min * 60)


# --------------------------------------------------------------------------------------- main

def build_parser():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("fetch")
    s.add_argument("tourn_id")
    s.add_argument("--dry-run", action="store_true", help="zero network, zero disk writes; verify what is on disk")
    s.add_argument("--out-dir", default=None, help="default $SN56_ARCHIVE_ROOT/<tourn_id>")
    s.add_argument("--refresh-api", action="store_true", help="re-fetch api/*.json even when cached")
    s.add_argument("--zips-only", action="store_true", help="do not extract the zips, keep the .zip files only")
    s.add_argument("--force", action="store_true", help="go on even when tournament_type != image")

    s = sub.add_parser("upload")
    s.add_argument("tourn_id")
    s.add_argument("--dry-run", action="store_true", help="zero HF calls; a file + size list only")
    s.add_argument("--archive-dir", default=None, help="default $SN56_ARCHIVE_ROOT/<tourn_id>")
    s.add_argument("--token-file", default=None)
    s.add_argument("--hf-user", default=None, help="default: whoami() of the token")
    s.add_argument("--skip-extracted", action="store_true",
                    help="do not upload the extracted tasks/*/{test_data,training_data}/ dirs "
                         "(duplicates of the .zip beside them) - about half the size")

    s = sub.add_parser("backfill")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--eval-data-root", default=None)
    s.add_argument("--max-size-gb", type=float, default=DEFAULT_MAX_BACKFILL_GB)
    s.add_argument("--include-large", action="store_true", help="also upload dirs > --max-size-gb")
    s.add_argument("--token-file", default=None)
    s.add_argument("--hf-user", default=None)
    s.add_argument("--skip-extracted", action="store_true")

    s = sub.add_parser("watch")
    s.add_argument("--interval-min", type=float, default=15.0)
    s.add_argument("--once", action="store_true", help="one tick, then exit (for tests)")
    s.add_argument("--dry-run", action="store_true", help="real detection (public network), "
                                                            "but fetch+upload run as dry-runs")
    s.add_argument("--audit-limit", type=int, default=200)
    s.add_argument("--state-file", default=None)
    s.add_argument("--token-file", default=None)
    s.add_argument("--hf-user", default=None)
    return ap


def main():
    args = build_parser().parse_args()
    rc = {"fetch": cmd_fetch, "upload": cmd_upload, "backfill": cmd_backfill, "watch": cmd_watch}[args.cmd](args)
    sys.exit(rc or 0)


if __name__ == "__main__":
    main()
