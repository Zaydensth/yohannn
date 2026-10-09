---
description: Full post-mortem of a finished tournament — what cost us, ranked, with by-code root causes.
---

Do a full post-mortem of the tournament that just finished. Never fabricate a score, a rank,
or a log line — if a query returns nothing, say so.

1. **Pull every image task my hotkey participated in** from
   `api.gradients.io/auditing/tasks/<task_id>`: model type, base model, `hours_to_complete`,
   status, final ranking and scores. Reconstruct the group standings.

2. **For each of my runs, resolve the config from all three sources and cross-check them**:
   the container logs on Grafana/Loki (the process-spawn line carries the full command
   line), our own commit at the submitted SHA, and the uploaded model repo itself
   (`adapter_config.json`, the weight shards, and any trainer state). Where the three
   disagree, that disagreement *is* a finding — it means a runtime fallback fired.

3. **For each task, resolve the winner's artifact too** and diff it against mine. Check
   FIRST whether their run finished its schedule and which checkpoint they shipped — that
   often explains the result better than their hyper-parameters do.

4. **Triage each loss in this order**, and stop at the first one that explains it:
   1. No valid artifact / non-zero exit / empty upload
   2. Wall-clock overrun or truncation — what fraction of planned steps completed
   3. Wrong checkpoint promoted (the overfit tail scored instead of the best one)
   4. Step count wrong for the task shape — too many or too few for the metric the validator
      uses now (read the evaluator code; do not assume a direction)
   5. Train/eval distribution mismatch (rendered at settings we did not train for)
   6. Our offline proxy disagreed with the returned score
   7. Hyper-parameter quality

5. **For every difference, state the MECHANISM it implies** — not just which number was
   bigger — and mark it PROVEN or HYPOTHESIS. Verify each mechanism against the validator
   code before you believe it.

6. **Report**: what actually cost us, ranked by cost; what is worth changing, ranked by
   expected value; and what you could NOT resolve — be explicit about the gaps.

7. **Save the durable findings to memory** (outside this repo — see the global
   `~/.claude/CLAUDE.md` §5) with their evidence. Do not save anything you could not verify.
