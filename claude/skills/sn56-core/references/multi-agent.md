# Playbook — research, analysis & review (multi-agent workflows)

When a task is bigger than one context — diagnose a loss, design a fix, audit a diff,
research a topic — fan it out with the **Workflow** tool (deterministic orchestration of
subagents) or **Agent** (a single subagent). Scout inline first to build the work-list,
THEN orchestrate.

## Core patterns (compose freely)
- **Fan-out → synthesize**: N parallel readers each cover one subsystem/angle → one
  synthesizer ranks findings.
- **Adversarial verify**: for every finding, spawn skeptic(s) prompted to REFUTE it; keep
  only what survives. Prevents plausible-but-wrong conclusions (the #1 failure mode).
- **Judge panel**: generate N independent solutions from different angles → score with
  parallel judges → synthesize from the winner, grafting the best of the rest.
- **Loop-until-dry**: for unknown-size discovery, keep spawning finders until K rounds
  return nothing new.
- **Completeness critic**: a final agent asks "what's missing / unverified?" — its answer
  is the next round.

## Diagnose → design (the two-phase shape that works)
1. **Diagnose workflow**: parallel deep-reads (how eval/scoring works · what our code
   actually does · forensics on winners/results) → synthesis of **ranked, cited root
   causes**. Read it, confirm, before designing.
2. **Design workflow**: research the fix space → judge candidate approaches →
   **adversarially refute** the top pick → emit a concrete, file-level implementation spec
   + an **offline verification checklist**.

Run them as separate workflows so you stay in the loop between phases.

## Rules that keep it honest
- Every claim carries a **`file:line` or URL** citation. Mark **VERIFIED vs INFERRED**.
- When two agents disagree, **resolve from the code yourself** before acting — do not
  average guesses.
- Have agents return **structured output** (a schema) so results compose without parsing.
- Scale effort to the ask: a quick check = a few agents; "audit thoroughly" = larger pool
  + adversarial pass.
- Subagents run in isolated context — brief each one completely. They cannot see this
  conversation.

## Model routing
See the fleet in `~/.claude/agents/` and the routing rules in `~/.claude/CLAUDE.md`. Cheapest model
that can be correct; always verify a worker's output before trusting it.

## Code review
- Use the **/code-review** skill on a diff before committing anything nontrivial
  (correctness + simplification).
- For a fix you designed, re-run the offline verification checklist and only then call it
  done.
