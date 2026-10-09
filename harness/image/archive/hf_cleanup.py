#!/usr/bin/env python3
"""hf_cleanup.py - inventory and clean up the Hugging Face repos of ONE account, with brakes.

The safe flow (three steps; nothing is deleted before step 3, and step 3 needs an explicit confirmation):
  1) python3 hf_cleanup.py list                      -> hf_inventory.tsv  (every repo: type, private, size, last modified)
  2) python3 hf_cleanup.py plan [--keep KEEP.txt] [--older-than-days N] [--only-type model|dataset|space]
                                                      -> hf_cleanup_plan.tsv (DELETE / KEEP per repo + the reason)
  3) python3 hf_cleanup.py apply --yes --confirm <username>   -> deletes the repos marked DELETE in the plan (IRREVERSIBLE)
  Extra: python3 hf_cleanup.py prune <repo_id> --glob 'checkpoints/*' --keep-glob 'checkpoints/last.safetensors' [--yes]
         deletes the FILES inside one repo that match the glob (except keep-glob) in one commit.

Token: ONLY from a file (--token-file; default ~/.hf_env then /root/.hf_env; `HF_TOKEN=hf_...` or a bare token) or the
env HF_TOKEN. Never printed. Every action is written to hf_cleanup_<timestamp>.log.
Built-in protection: a repo whose id matches --protect-regex (default 'sn56-image-' = the dataset archives that
hf_archive_tournament.py creates) is always KEEP.
Dependency: pip install -U huggingface_hub
"""
import argparse, csv, datetime as dt, fnmatch, os, re, sys

def read_token(path_opt):
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
    sys.exit("no token found: create ~/.hf_env holding `HF_TOKEN=hf_...` (chmod 600) or use --token-file / env HF_TOKEN")

def api_and_user(args):
    try:
        from huggingface_hub import HfApi
    except ImportError:
        sys.exit("pip install -U huggingface_hub")
    api = HfApi(token=read_token(args.token_file))
    who = api.whoami()
    user = who.get("name")
    orgs = [o.get("name") for o in who.get("orgs", []) if o.get("name")]
    return api, user, orgs

def log(msg, args):
    line = f"{dt.datetime.now(dt.timezone.utc).isoformat()} {msg}"
    print(line)
    with open(args.log, "a") as f:
        f.write(line + "\n")

def repo_size(api, repo_id, repo_type):
    try:
        info = api.repo_info(repo_id, repo_type=repo_type, files_metadata=True)
        total = sum((s.size or 0) for s in (info.siblings or []))
        last = getattr(info, "last_modified", None) or getattr(info, "lastModified", None)
        return total, (last.isoformat() if hasattr(last, "isoformat") else str(last)), bool(getattr(info, "private", False)), len(info.siblings or [])
    except Exception as e:  # the repo may have just been deleted, or be gated
        return -1, f"ERR {type(e).__name__}", False, -1

def cmd_list(args):
    api, user, orgs = api_and_user(args)
    authors = [user] + ([] if args.no_orgs else orgs)
    log(f"account={user} orgs={orgs} (orgs included: {not args.no_orgs})", args)
    rows = []
    for author in authors:
        for rtype, fn in (("model", api.list_models), ("dataset", api.list_datasets), ("space", api.list_spaces)):
            try:
                items = list(fn(author=author))
            except Exception as e:
                log(f"list {rtype} author={author} failed: {type(e).__name__}", args); items = []
            for it in items:
                rid = it.id
                size, last, private, nfiles = repo_size(api, rid, rtype)
                rows.append({"repo_id": rid, "type": rtype, "private": private, "bytes": size,
                             "gb": round(size / 1e9, 3) if size >= 0 else "", "n_files": nfiles, "last_modified": last})
                print(f"  {rtype:7} {'PRIV' if private else 'pub ':4} {rows[-1]['gb']!s:>8} GB {nfiles:>4} files  {last[:19]}  {rid}")
    with open(args.inventory, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["repo_id", "type", "private", "bytes", "gb", "n_files", "last_modified"], delimiter="\t")
        w.writeheader(); w.writerows(rows)
    tot = sum(r["bytes"] for r in rows if r["bytes"] > 0)
    priv = sum(r["bytes"] for r in rows if r["bytes"] > 0 and r["private"])
    log(f"{len(rows)} repo(s), total {tot/1e9:.2f} GB (private {priv/1e9:.2f} GB) -> {args.inventory}", args)

def load_keep(path):
    keep = set()
    if path and os.path.isfile(path):
        for line in open(path):
            s = line.strip()
            if s and not s.startswith("#"):
                keep.add(s)
    return keep

def cmd_plan(args):
    if not os.path.isfile(args.inventory):
        sys.exit(f"{args.inventory} does not exist - run `list` first")
    keep = load_keep(args.keep)
    prot = re.compile(args.protect_regex) if args.protect_regex else None
    cutoff = None
    if args.older_than_days:
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=args.older_than_days)
    rows = list(csv.DictReader(open(args.inventory), delimiter="\t"))
    plan = []
    for r in rows:
        rid = r["repo_id"]; reason = []
        action = "DELETE"
        if rid in keep: action, reason = "KEEP", ["in the keep list"]
        elif prot and prot.search(rid): action, reason = "KEEP", [f"matches protect-regex {args.protect_regex!r}"]
        elif args.only_type and r["type"] != args.only_type: action, reason = "KEEP", [f"not of type {args.only_type}"]
        elif cutoff and r["last_modified"] and not r["last_modified"].startswith("ERR"):
            try:
                lm = dt.datetime.fromisoformat(r["last_modified"].replace("Z", "+00:00"))
                if lm > cutoff: action, reason = "KEEP", [f"newer than {args.older_than_days} days"]
            except ValueError:
                pass
        if action == "DELETE":
            reason = ["not in the keep list, not protected"]
        plan.append({**r, "action": action, "reason": "; ".join(reason)})
    with open(args.plan, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(plan[0].keys()), delimiter="\t"); w.writeheader(); w.writerows(plan)
    nd = [p for p in plan if p["action"] == "DELETE"]
    gb = sum(float(p["bytes"]) for p in nd if p["bytes"] not in ("", "-1")) / 1e9
    print(f"\nPLAN -> {args.plan}: DELETE {len(nd)} repo(s) ({gb:.2f} GB), KEEP {len(plan)-len(nd)}")
    for p in nd:
        print(f"  DELETE {p['type']:7} {p['gb']!s:>8} GB  {p['repo_id']}")
    print("\nReview the plan file (set the action column to KEEP for anything to keep), then: apply --yes --confirm <username>")

def cmd_apply(args):
    if not os.path.isfile(args.plan):
        sys.exit(f"{args.plan} does not exist - run `plan` first")
    api, user, orgs = api_and_user(args)
    plan = [p for p in csv.DictReader(open(args.plan), delimiter="\t") if p["action"] == "DELETE"]
    log(f"apply: {len(plan)} repo(s) marked DELETE in the plan; dry_run={not args.yes}", args)
    if args.yes and args.confirm != user:
        sys.exit(f"--confirm must equal the token's account name exactly ({user!r}); cancelled, nothing deleted")
    ok = fail = 0
    for p in plan:
        rid, rtype = p["repo_id"], p["type"]
        if not args.yes:
            print(f"  [dry-run] would delete {rtype} {rid} ({p['gb']} GB)"); continue
        try:
            api.delete_repo(rid, repo_type=rtype)
            ok += 1; log(f"DELETED {rtype} {rid} ({p['gb']} GB)", args)
        except Exception as e:
            fail += 1; log(f"FAILED {rtype} {rid}: {type(e).__name__}: {str(e)[:120]}", args)
    if args.yes:
        log(f"done: deleted {ok}, failed {fail}", args)
    else:
        print("\nDry-run. To really delete: apply --yes --confirm <username>")

def cmd_prune(args):
    api, user, orgs = api_and_user(args)
    from huggingface_hub import CommitOperationDelete
    info = api.repo_info(args.repo_id, repo_type=args.repo_type, files_metadata=True)
    files = [s.rfilename for s in (info.siblings or [])]
    victims = [f for f in files if fnmatch.fnmatch(f, args.glob) and not (args.keep_glob and fnmatch.fnmatch(f, args.keep_glob))]
    size = sum((s.size or 0) for s in info.siblings if s.rfilename in victims) / 1e9
    log(f"prune {args.repo_type} {args.repo_id}: {len(victims)}/{len(files)} file(s) match {args.glob!r} (keep {args.keep_glob!r}), {size:.2f} GB; dry_run={not args.yes}", args)
    for f in victims:
        print(f"  {'[dry-run] ' if not args.yes else ''}delete {f}")
    if not victims:
        return
    if args.yes:
        api.create_commit(args.repo_id, repo_type=args.repo_type,
                          operations=[CommitOperationDelete(path_in_repo=f) for f in victims],
                          commit_message=f"prune {len(victims)} files matching {args.glob}")
        log(f"PRUNED {len(victims)} file(s) from {args.repo_id}", args)

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--token-file", default=None)
    ap.add_argument("--inventory", default="hf_inventory.tsv")
    ap.add_argument("--plan", default="hf_cleanup_plan.tsv")
    ap.add_argument("--log", default=f"hf_cleanup_{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.log")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("list"); s.add_argument("--no-orgs", action="store_true", help="only the user's own repos, not its organisations")
    s = sub.add_parser("plan")
    s.add_argument("--keep", default="hf_keep.txt", help="file listing the repo_ids to keep (one per line)")
    s.add_argument("--protect-regex", default=r"sn56-image-", help="a repo whose id matches is always KEEP")
    s.add_argument("--older-than-days", type=int, default=None, help="only DELETE repos unchanged for N days")
    s.add_argument("--only-type", choices=["model", "dataset", "space"], default=None)
    s = sub.add_parser("apply")
    s.add_argument("--yes", action="store_true", help="without it = dry-run")
    s.add_argument("--confirm", default="", help="type the HF account name exactly to allow the deletion")
    s = sub.add_parser("prune")
    s.add_argument("repo_id"); s.add_argument("--repo-type", default="model", choices=["model", "dataset", "space"])
    s.add_argument("--glob", required=True); s.add_argument("--keep-glob", default=None); s.add_argument("--yes", action="store_true")
    args = ap.parse_args()
    {"list": cmd_list, "plan": cmd_plan, "apply": cmd_apply, "prune": cmd_prune}[args.cmd](args)

if __name__ == "__main__":
    main()
