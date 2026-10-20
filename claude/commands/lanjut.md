---
description: Resume a program from the previous account's/session's handoff — read the memory index, the newest memory files and the handoff, observe the real state, then continue without re-deciding what was already decided.
argument-hint: "<track> <yyyymmdd> (the handoff to resume from)"
---

Resume work that another account or session handed off. The handoff is the contract; the memory is the context;
the real state is what you observe now. Do these in order, and say which step you are on:

1. **Read, in this order, before any tool that changes anything:**
   `~/.claude/CLAUDE.md` (§0 guardrails, §4 gates, §5 memory) → `<vault>/projects/<track>/MEMORY.md` and
   `<vault>/global/MEMORY.md` → every memory file changed in the last 48 h
   (`git -C <vault> log --since='2 days ago' --name-only --format=`) → `projects/<track>/jobs/HANDOFF_<yyyymmdd>_*.md`
   (the newest) → the tail of the ops log it names → the RESULTS section of the locked pre-registration it names.
2. **Observe the real state** — never from the handoff alone: `date -u`; status of every pod/box it lists (REST or ssh,
   with the DNS workaround if the handoff says so); the result files; the balance; the watchers that should be running.
   Write one line per difference between the handoff and reality.
3. **Do not re-open decisions** the locked plan or the user already made (quoted in the handoff). Continue from the
   "exact commands to resume" list. If reality contradicts the plan, that is a RE-SCOPE point for the strategist, not a
   reason to improvise.
4. **Report** DONE / NEXT / NEEDS USER in a few lines, with VERIFIED/INFERRED labels and two-zone times, then continue.
5. **Save memory as facts appear and push**; if the usage limit is reported ≥ 96 %, run `/handoff` and stop.
