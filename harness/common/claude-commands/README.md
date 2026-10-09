# claude-commands — slash commands for the harness (method only)

Six Claude Code slash commands that drive the harness tools. They hold **method** (order of work, what counts as
evidence, what must never be done) and no project-specific numbers. They are written in Indonesian, like the rest of
the Claude instruction files in this package.

| file | slash command | what it does | pairs with |
|---|---|---|---|
| `gates.md` | `/gates [sha]` | resolve the SHA, run the gate-auditor agent (G0-G4), show its ledger lines verbatim | agent `gate-auditor`, `../templates/RUNBOOK-ab-template.md` |
| `intel.md` | `/intel <tourn_id>` | pull a tournament's public record, store VERIFIED findings in memory | agent `tournament-intel`, `../intel/pull_tournament.py` |
| `lever.md` | `/lever <name> <what>` | add one env lever end to end (name check, explicit default, provenance line, CPU test) | your trainer code |
| `logdig.md` | `/logdig [log]` | read a run's signals in a fixed order; "absent" is reported as unknown, never as healthy | `/postmortem-loss` |
| `postmortem-loss.md` | `/postmortem-loss <task or symptom>` | one loss -> numbers -> causal chain -> silent-failure checklist -> failing test -> minimal fix | `/lever` |
| `smoke.md` | `/smoke [sha]` | contract gate + container smoke test on the exact SHA -> SAFE / DO NOT SUBMIT / INCONCLUSIVE | `../check-repo-contract.sh`, `../pre-submit-smoke-test.sh` |

**Install:** copy them into the `.claude/commands/` of the workspace where you run Claude Code (NOT into your miner
repo's submission tree), and export `SN56_HARNESS=<path to this harness dir>` so the commands find the tools.
`postmortem-loss.md` is deliberately not named `postmortem.md`: a separate tournament-level `/postmortem` command may
exist in your `.claude/commands/`.

Nothing here pushes, registers or submits: those stay the user's actions.
