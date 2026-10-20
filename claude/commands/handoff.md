---
description: Write a handoff for the next Claude account/session (state, exact resume commands, rules, newest memory list, copy-paste prompt), push the vault, and secure every session pod.
---

Write a handoff so that a **different Claude account** can continue this work without guessing. Use it when the
user switches accounts, when the usage limit is reported ≥ 96 %, or at the end of a long autonomous run.

Steps, in order — do not skip, do not reorder:

1. **Stop spending first.** Stop parallel workflows/subagents (`TaskStop`); start no new agent. The rest of the
   quota is for this handoff.
2. **Observe the real state** (never from memory): `date -u`; every pod this session created (status, rows done,
   what is running, the session-independent watchers and their deadlines, the one-line stop command); result
   files; balance and rate; open loops with other agents/people.
3. **Write** `<vault>/projects/<track>/jobs/HANDOFF_<yyyymmdd>_<slug>.md` from
   `<vault>/templates/jobs/HANDOFF.md`: real state with timestamps, exact resume commands, rules in force, the
   list of memory files changed in the last 48 h, and the copy-paste prompt for the next account (resume keyword
   `/lanjut-<track>-<yyyymmdd>`). Label VERIFIED/INFERRED. No secrets — pointers only.
4. **Index it**: one line in `projects/<track>/MEMORY.md` §8 ("read first").
5. **Push the vault** (`bash <vault>/sync.sh`), then verify `git log origin/main -1` shows the commit. A handoff
   that is not pushed does not exist for the next account.
6. **Secure the pods**: each session pod is STOPPED (evidence copied first) or supervised by watchers that do not
   depend on this session; say which, with the stop command.
7. **Report** DONE / NEXT / NEEDS USER in a few lines and give the user the copy-paste prompt. Then stop.
