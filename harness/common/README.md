# common — tools shared by both tracks

| path | what | verified how |
|---|---|---|
| `intel/pull_tournament.py` | one command pulls a tournament's public record (details, task jsons, HF small files of the top N + yours, optional Loki and presigned data) and writes `summary.md` with per-task standings and the small-tournament standing replicated from `round_results.py:1067-1128` (lower-is-better, GrpoTask/EnvTask higher-is-better as `scoring/tasks.py:165-175`) | ranking re-checked against G.O.D b49ba8644a0d; offline unit check of the ordering |
| `dedup_local.py` | local replica of the validator's T0/T1 dedup (identical commit / identical normalised source) + the exact T2 judge prompts as a bundle you or an agent can answer without an API key | upstream files byte-identical at b49ba8644a0d; T2 prompts equal to `repo_dedup_config.json`; smoke-tested on toy repos |
| `check-repo-contract.sh` | hygiene gate on a commit: ref resolves, canary self-test of every pattern, no weights / datasets / tokens / IPs / SS58 hotkeys, LICENSE + NOTICE byte-identical to upstream, the image's COPY set and the tree surface (enforced when you set the allow-lists) | run on clean and leaky synthetic repos |
| `pre-submit-smoke-test.sh` | TEMPLATE gate on the exact SHA: contract -> fresh no-cache build from a clean worktree (timed vs 30 min) -> entrypoint -> imports in the production interpreter -> CLI -> one real optimizer step -> CPU suite; a stage that did not run makes the verdict INCONCLUSIVE, never PASS | control flow exercised with a docker stub (not a real build) |
| `run-suite.sh` | honest test-suite classifier: SKIP only on exit 77 or a `^SKIP:` line, everything else that fails is FAIL; an empty suite is refused | run on synthetic tests |
| `verify-fixes-present.py` + `fixes.example.tsv` | TEMPLATE: marker strings prove each consequential fix is still in the ref you submit (selective merges drop fixes silently); an empty marker list is refused | run on a synthetic repo |
| `monday_checks.py` | pre-registration checks, repo-agnostic: upstream contract diff since your last reviewed commit, production image digests, upstream constants you depend on, LICENSE/NOTICE on your DEFAULT branch, pinned HF build assets, PyPI drift of unpinned deps, ship SHA on the branch; prints (never runs) the push | run against public endpoints and synthetic repos |
| `templates/` | the empty A/B runbook | - |
| `claude-commands/` | six Claude Code slash commands that drive these tools (Indonesian, method only) | - |

## Prerequisites

bash, python3 >= 3.8 (stdlib), git, curl; docker for `pre-submit-smoke-test.sh`. Network only where a tool says so
(GitHub / Docker Hub / PyPI / HF / the tournament API - all anonymous, read-only). Loki access (`SN56_GRAFANA_HOST`,
`SN56_LOKI_DS`) is optional and never stored in a repo.

## Rules these tools assume

- Keep them OUT of your miner repo's submission tree: the validator clones the whole repo, and dev tooling is
  anti-obfuscation and dedup surface for no benefit.
- Tokens only from 0600 files or the environment, never on a command line, never printed.
- Nothing here pushes, registers or submits. Those are the user's actions.

## Not included

No hotkeys, no task or tournament ids, no measured thresholds, no plans or results. Everything that depends on your
miner (dockerfile path, COPY allow-list, import list, build markers, fix markers) is an argument or an env variable.
