#!/usr/bin/env bash
# install.sh - put this kit's Claude Code configuration into ~/.claude, and optionally lay out a memory vault.
#
#   bash install.sh [--dry-run] [--vault <path>]
#
#   -n, --dry-run    show what would change; write nothing
#   --vault <path>   also copy memory-vault-template/ to <path>, your PRIVATE memory vault (never overwrites a file)
#   -h, --help       show this text
#
# Idempotent, no network, no sudo, bash 3.2+ (the /bin/bash of macOS is enough).
#  1. Compares claude/{CLAUDE.md,agents/,skills/,commands/} with ~/.claude. If nothing differs, nothing is written.
#  2. Before the first write, copies whichever of ~/.claude/{CLAUDE.md,agents,skills,commands,settings.json} exist
#     to ~/.claude.bak-<timestamp>/ and records in MANIFEST.txt there what this run adds or replaces. Those are the
#     only paths it writes under ~/.claude: sessions, projects/, caches and ~/.claude.json are never touched.
#  3. Copies the kit's files over them. Files of your own in agents/, skills/ and commands/ are kept.
#  4. settings.json is created from claude/settings.template.json ONLY when ~/.claude/settings.json does not exist
#     (with --vault, its two hook commands point at that vault). An existing settings.json is never modified: a
#     suggested merge is printed as a diff for you to apply.
#  5. Checks the result: every kit agent installed, with a known `model:` line and `effort: max`.
# Not installed: claude/project/ and harness/common/claude-commands/ (per-workspace files; see README.md).
set -euo pipefail

say()  { printf '%s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die()  { printf 'install.sh: %s\n' "$*" >&2; exit 2; }
usage() { awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "${BASH_SOURCE[0]}"; }

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
SRC="$HERE/claude"
VTPL="$HERE/memory-vault-template"
TPL="$SRC/settings.template.json"
[ -n "${HOME:-}" ] || die "HOME is not set"
DEST="$HOME/.claude"
SETTINGS="$DEST/settings.json"
DEFAULT_VAULT="$HOME/claude-vault"
MODEL_RE='^model: (claude-fable-5-1|claude-opus-5-5|claude-sonnet-5)$'
STAMP="$(date +%Y%m%d-%H%M%S)"

DRY=0
VAULT=""
while [ $# -gt 0 ]; do
  case "$1" in
    -n|--dry-run) DRY=1; shift ;;
    --vault)
      if [ $# -lt 2 ] || [ -z "$2" ]; then die "--vault needs a path"; fi
      VAULT="$2"; shift 2 ;;
    --vault=*)
      VAULT="${1#--vault=}"
      if [ -z "$VAULT" ]; then die "--vault needs a path"; fi
      shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1 (try --help)" ;;
  esac
done

# ---------------------------------------------------------------------------------------------------------------------
# helpers

# absolute, symlink-resolved form of a path that may not exist yet
abs_path() {
  local p="$1" rest=""
  case "$p" in /*) ;; *) p="$PWD/$p" ;; esac
  while [ ! -d "$p" ]; do
    rest="/$(basename "$p")$rest"
    p="$(dirname "$p")"
  done
  p="$(cd "$p" && pwd -P)"
  if [ "$p" = "/" ]; then p=""; fi
  case "$rest" in */..|*/../*) die "use a path without '..': $1" ;; esac
  printf '%s%s\n' "$p" "$rest"
}

# prints the first symlink on the way from $DEST to $DEST/<rel> (DEST itself excluded); fails if there is none
symlink_on_path() {
  local rest="$1" acc="$DEST" part
  while [ -n "$rest" ]; do
    part="${rest%%/*}"
    if [ "$part" = "$rest" ]; then rest=""; else rest="${rest#*/}"; fi
    acc="$acc/$part"
    if [ -L "$acc" ]; then printf '%s\n' "$acc"; return 0; fi
    if [ ! -e "$acc" ]; then return 1; fi
  done
  return 1
}

# python3 that will not pop up the macOS "install command line tools" dialog
have_python() {
  local py
  py="$(command -v python3 2>/dev/null)" || return 1
  if [ "$(uname -s)" = "Darwin" ] && [ "$py" = "/usr/bin/python3" ] && ! xcode-select -p >/dev/null 2>&1; then
    return 1
  fi
  python3 -c 'import json' >/dev/null 2>&1
}

count_lines() { wc -l < "$1" | tr -d ' '; }

# ---------------------------------------------------------------------------------------------------------------------
# preconditions

for need in "$SRC/CLAUDE.md" "$SRC/agents" "$SRC/skills" "$SRC/commands" "$TPL"; do
  [ -e "$need" ] || die "incomplete kit: $need is missing (run me from a full checkout of the kit)"
done
if [ -n "$VAULT" ] && [ ! -d "$VTPL" ]; then die "incomplete kit: $VTPL is missing"; fi
if [ -e "$DEST" ] && [ ! -d "$DEST" ]; then die "$DEST exists and is not a directory (nothing was written)"; fi
if [ -d "$DEST" ] && [ "$(cd "$DEST" && pwd -P)" = "$SRC" ]; then die "~/.claude is the kit's own claude/ folder"; fi

TMP="$(mktemp -d "${TMPDIR:-/tmp}/kit-install.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

VAULT_ABS=""
if [ -n "$VAULT" ]; then
  case "$VAULT" in
    "~") VAULT="$HOME" ;;
    "~/"*) VAULT="$HOME/${VAULT:2}" ;;
  esac
  VAULT_ABS="$(abs_path "$VAULT")"
  HOME_ABS="$(cd "$HOME" && pwd -P)"
  case "$VAULT_ABS" in
    "$HERE"|"$HERE"/*) die "--vault must be outside this kit's folder: the kit is a public repository, your vault is private" ;;
  esac
  if [ -z "$VAULT_ABS" ] || [ "$VAULT_ABS" = "/" ] || [ "$VAULT_ABS" = "$HOME_ABS" ]; then
    die "--vault must be a folder of its own, not / or your home directory"
  fi
  if [ -e "$VAULT_ABS" ] && [ ! -d "$VAULT_ABS" ]; then die "--vault $VAULT_ABS exists and is not a directory"; fi
  p="$(dirname "$VAULT_ABS")"
  while :; do
    if [ -e "$p/.git" ]; then
      die "--vault $VAULT_ABS is inside the git work tree $p. The vault must be its own PRIVATE repository, never a folder inside another repo (a miner repo is cloned by the validator). Pick a path outside any repo."
    fi
    if [ "$p" = "/" ]; then break; fi
    p="$(dirname "$p")"
  done
  ( cd "$VTPL" && find . -type f ! -name .DS_Store ! -name '*.pyc' ! -path '*/__pycache__/*' | sed 's|^\./||' ) \
    | LC_ALL=C sort > "$TMP/vault_files"
fi

# ---------------------------------------------------------------------------------------------------------------------
# plan: which kit files are new / changed / unchanged under ~/.claude

( cd "$SRC" && { printf '%s\n' CLAUDE.md; find agents skills commands -type f ! -name .DS_Store ! -name '*.pyc' ! -path '*/__pycache__/*'; } ) \
  | LC_ALL=C sort > "$TMP/kit_files"
: > "$TMP/new"
: > "$TMP/changed"
SAME=0
while IFS= read -r rel; do
  if link="$(symlink_on_path "$rel")"; then
    die "refusing to write through the symlink $link - resolve it yourself, then re-run (nothing was written)"
  fi
  d="$DEST/$rel"
  if [ -d "$d" ]; then die "refusing: $d is a directory where the kit has a file (nothing was written)"; fi
  if [ ! -e "$d" ]; then
    printf '%s\n' "$rel" >> "$TMP/new"
  elif cmp -s "$SRC/$rel" "$d"; then
    SAME=$((SAME + 1))
  else
    printf '%s\n' "$rel" >> "$TMP/changed"
  fi
done < "$TMP/kit_files"
N_NEW="$(count_lines "$TMP/new")"
N_CHG="$(count_lines "$TMP/changed")"

# settings.json: install (absent) / suggest (present) / skip (dangling symlink)
if [ -L "$SETTINGS" ] && [ ! -e "$SETTINGS" ]; then
  SET_MODE=skip
elif [ -e "$SETTINGS" ]; then
  SET_MODE=suggest
else
  SET_MODE=install
fi

# the settings template, with the hook paths pointed at --vault when it is not the default location
RENDERED="$TMP/settings.rendered.json"
HOOKS_NOTE=""
if [ -n "$VAULT_ABS" ] && [ "$VAULT_ABS" != "$(abs_path "$DEFAULT_VAULT")" ]; then
  case "$VAULT_ABS" in
    *[!A-Za-z0-9._/+-]*)
      cp "$TPL" "$RENDERED"
      HOOKS_NOTE="the vault path has characters this script does not put into a hook command: edit the two hook commands in ~/.claude/settings.json by hand (they say ~/claude-vault)" ;;
    *)
      sed "s|~/claude-vault/|$VAULT_ABS/|g" "$TPL" > "$RENDERED" ;;
  esac
else
  cp "$TPL" "$RENDERED"
  if [ "$SET_MODE" = install ] && [ ! -d "$DEFAULT_VAULT" ] && [ -z "$VAULT_ABS" ]; then
    HOOKS_NOTE="the hooks in settings.json run ~/claude-vault/sync.sh; until that vault exists every session shows a hook error. Create it: bash install.sh --vault ~/claude-vault"
  fi
fi

WRITES=$((N_NEW + N_CHG))
if [ "$SET_MODE" = install ]; then WRITES=$((WRITES + 1)); fi

# ---------------------------------------------------------------------------------------------------------------------
# report the plan

if [ "$DRY" = 1 ]; then say "DRY RUN - nothing is written."; fi
say "Kit:    $HERE"
say "Target: $DEST"
say "  CLAUDE.md, agents/, skills/, commands/: $N_NEW new, $N_CHG changed, $SAME unchanged"
if [ "$DRY" = 1 ]; then sed 's/^/    + /' "$TMP/new"; fi
sed 's/^/    ~ (replaced, old copy goes to the backup) /' "$TMP/changed"
case "$SET_MODE" in
  install) say "  settings.json: none yet -> created from claude/settings.template.json" ;;
  suggest) say "  settings.json: exists -> left exactly as it is (suggested merge below)" ;;
  skip)    warn "$SETTINGS is a dangling symlink -> not touched" ;;
esac

# ---------------------------------------------------------------------------------------------------------------------
# write: backup, then copy

BACKUP=""
if [ "$WRITES" -gt 0 ] && [ "$DRY" = 0 ]; then
  existing=""
  for item in CLAUDE.md agents skills commands settings.json; do
    if [ -e "$DEST/$item" ] || [ -L "$DEST/$item" ]; then existing="$existing $item"; fi
  done
  if [ -n "$existing" ]; then
    BACKUP="$HOME/.claude.bak-$STAMP"
    n=0
    while [ -e "$BACKUP" ]; do n=$((n + 1)); BACKUP="$HOME/.claude.bak-$STAMP-$n"; done
    mkdir -p "$BACKUP"
    for item in $existing; do cp -pR "$DEST/$item" "$BACKUP/"; done
    {
      printf '# install.sh backup %s of:%s\n' "$STAMP" "$existing"
      printf '# kit: %s\n' "$HERE"
      sed 's/^/NEW /' "$TMP/new"
      sed 's/^/CHANGED /' "$TMP/changed"
      if [ "$SET_MODE" = install ]; then printf 'NEW settings.json\n'; fi
    } > "$BACKUP/MANIFEST.txt"
    say "  backup: $BACKUP (what this run adds or replaces: MANIFEST.txt)"
  else
    say "  backup: nothing to back up (no earlier config in $DEST)"
  fi
  cat "$TMP/new" "$TMP/changed" > "$TMP/todo"
  while IFS= read -r rel; do
    mkdir -p "$(dirname "$DEST/$rel")"
    cp -p "$SRC/$rel" "$DEST/$rel"
  done < "$TMP/todo"
  if [ "$SET_MODE" = install ]; then
    mkdir -p "$DEST"
    cp "$RENDERED" "$SETTINGS"
  fi
elif [ "$WRITES" -gt 0 ]; then
  say "  (a real run backs up the existing config to ~/.claude.bak-<timestamp> first)"
else
  say "  already up to date - nothing written, no backup made"
fi

# ---------------------------------------------------------------------------------------------------------------------
# settings.json exists: print a suggested merge (never applied)

if [ "$SET_MODE" = suggest ]; then
  merged=1
  if have_python; then
    set +e
    python3 - "$SETTINGS" "$RENDERED" "$TMP/yours.json" "$TMP/suggested.json" <<'PY'
import json, sys
cur_p, tpl_p, yours_p, out_p = sys.argv[1:5]
try:
    with open(cur_p, encoding="utf-8") as f:
        cur = json.load(f)
except Exception as e:  # not JSON we can read: let the shell show a raw comparison
    print(f"  (could not parse {cur_p}: {e})")
    sys.exit(3)
with open(tpl_p, encoding="utf-8") as f:
    tpl = json.load(f)
if not isinstance(cur, dict) or not isinstance(cur.get("hooks", {}), dict):
    sys.exit(3)
out = json.loads(json.dumps(cur))
notes = []
for key, val in tpl.items():
    if key.startswith("$"):
        continue
    if key == "hooks":
        hooks = out.setdefault("hooks", {})
        for event, groups in val.items():
            have = hooks.setdefault(event, [])
            if not isinstance(have, list):
                sys.exit(3)
            blob = json.dumps(have)
            for group in groups:
                cmds = [h.get("command", "") for h in group.get("hooks", [])]
                if cmds and all(json.dumps(c)[1:-1] in blob for c in cmds):
                    continue
                have.append(group)
    elif key not in out:
        out[key] = val
    elif out[key] != val:
        notes.append(f"  kept your '{key}': {json.dumps(out[key])} (the kit's template has {json.dumps(val)})")
for n in notes:
    print(n)
if out == cur:
    sys.exit(10)
for path, obj in ((yours_p, cur), (out_p, out)):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)
        f.write("\n")
PY
    rc=$?
    set -e
    case "$rc" in
      0)
        say "  suggested merge into $SETTINGS (NOT applied - add the + lines yourself, or ask Claude Code to merge them):"
        diff -u --label "settings.json (yours, reformatted)" --label "settings.json (with the kit's keys)" \
          "$TMP/yours.json" "$TMP/suggested.json" || true ;;
      10) say "  settings.json already has the kit's fallbackModel and hooks - nothing to suggest" ;;
      *)  merged=0 ;;
    esac
  else
    merged=0
  fi
  if [ "$merged" = 0 ]; then
    say "  raw comparison (merge the keys fallbackModel and hooks by hand; do NOT replace your file):"
    diff -u --label "settings.json (yours)" --label "settings.template.json (kit)" "$SETTINGS" "$RENDERED" || true
  fi
fi
if [ -n "$HOOKS_NOTE" ]; then warn "$HOOKS_NOTE"; fi

# ---------------------------------------------------------------------------------------------------------------------
# --vault: copy the template, never overwrite

if [ -n "$VAULT_ABS" ]; then
  say "Vault:  $VAULT_ABS"
  added=0; same=0; kept=0
  while IFS= read -r rel; do
    s="$VTPL/$rel"; d="$VAULT_ABS/$rel"
    if [ -e "$d" ] || [ -L "$d" ]; then
      if cmp -s "$s" "$d"; then
        same=$((same + 1))
      else
        kept=$((kept + 1))
        say "    = kept yours (differs from the template): $rel"
      fi
      continue
    fi
    added=$((added + 1))
    if [ "$DRY" = 1 ]; then say "    + $rel"; continue; fi
    mkdir -p "$(dirname "$d")"
    cp -p "$s" "$d"
  done < "$TMP/vault_files"
  if [ "$DRY" = 1 ]; then
    say "  would add $added, $same already identical, $kept kept as they are"
  else
    say "  $added added, $same already identical, $kept kept as they were"
  fi
fi

# ---------------------------------------------------------------------------------------------------------------------
# check the result (the acceptance gate of CLAUDE.md section 4, kit agents only)

FAIL=0
if [ "$DRY" = 0 ]; then
  n_kit=0
  for f in "$SRC"/agents/*.md; do
    n_kit=$((n_kit + 1))
    t="$DEST/agents/$(basename "$f")"
    if [ ! -f "$t" ]; then say "  FAIL missing: $t"; FAIL=1; continue; fi
    if ! cmp -s "$f" "$t"; then say "  FAIL differs from the kit after copying: $t"; FAIL=1; fi
    if ! grep -qE "$MODEL_RE" "$t"; then say "  FAIL no known model: line in $t"; FAIL=1; fi
    if ! grep -qx 'effort: max' "$t"; then say "  FAIL no 'effort: max' line in $t"; FAIL=1; fi
  done
  n_all="$(find "$DEST/agents" -maxdepth 1 -type f -name '*.md' | wc -l | tr -d ' ')"
  if [ "$FAIL" = 0 ]; then
    say "Check:  PASS - $n_kit kit agents installed, each with a known model: line and effort: max"
  else
    say "Check:  FAIL - see the lines above"
  fi
  if [ "$n_all" -gt "$n_kit" ]; then say "  note: $((n_all - n_kit)) agent file(s) of your own also live in $DEST/agents (kept)"; fi
fi

# ---------------------------------------------------------------------------------------------------------------------
# next steps

say ""
say "Next (details in README.md, Quickstart):"
say "  - Replace every <...> placeholder in ~/.claude/CLAUDE.md, then delete its 'Cara memakai template ini' block."
say "  - Check which models your account can use and what actually serves each agent (README: Quickstart, step 4)."
if [ -n "$VAULT_ABS" ]; then
  say "  - Vault: make $VAULT_ABS a git repo whose remote is a PRIVATE repository, set its user.name/user.email,"
  say "    then link a track: bash \"$VAULT_ABS/install.sh\" image <folder where you run claude for the image track>"
fi
say "  - Per-workspace files are not installed: claude/project/ and harness/common/claude-commands/ (README)."
if [ -n "$BACKUP" ]; then say "  - Undo: copy back from $BACKUP and delete the NEW files listed in its MANIFEST.txt."; fi
exit "$FAIL"
