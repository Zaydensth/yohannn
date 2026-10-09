# Playbook — self-driving loop (pacing, monitoring, self-prompting)

How the agent keeps working without being hand-held, while still checking in when it
matters.

## When to self-drive vs check in
- **Self-drive (just do it)**: obvious improvements, verified bugfixes, offline prep,
  gathering data, preparing variants. Explain after, don't ask before.
- **Check in first**: anything irreversible/outward (push, on-chain submit, delete, send),
  a decision that's genuinely the user's (which repo, which strategy trade-off), or when
  the data contradicts the plan.
- Report at **milestones**, not every tool call. A good update = what's DONE+verified,
  what's NEXT, what needs the user (esp. "GPU worth turning on now").

## Recurring monitoring — use /loop, don't babysit
- `/loop <interval> <prompt>` runs a prompt on a schedule (e.g. watch a training run every
  15 min).
- `/loop <prompt>` with no interval = self-paced: you decide the next wake time each turn.
- Inside a self-paced loop, arm a **Monitor** for the event you're waiting on (log line,
  file change, task finish) so you wake on the event, not just a timer; pick a long
  fallback heartbeat so quiet ticks are rare.
- Background tasks (Workflow/Agent/long Bash) **notify you on completion** — don't poll
  them with short timers; schedule a long fallback and react to the notification.
- End the loop (`ScheduleWakeup stop`) when the work is done or can't progress; send one
  closing note.

## A typical autonomous session
```
1. Observe: pull results/logs/state (APIs, git, VPS, dataset output).
2. Orient: reconcile vs validator code; update memory with what's new.
3. Decide: highest-leverage next step; prepare variants offline.
4. Act: implement + VERIFY on CPU; for GPU work, batch it into one dense session.
5. If waiting on a long job: arm a Monitor + schedule a fallback wake; stop babysitting.
6. At a milestone: summarize (done/next/needs-user), update memory, optionally notify.
7. Loop — or check in if a decision is the user's.
```

## Budget discipline (the reason self-driving matters here)
- Idle GPU = wasted money. **Prepare everything offline first**, then turn the GPU on for
  exactly the train+eval you planned. Tell the user "everything's ready, VPS is worth
  turning on now" — don't spin a GPU to think.
- The tournament's own wall-clock budget is the harder constraint: a run that overruns is
  a **forfeit**, which no amount of image quality offsets. Treat budget arithmetic as
  correctness-critical code, not tuning.
