# archive — keep every tournament's datasets for good (private HF dataset repos)

The task API's `test_data` / `training_data` are **presigned URLs that expire after 7 days**. After that, a tournament's
held-out set cannot be pulled from anywhere, and with it goes every chance to replay that tournament offline (calibrate
your evaluator replica, re-score an old idea). These tools pull the data to disk, verify it, and upload it to a
**private** Hugging Face dataset repo.

| file | what |
|---|---|
| `hf_archive_tournament.py` | `fetch` (api details + task jsons + both zips, verified: zip CRC, sha256, image<->caption pairing; writes `manifest.json`, `MANIFEST.md`, `boards.tsv`, `ARCHIVE_DONE`), `upload` (private dataset repo `<user>/sn56-image-<tourn_id>`, idempotent), `backfill`, `watch` (poll for a new image tournament, then fetch + upload). Every subcommand has a `--dry-run` that does zero network and zero disk writes |
| `test_hf_archive_tournament.py` | regression tests (stdlib, no network): a dry run never marks a tournament archived, a failed upload never does, a failed tick retries, a dry run writes no byte, the dataset repo is always private |
| `hf_cleanup.py` | inventory / plan / apply-with-confirmation for one HF account's repos, plus `prune` of files inside a repo; repos matching `--protect-regex` (default `sn56-image-`, the archives) are always kept |

## Prerequisites

- python3 >= 3.8 (stdlib) for `fetch` / dry runs / `watch`; `pip install -U huggingface_hub` for real `upload` /
  `backfill` / `hf_cleanup.py`; `curl` on the PATH (downloads).
- A Hugging Face **write** token in a file: `umask 077; cat > ~/.hf_env` then one line `HF_TOKEN=hf_...` (or
  `--token-file`, or the env `HF_TOKEN`). Never on a command line, never printed.
- `SN56_ARCHIVE_ROOT` (default `~/sn56-archives`) - one `tourn_<id>_<yyyymmdd>/` dir per tournament.

## Run

```bash
python3 hf_archive_tournament.py fetch  tourn_<hex>_<yyyymmdd>        # as soon as the tournament exists (7-day window)
python3 hf_archive_tournament.py upload tourn_<hex>_<yyyymmdd>
python3 hf_archive_tournament.py backfill --dry-run                   # list what is on disk and would be uploaded
screen -S watch python3 hf_archive_tournament.py watch --interval-min 15   # extra safety net, on an always-on machine
python3 test_hf_archive_tournament.py
python3 hf_cleanup.py list && python3 hf_cleanup.py plan --older-than-days 14 && python3 hf_cleanup.py apply   # dry run
```

`watch` is an extra net, never the only one: its first signal (`GET /tournament/latest/details`) was observed to lag
behind a running tournament (INFERRED: it may report the latest COMPLETED one), and its second (`/auditing/tasks`) only
sees a tournament once some repo is recorded. Fetch by hand as soon as you know a tournament runs.

The archive layout (`tasks/<round>_<family>_<task8>/`) is what `../replica/` stages; `../replica/archive_layout.py`
gives the box copy its `<task_id>` links.

## Not included

No archive, no task id, no tournament id and no account name. Trained MODEL repos are not this tool's business: the
validator's uploader makes them public itself (G.O.D `trainer/containers/uploader.py:190`). `hf_cleanup.py apply`
deletes repos IRREVERSIBLY and only with `--yes --confirm <your account name>`.
