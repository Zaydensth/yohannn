# AUTOMATION — how the agent drives itself

This is the operational layer on top of `CLAUDE.md`. It maps every recurring job to the
tool/skill that does it, and defines the self-driving loop. Placeholders (`<VPS_IP>`,
`<BASE_MODEL>`, `<VALIDATOR_IMAGE>`, `<HOTKEY>`) → fill from your own `user`/`project`
memory (which lives outside this repo, in your private memory vault — see the global
`~/.claude/CLAUDE.md` §5).

## The master loop (OODA, self-paced)
```
OBSERVE   pull the real state (API/logs/git/VPS/dataset output) — never assume
ORIENT    reconcile against the validator code (gradients-ai/G.O.D main)
DECIDE    pick the highest-leverage next action; prepare MULTIPLE variants offline
ACT       do it; VERIFY by data (CPU-first); record what you learned
LOOP      repeat; check in with the user at milestones + when a decision is theirs
```
Guardrail: **maximize CPU/offline work before touching a GPU** so no GPU sits idle waiting
for you to write code. Prepare all variants, then run one dense GPU session.

## Job → tool map (what to reach for)
| Job | Use | Playbook |
|---|---|---|
| Heavy analysis / design / audit | **Workflow** tool (fan-out subagents → adversarial verify → synthesize) | `sn56-core`/multi-agent |
| One focused sub-task in parallel | **Agent** tool (`executor` / `worker-sonnet`) | `sn56-core`/multi-agent |
| Plan lock, re-scope, go/no-go, post-mortem — or a genuinely hard call | **Agent** tool → `strategist` (mandatory at the four points in `~/.claude/CLAUDE.md` §4; one short round, one ledger line) | `sn56-core`/multi-agent |
| Who-won / rankings / task facts | `api.gradients.io/auditing/tasks/<id>` | `sn56-core`/intel-sources |
| What a miner actually ran at runtime | public Grafana/Loki container logs | `sn56-core`/intel-sources |
| Upstream changes before they land | watch `gradients-ai/G.O.D` main + branches | `sn56-core`/intel-sources |
| Trained-artifact forensics | HF model repo files (`adapter_config.json`, weights) | `sn56-core`/intel-sources |
| Recurring monitor / poll | **/loop** skill (interval or self-paced) + **Monitor** | `sn56-core`/self-driving |
| VPS: connect/install/smoke/train/eval | Bash over SSH + docker | `vps-operations.md` |
| Review a diff before committing | **/code-review** skill | `sn56-core`/multi-agent |
| Summary / memory / alert | memory files + **PushNotification** | `sn56-core`/reporting-and-memory |
| Verify a change end-to-end | run it (CPU asserts, then GPU) — never claim "done" unmeasured | `vps-operations.md` |

## Non-negotiables (repeat of the guardrails that bite in automation)
- **Verify by data, never fabricate.** VERIFIED (ran/read) vs INFERRED, always.
- **Read the validator code** before claiming any mechanic; what this repo emits must match
  what eval parses.
- **VPS power is the user's** — never power-off; ask. **Never submit the on-chain fee** —
  the user does that.
- **Don't push to GitHub** unless it's a validated fix AND approved. **LICENSE/NOTICE stay
  byte-identical to upstream.**
- **Keep secrets out even though the repo is private.** Git history is permanent and a
  submission repo may have to be opened. No tuned constants, competitor intel, hotkeys, VPS
  IPs, or post-mortems in tracked files — those go to the private memory vault.
- Prefer **/loop + Monitor** to babysitting; report at milestones, not every step.
