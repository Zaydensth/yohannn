# Forfeit modes — read this before touching training

> `file:line` re-checked against `gradients-ai/G.O.D@b49ba86` on 2026-10-09. Re-check again
> before relying on a line number.

Ranked by cost. Everything in Tier 1 scores zero regardless of how good the policy is.

## TIER 1 — total loss

### 1. Unmapped SGLang tool-call parser — the #1 environment-specific zero
If the served model id does not match a known family substring, SGLang returns tool calls as
**plain text** and **every single turn forfeits**. Known families map from the model id, with
`config.json` `model_type` as a fallback: `qwen3-coder`, `hermes`, `qwen3`/`qwen2`/`qwen` →
`qwen25`, `llama` → `llama3`, `mixtral`/`mistral` → `mistral`; the `SGLANG_TOOL_CALL_PARSER`
env var overrides it on the validator side.
`core/pvp/sglang_parsers.py:1-31`

**Detect:** every turn a forfeit, from turn one, across every game.
**Prevent:** verify your served repo id resolves to a parser before submitting. A rename that
drops the family substring from the model id is enough to zero the run.

Extra trap: for a plain (non-continuation) LoRA in PvP the parser is **not** resolved in
`_prepare_model` — `validator/evaluation/pvp/__main__.py:93` passes
`tool_call_parser_for(base_path) if spec.base_chain else None`, i.e. `None` for a normal LoRA
submission, and it is rescued only later.

### 2. Per-turn forfeits
The forfeiting seat gets the game's **MIN** utility and the opponent **MAX**
(`core/pvp/game_eval.py:80-87`, `:105-127`). Triggers:
- no legal `game_action` in the response
- **> 30 s wall clock on the turn** (SIGALRM)
- inference timeout after retries
- context overflow

### 3. The 512-token output cap
`PVP_TURN_MAX_TOKENS = 512` must cover **memory edits and the move**. A truncated tool call
parses to no move and forfeits. The constant carries that exact warning in-source.
`core/pvp/constants.py:28-33`

### 4. Training container did not report success
Only hotkeys whose container reported `status == "success"` are evaluated at all. A missing HF
repo scores zero before a single game is played.
`validator/scoring/tasks.py:838-844` (training-status filter), `:612-622` (repo not found)

### 5. SGLang cannot serve your repo in time
1800 s health timeout or the eval container dies. Individual-env sessions additionally cap at
4 h, per-task timeout 150 s, concurrency 4; any per-task exception scores 0.0 for that task.
`validator/evaluation/evaluators/environment.py:576`, `:606-611`;
`validator/evaluation/constants.py:46-52`

### 6. Eval retries exhausted
`MAX_TOURNAMENT_EVAL_ATTEMPTS = 3`. An exhausted PvP pair fails the whole task's evaluation for
those miners; an exhausted individual-env hotkey is written `score = 0.0`.
`validator/scoring/constants.py:8`

## What is NOT a forfeit — do not over-engineer against these

- **Infra failure.** A connection-refused server raises `ModelUnreachableError` **past** the
  forfeit handlers; the runner waits for both servers and **replays** the game, crediting the
  wait back to the 900 s budget. But a model that is merely **slow** *is* a forfeit.
  `core/pvp/bot.py:101-112`, `:251-269`
- **`EmptyLegalActionsError` is scored a DRAW**, not a forfeit.
  `core/pvp/game_eval.py:125-127`

## Matchup-level early termination

10 consecutive losses in the opening window (20 thereafter), or 10 episode forfeits, ends the
matchup. Both are invoked with `remaining=0`, so they only break the loop — no phantom games
are awarded. Checks run at **pair boundaries only**, so a matchup always ends on an even game
count. `validator/evaluation/pvp/game_runner.py:148-190`, call sites `:330-341`; thresholds
`validator/evaluation/constants.py:115-117`

**Draws reset both consecutive-loss counters** (`game_runner.py:304-312`). Given the
`(wins + 0.5·draws) / games` win rate, a policy that can force draws breaks a losing streak and
never trips early termination — a real defensive strategy, not a curiosity.

## Triage order when a run scores badly

1. Did the container exit success and did the repo upload?
2. Did the tool-call parser resolve? (check for text-not-tool-call in logs)
3. Were turns forfeiting — and from which turn?
4. Did any output hit the 512-token cap?
5. Was the matchup early-terminated?
6. Only now consider policy quality.
