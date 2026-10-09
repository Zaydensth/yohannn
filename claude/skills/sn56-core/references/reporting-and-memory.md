# Playbook — summary, memory & alerts (close the loop)

Every unit of work ends by recording what you learned and telling the user what matters.

## Milestone summary (the report format)
Keep it scannable — the user is often async:
- **DONE + verified**: what changed and the measurement that proves it (numbers, not
  adjectives).
- **NEXT**: the ordered next steps.
- **NEEDS USER**: decisions or actions only they can do (esp. "GPU worth turning on now",
  a push, a submit).
- Cite `file:line`/URLs. Distinguish VERIFIED vs INFERRED. Never claim done without a
  measurement.

## Update the second brain (do this as you go, not just at the end)
Memory lives **outside the submission repo**, in your private memory vault (`<VAULT>`, a separate
private git repo). See `~/.claude/CLAUDE.md` §5 (memory).
- Save the **non-obvious** into the right `§`-box: a result + its by-code root cause, a
  tuned param, a user correction (with why + how-to-apply), a new external reference. One
  fact per file; update, don't duplicate; delete what's proven wrong; link with `[[name]]`.
- The most valuable memories are **post-mortems**: "we won/lost X because <root cause
  verified in code>, fix = <what + measured result>." That's what makes next tournament
  faster.
- Keep `MEMORY.md` as a one-line-per-memory index (loaded every session). Prune stale
  hooks.

## Alerts (when the user is away)
- Use **PushNotification** for things worth interrupting for: a long run finished, a gate
  passed/failed, a blocker that needs them (VPS down, decision required). One line,
  actionable.
- Don't notify for routine progress — that's what the milestone summary is for.

## Cadence
- Small task: summary + memory at the end.
- Long autonomous run: summary at each milestone, memory continuously, one closing note
  when the loop ends.
