# Home — memory vault

Entry point of this vault (an Obsidian "map of content"). Wiki links can be followed directly
in Obsidian.

## Start here

| | |
|---|---|
| `global/MEMORY.md` | cross-track facts: profile, working style, credential *pointers*, upstream (create it with the first global fact) |
| `projects/<track>/MEMORY.md` | one index per track — `text`, `image`, `environment` (seeded by `install.sh`) |
| [README](README.md) | why this vault is kept apart from the miner repos, and how to install it |
| [ONBOARDING](ONBOARDING.md) | read-first for any agent that works in this vault |

## Iron rules (read before acting)

_Empty. Link one note per rule here once you have written it — for example: unknown credentials
are asked for, never invented · the public-repo boundary · every claim labelled VERIFIED or
INFERRED · no "done" without a number · offline first, one change per A/B._

## Often-needed context

_Empty. Link your most-used notes here._

## Adding a memory

Copy `templates/memory.md`. One file = one fact. Put it in `global/` if it holds on every
track, otherwise in `projects/<track>/`. Add one line to that folder's `MEMORY.md` — that index
is what Claude Code loads at the start of every session.
