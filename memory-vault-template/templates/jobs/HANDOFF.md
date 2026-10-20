# HANDOFF <yyyy-mm-dd> (<reason: account switch | usage limit | end of day>) — resume keyword: `/lanjut-<track>-<yyyymmdd>`

Written <date -u> / <local time> by session <id> (<model>). The next account/session reads, in this order:
`~/.claude/CLAUDE.md` → `projects/<track>/MEMORY.md` + `global/MEMORY.md` → the memory files changed in the last
48 h (list them below) → this file → the tail of the ops log → the RESULTS section of the locked pre-registration.

## 1. Real state (VERIFIED, with timestamps)
- Program / locked plan: `<PREREG file>`, ledger lines `<n–m>`, measurement tree `<sha>`, ship tree `<sha>`,
  cutoff `<UTC>`. What may still be edited (RESULTS append only).
- Pods: `<X> <id>` status (RUNNING/STOPPED), rows done `<k/n>`, what is running now, the **session-independent
  watchers** that exist (deadline, idle, money) and the **one-line stop command**.
- Results so far: the paired table (arm vs control, per seed/task), witnesses, what is still pending.
- Archives / datasets / artifacts: where they are (private HF repo name, local path), HEAD/commit.
- Open loops with other agents or people (e.g. relay packages waiting for an answer), with the file paths.
- Balance / budget state and the rate per hour.

## 2. Exact commands to resume (copy-paste, in order)
1. `date -u`; observe the pods and result files (commands).
2. The read-out / next gate, with the locked formula and thresholds.
3. The next consult point for the strategist (POINT=lock|rescope|gonogo|postmortem) and what evidence to bring.
4. Anything deferred (with the file that explains why).

## 3. Rules in force (one line each)
§0 guardrails; push policy; what must never be computed/read; what the user decided this cycle (quote with date).

## 4. Memory files changed recently (so the next agent reads the newest facts first)
- `<file>` — one-line hook
- …

## 5. Copy-paste prompt for the next account
```
/lanjut-<track>-<yyyymmdd> — continue <program> from the previous account's handoff.
1. Read ~/.claude/CLAUDE.md (§0, §4, §5), then the NEWEST memory: <vault>/projects/<track>/MEMORY.md, global/MEMORY.md and these files: <list>.
2. Read jobs/HANDOFF_<yyyymmdd>_<slug>.md, the ops log tail, and the RESULTS of <PREREG>.
3. OBSERVE first (date -u, pod status, result files); never assume.
4. <the next concrete step>; label VERIFIED/INFERRED; save every new fact to memory and push.
5. On a usage-limit warning ≥ 96 %: stop parallel agents, write a new handoff, STOP session pods, halt.
```
