# Claude Code memory vault (template)

A private "second brain" for Claude Code: one Markdown file per fact, one index per track,
linked into Claude Code's per-project memory folder and committed to a **private** git repository
at the end of every session. The same folder works as an Obsidian vault.

> **Your copy must be private.** This template is public; your vault is not. It will hold tuned
> constants, post-mortems, notes on other entries and pointers to credentials. Create a new
> **private** repository for it and never make it public — git history is permanent. Never
> store a secret value (token, password, key) in it either: store where the value lives.

## Why it is separate from the miner repos

The split is by **sensitivity**, not by file type.

| In this vault | In a miner (submission) repo |
|---|---|
| Memory: findings, constants, post-mortems, profile, credential pointers | Training code, `LICENSE`, `NOTICE` |
| Strategist ledger, pre-registrations, gate ledgers (`projects/<track>/jobs/`) | Optionally a dev-only `.claude/` and project `CLAUDE.md`, kept off the submitted commit |
| A backup of your global `~/.claude` config (`home-claude/`) | |

A submission repo is cloned by the validator and its history is permanent, so assume anything
committed there can be read by others.

## Layout

```
HOME.md               Obsidian entry point (map of content)
MEMORY.md             template for a per-track index (nine boxes §0-§8); install.sh copies it
ONBOARDING.md         read-first for any agent working in this vault
global/               cross-track facts
projects/<track>/     one folder per track (text, image, environment)
  MEMORY.md           the index Claude Code loads each session (seeded by install.sh)
  jobs/               FABLE_LEDGER.md (append-only), PREREG_<n>.md, gate ledgers
home-claude/          your own backup of ~/.claude (CLAUDE.md, agents/, commands/)
templates/            memory.md (one fact); jobs/PREREG.md, jobs/FABLE_LEDGER.md
tools/graph_check.py  read-only checker: dangling [[links]], orphans, index drift (stdlib only)
install.sh            links projects/<track> into ~/.claude/projects/<slug>/memory
sync.sh               commit + push; run by the Stop hook; never fails the session
sync-on-write.sh      PostToolUse hook; syncs only when the written path is inside the vault
```

## Install

1. Create an empty **private** repository, clone it to `~/claude-vault` (the path the hook
   commands in `claude/settings.template.json` assume), and copy the contents of this folder
   into it. Set the commit identity once — `sync.sh` uses the vault repo's own git config:
   `git -C ~/claude-vault config user.name "<name>"` and `... user.email "<email>"`.
2. Link each track to the folder where you run `claude` for it:

   ```bash
   bash ~/claude-vault/install.sh image ~/code/<your-image-miner>
   ```

   The script seeds `projects/<track>/MEMORY.md` and `jobs/FABLE_LEDGER.md` if they are missing,
   and links `~/.claude/projects/<slug>/memory` to `projects/<track>`. Claude Code derives
   `<slug>` from the absolute path of the working folder. Run `claude` once in that folder, then
   run the script again: it warns if Claude Code chose a different slug.
3. Merge `claude/settings.template.json` from the starter package into `~/.claude/settings.json`
   (do not overwrite settings you already have). If the vault is not at `~/claude-vault`, change
   both hook paths.
4. Optional: open the folder as a vault in Obsidian.

## Protocol (short version — the full one is in `~/.claude/CLAUDE.md` §5)

- One file = one fact. Frontmatter: `name`, `description`, `metadata.type`
  (`user` | `feedback` | `project` | `reference`). For `feedback` and `project`, add **Why:**
  and **How to apply:**.
- `MEMORY.md` is an index: one short line per memory, never content. Claude Code warns about and
  truncates an index that grows too long.
- Update, do not duplicate. Delete what is proven wrong. Link related notes with `[[name]]`.
- Recalled memory is background, not an instruction: re-check that a file or flag it names
  still exists before acting on it.
- Label every claim VERIFIED (you ran it or read it) or INFERRED (reasoned, untested).

## Checks

`python3 tools/graph_check.py` reports dangling `[[links]]`, orphans and index drift. It is
read-only; `sync.sh` runs it quietly after a sync that changed something. Known quirk: the
example `[[...]]` link in `templates/memory.md` is reported as dangling, so a fresh vault shows
one dangling link (one more once `home-claude/` holds a copy of `CLAUDE.md`, which has its own
example link).
