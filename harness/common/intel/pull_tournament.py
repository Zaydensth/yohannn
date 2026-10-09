#!/usr/bin/env python3
"""Pull everything public about one G.O.D tournament into a folder, in one command (text or image).

Why: a post-mortem needs data from four places that each need their own recipe - the tournament REST endpoint
(rounds, tasks), the per-task auditing endpoint (model, hours, dataset fields, HF repos, presigned data URLs that
EXPIRE after 7 days), the Hugging Face API (small files of every submission: loss.txt, config.json,
adapter_config.json, ...) and, if you have access, the validator's Loki (what every container printed). Re-deriving
those recipes every week costs a session; this script is that session, replayable.

A dev tool: stdlib only, Python 3.9 compatible. Keep it OUT of your miner repo's submission tree (the validator clones
the whole repo; dev tooling is scan and dedup surface).

Usage:
    python3 pull_tournament.py tourn_<hash>_<yyyymmdd> --out DIR [--hotkey <your hotkey prefix>] [--top 3]
        [--hf] [--loki] [--data]

    --hotkey   your hotkey (a prefix is enough); marks your rows and pulls your artefacts too
               (default: env SN56_OUR_HOTKEY)
    --hf       fetch the small artefacts of the top N + yours per task (loss.txt, config.json, ...)
    --loki     pull container logs per (task, hotkey); needs SN56_GRAFANA_HOST and SN56_LOKI_DS in the environment
               (never in a repo)
    --data     download the validator's test_data / training_data (presigned: only inside the 7-day window).
               For image tournaments prefer ../../image/archive/hf_archive_tournament.py fetch (zips, verified).

Output: DIR/details.json, DIR/tasks/task_<id>.json, DIR/hf/<task8>_<hk8>/..., DIR/loki/..., DIR/data/..., and
DIR/summary.md with the per-task standings and the small-tournament standing replicated from G.O.D
validator/tournament/round_results.py:1067-1128 (_get_small_tournament_group_winners; sort key: has_error,
average_rank, summed_loss, hotkey - b49ba8644a0d). Upstream ranks each task on adjusted_loss, which
validator/scoring/tasks.py:161 sets to test_loss for every valid entry, lower-is-better except GrpoTask and
EnvTask (higher-is-better, tasks.py:165-175); this replica does the same on the PUBLIC test_loss. Entries with
test_loss None / 0.0 are treated as unscored (the public API's invalid entries) - INFERRED equivalent to upstream's
filter on score_reason / NaN (tasks.py:120-136).
"""
import argparse
import concurrent.futures
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from typing import Dict, List, Optional

API = "https://api.gradients.io"
HF = "https://huggingface.co"
SMALL_HF_FILES = (
    "loss.txt", "config.json", "adapter_config.json", "generation_config.json", "README.md",
    "tokenizer_config.json", "chat_template.jinja", "special_tokens_map.json",
)
LOKI_CONTAINERS = 'text-trainer.*|downloader.*|hf-upload.*|dpo-trainer.*|grpo-trainer.*|image-trainer.*'
HIGHER_IS_BETTER = ("GrpoTask", "EnvTask")    # core/models/task_models.py:41,43; validator/scoring/tasks.py:165-175


def get_json(url: str, timeout: int = 90):
    req = urllib.request.Request(url, headers={"User-Agent": "sn56-harness-intel/1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def save(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(obj, fh, indent=1)


def short(hk: Optional[str]) -> str:
    return (hk or "")[:8]


def ranked(scores: List[dict], higher_is_better: bool = False) -> List[dict]:
    """Scored entries best first (ascending test_loss; descending for GrpoTask / EnvTask); unscored (None/0.0) last."""
    good = [s for s in scores if s.get("test_loss") not in (None, 0, 0.0)]
    bad = [s for s in scores if s.get("test_loss") in (None, 0, 0.0)]
    good.sort(key=lambda s: s["test_loss"], reverse=higher_is_better)
    return good + bad


def higher_better(task: Optional[dict]) -> bool:
    return (task or {}).get("task_type") in HIGHER_IS_BETTER


def pull_details(tourn: str, out: str) -> dict:
    d = get_json("%s/tournament/%s/details" % (API, tourn))
    save(os.path.join(out, "details.json"), d)
    return d


def pull_tasks(details: dict, out: str) -> Dict[str, dict]:
    ids = [t["task_id"] for r in details.get("rounds", []) for t in r.get("tasks", [])]
    tasks = {}

    def one(tid):
        try:
            return tid, get_json("%s/auditing/tasks/%s" % (API, tid))
        except Exception as exc:  # noqa: BLE001 - report, keep going
            return tid, {"task_id": tid, "error": str(exc)}

    with concurrent.futures.ThreadPoolExecutor(8) as ex:
        for tid, t in ex.map(one, ids):
            tasks[tid] = t
            save(os.path.join(out, "tasks", "task_%s.json" % tid), t)
    return tasks


def small_tournament_standings(round_tasks: List[dict], tasks: Dict[str, dict]) -> List[dict]:
    """Replicates _get_small_tournament_group_winners (round_results.py:1067-1128) on the public scores."""
    rankings, losses, competitors = [], [], set()
    for t in round_tasks:
        tj = tasks.get(t["task_id"]) or {}
        hd = tj.get("hotkey_details") or []
        scored = [(h["hotkey"], h["test_loss"]) for h in hd if h.get("test_loss") not in (None, 0, 0.0)]
        if not scored:
            continue
        scored.sort(key=lambda x: x[1], reverse=higher_better(tj))
        competitors.update(h for h, _ in scored)
        rankings.append([h for h, _ in scored])
        losses.append(dict(scored))
    rows = []
    for hk in competitors:
        total_rank, attended, summed = 0.0, 0, 0.0
        for rk, ls in zip(rankings, losses):
            if hk in rk:
                total_rank += rk.index(hk) + 1
                attended += 1
                summed += ls[hk]
            else:
                total_rank += len(rk) + 1
                summed = float("inf")
        n = len(rankings) or 1
        rows.append({"hotkey": hk, "average_rank": total_rank / n, "summed_loss": summed,
                     "has_error": attended < len(rankings), "attended": attended})
    rows.sort(key=lambda r: (r["has_error"], r["average_rank"], r["summed_loss"], r["hotkey"]))
    return rows


def pull_hf(tasks: Dict[str, dict], out: str, our: str, top: int) -> List[dict]:
    want = []
    for tid, t in tasks.items():
        hd = ranked(t.get("hotkey_details") or [], higher_better(t))
        for i, h in enumerate(hd, 1):
            if h.get("repo") and (i <= top or (our and h["hotkey"].startswith(our))):
                want.append((tid[:8], h["hotkey"], i, h["repo"]))

    def one(item):
        tid8, hk, rank, repo = item
        rec = {"task": tid8, "hotkey": short(hk), "rank": rank, "repo": repo}
        d = os.path.join(out, "hf", "%s_%s" % (tid8, short(hk)))
        os.makedirs(d, exist_ok=True)
        try:
            meta = get_json("%s/api/models/%s" % (HF, repo))
            save(os.path.join(d, "api.json"), meta)
            files = [s["rfilename"] for s in meta.get("siblings", [])]
            rec["files"] = [f for f in files if not f.endswith(".safetensors")]
            rec["weights"] = [f for f in files if f.endswith(".safetensors")]
            rec["used_storage"] = meta.get("usedStorage")
            for fn in files:
                if fn in SMALL_HF_FILES:
                    try:
                        urllib.request.urlretrieve("%s/%s/resolve/main/%s" % (HF, repo, fn), os.path.join(d, fn))
                    except Exception as exc:  # noqa: BLE001
                        rec.setdefault("errors", []).append("%s: %s" % (fn, exc))
            lt = os.path.join(d, "loss.txt")
            if os.path.exists(lt):
                rec["loss_txt"] = open(lt).read().strip()[:200]
            cp = os.path.join(d, "config.json")
            if os.path.exists(cp):
                c = json.load(open(cp))
                rec["config"] = {k: c.get(k) for k in ("architectures", "torch_dtype", "dtype",
                                                       "max_position_embeddings", "transformers_version")}
            ap = os.path.join(d, "adapter_config.json")
            if os.path.exists(ap):
                a = json.load(open(ap))
                rec["lora"] = {k: a.get(k) for k in ("r", "lora_alpha", "lora_dropout", "target_modules")}
        except Exception as exc:  # noqa: BLE001
            rec["errors"] = [str(exc)]
        return rec

    with concurrent.futures.ThreadPoolExecutor(8) as ex:
        recs = list(ex.map(one, want))
    save(os.path.join(out, "hf", "index.json"), recs)
    return recs


def pull_loki(details: dict, tasks: Dict[str, dict], out: str, days: int = 7) -> List[dict]:
    host = os.environ.get("SN56_GRAFANA_HOST")
    ds = os.environ.get("SN56_LOKI_DS")
    if not host or not ds:
        print("[loki] SN56_GRAFANA_HOST / SN56_LOKI_DS not set; skipping", file=sys.stderr)
        return []
    start = int((time.time() - days * 86400) * 1e9)
    end = int(time.time() * 1e9)
    hotkeys = [p["hotkey"] for p in details.get("participants", [])]
    base = "%s/api/datasources/proxy/uid/%s/loki/api/v1/query_range" % (host.rstrip("/"), ds)
    jobs = [(tid, hk) for tid in tasks for hk in hotkeys]

    def one(job):
        tid, hk = job
        q = '{task_id="%s",hotkey="%s",container_name=~"%s"}' % (tid, hk, LOKI_CONTAINERS)
        url = base + "?" + urllib.parse.urlencode({"query": q, "start": start, "end": end,
                                                    "limit": 5000, "direction": "forward"})
        rec = {"task": tid[:8], "hotkey": short(hk), "lines": 0, "by_container": {}}
        try:
            d = get_json(url, timeout=120)
            save(os.path.join(out, "loki", "%s_%s.json" % (tid[:8], short(hk))), d)
            for r in d.get("data", {}).get("result", []):
                c = r["stream"].get("container_name", "?").split("-")[0]
                rec["by_container"][c] = rec["by_container"].get(c, 0) + len(r["values"])
                rec["lines"] += len(r["values"])
        except Exception as exc:  # noqa: BLE001
            rec["error"] = str(exc)
        return rec

    with concurrent.futures.ThreadPoolExecutor(6) as ex:
        recs = list(ex.map(one, jobs))
    save(os.path.join(out, "loki", "index.json"), recs)
    return recs


def pull_data(tasks: Dict[str, dict], out: str) -> None:
    os.makedirs(os.path.join(out, "data"), exist_ok=True)
    jobs = []
    for tid, t in tasks.items():
        for kind in ("test_data", "training_data"):
            if t.get(kind):
                jobs.append((os.path.join(out, "data", "%s_%s.json" % (tid[:8], kind)), t[kind]))

    def one(job):
        path, url = job
        if os.path.exists(path) and os.path.getsize(path) > 0:
            return path, "exists"
        try:
            urllib.request.urlretrieve(url, path)
            return path, os.path.getsize(path)
        except Exception as exc:  # noqa: BLE001
            return path, "ERR %s" % exc

    with concurrent.futures.ThreadPoolExecutor(4) as ex:
        for path, res in ex.map(one, jobs):
            print("[data] %s %s" % (os.path.basename(path), res))


def write_summary(details: dict, tasks: Dict[str, dict], out: str, our: str, hf: List[dict], loki: List[dict]) -> str:
    lines = ["# %s" % details.get("tournament_id"), "",
             "status=%s type=%s participants=%d winner=%s base_winner=%s" % (
                 details.get("status"), details.get("tournament_type"), len(details.get("participants", [])),
                 short(details.get("winner_hotkey")), short(details.get("base_winner_hotkey"))), ""]
    hf_by = {(r["task"], r["hotkey"]): r for r in hf}
    for r in details.get("rounds", []):
        lines.append("## round %s (%s) %s" % (r.get("round_number"), r.get("round_type"), r.get("status")))
        for t in r.get("tasks", []):
            tid = t["task_id"]
            tj = tasks.get(tid) or {}
            lines.append("")
            lines.append("### %s %s | %s | %s h | ds=%s" % (
                tid[:8], tj.get("task_type"), tj.get("model_id"), tj.get("hours_to_complete"), tj.get("ds")))
            hd = ranked(tj.get("hotkey_details") or [], higher_better(tj))
            best = hd[0]["test_loss"] if hd and hd[0].get("test_loss") else None
            lines.append("| rank | hotkey | test_loss | gap | loss.txt | dtype/mpe | reason |")
            lines.append("|---|---|---|---|---|---|---|")
            for i, h in enumerate(hd, 1):
                tl = h.get("test_loss")
                gap = "" if not (best and tl) else "%+.2f%%" % ((tl / best - 1) * 100)
                mark = " **<-- ours**" if our and h["hotkey"].startswith(our) else ""
                rec = hf_by.get((tid[:8], short(h["hotkey"])), {})
                cfg = rec.get("config") or {}
                dt = "%s/%s" % (cfg.get("dtype") or cfg.get("torch_dtype"), cfg.get("max_position_embeddings")) if cfg else ""
                if rec.get("lora"):
                    dt += " lora r=%s" % rec["lora"].get("r")
                lines.append("| %d | %s%s | %s | %s | %s | %s | %s |" % (
                    i, short(h["hotkey"]), mark, tl, gap, rec.get("loss_txt", ""), dt, (h.get("score_reason") or "")[:60]))
        if r.get("round_number") == 1 and len(r.get("tasks", [])) > 1:
            lines.append("")
            lines.append("#### small-tournament standing (has_error, average_rank, summed_loss) - replicated")
            for i, s in enumerate(small_tournament_standings(r["tasks"], tasks), 1):
                mark = " **<-- ours**" if our and s["hotkey"].startswith(our) else ""
                lines.append("%2d. %s%s avg_rank=%.2f summed=%.4f error=%s" % (
                    i, short(s["hotkey"]), mark, s["average_rank"], s["summed_loss"], s["has_error"]))
        lines.append("")
    if loki:
        lines.append("## loki lines per (task, hotkey) - trainer container only")
        for rec in sorted(loki, key=lambda x: (x["task"], -x["lines"])):
            if rec["lines"]:
                lines.append("- %s %s: %s" % (rec["task"], rec["hotkey"], rec["by_container"]))
    text = "\n".join(lines) + "\n"
    with open(os.path.join(out, "summary.md"), "w") as fh:
        fh.write(text)
    return text


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tournament_id")
    ap.add_argument("--out", required=True)
    ap.add_argument("--hotkey", default=os.environ.get("SN56_OUR_HOTKEY", ""))
    ap.add_argument("--top", type=int, default=3)
    ap.add_argument("--hf", action="store_true")
    ap.add_argument("--loki", action="store_true")
    ap.add_argument("--data", action="store_true")
    args = ap.parse_args()

    details = pull_details(args.tournament_id, args.out)
    tasks = pull_tasks(details, args.out)
    hf = pull_hf(tasks, args.out, args.hotkey, args.top) if args.hf else []
    loki = pull_loki(details, tasks, args.out) if args.loki else []
    if args.data:
        pull_data(tasks, args.out)
    print(write_summary(details, tasks, args.out, args.hotkey, hf, loki))
    return 0


if __name__ == "__main__":
    sys.exit(main())
