#!/usr/bin/env bash
# check-repo-contract.sh - repo-contract hygiene gate for an SN56 miner repo. Run it INSIDE the miner repo, before the
# expensive build: a violation here gets the entry EXCLUDED, which costs the whole tournament, not one task.
#
# What the validator does with your repo (gradients-ai/G.O.D main b49ba8644a0d; re-read at your pin):
#   * registration clones it and runs the anti-obfuscation binary on the submitted commit (60 s timeout; a non-zero
#     exit, a timeout or any exception excludes the entry: validator/tournament/github_validation.py:20-55, called at
#     tournament_manager.py:1100), then compares LICENSE / NOTICE with its own (normalised line content, on your
#     DEFAULT branch: github_validation.py:57-170, called at tournament_manager.py:1111);
#   * docs/miner.md:35 requires readable source, exact LICENSE/NOTICE, and no hidden datasets or pretrained models;
#   * the dedup review reads the whole tree, and every file that does not train is scan and dedup surface.
# And your own rule: git history is permanent, so no secrets, no box addresses, no hotkeys - ever.
#
# Usage: bash check-repo-contract.sh [commit-ish]     (default HEAD)        Exit 0 = clean, 1 = violation(s), 2 = abort.
# env (all optional):
#   CONTRACT_EXCLUDE        pathspecs left out of the secret/host scan (e.g. vendored upstream code you never edit)
#   UPSTREAM_LICENSE_BLOB / UPSTREAM_NOTICE_BLOB   git blob ids the files must equal; defaults = G.O.D b49ba8644a0d
#                           (`git rev-parse b49ba8644a0d:LICENSE.md` / `:NOTICE`, VERIFIED 2026-10-09). If upstream
#                           changes them, update these deliberately, in a commit that names the upstream commit.
#   LICENSE_FILES [LICENSE.md NOTICE]   the two names your repo uses (docs/miner.md:256-268 lists the accepted ones)
#   CONTRACT_DOCKERFILE     the dockerfile whose COPY/ADD set is checked (default: the first G.O.D path that exists,
#                           trainer/constants.py:1-12, for CONTRACT_TRACK=image|text [text])
#   CONTRACT_COPY_ALLOW     space-separated COPY/ADD sources allowed into the image (unset = the set is only printed)
#   CONTRACT_ALLOWED_TOP    space-separated top-level entries allowed in the tracked tree (unset = only printed)
set -u
REF="${1:-HEAD}"
fail=0
say(){ printf '  %s\n' "$*"; }
bad(){ printf '  VIOLATION: %s\n' "$*"; fail=$((fail+1)); }
echo "===== repo contract @ $REF ====="

# 0a. THE REF MUST RESOLVE and its tree must have files. A gate pointed at a commit this clone has not fetched would
# otherwise print "OK" for every "nothing forbidden is tracked" check (git ls-tree fails, the error is swallowed, and an
# empty answer looks exactly like a clean tree) - and an empty tree makes every such check vacuously true.
if ! git rev-parse --verify -q "$REF^{tree}" >/dev/null 2>&1; then
  echo "  ABORT: '$REF' does not resolve to a tree here - nothing below could run; a PASS would be fabricated."
  echo "         Fetch the commit first (git fetch, or a git bundle on an offline box)."
  exit 2
fi
_tracked=$(git ls-tree -r --name-only "$REF" | wc -l | tr -d ' ')
if [ "${_tracked:-0}" -lt 2 ]; then
  echo "  ABORT: '$REF' has $_tracked tracked file(s) - an empty tree passes every check vacuously."
  exit 2
fi
say "OK  ref resolves, $_tracked tracked files visible"

# 0b. CANARY - prove every pattern can fire on THIS platform before any "OK" is trusted (grep dialects differ: an
# anchor GNU grep honours may match nothing in BSD/macOS grep). The samples are ASSEMBLED AT RUNTIME, so this file
# never contains a literal its own patterns would match (otherwise the gate flags itself).
_canary=$(mktemp)
_a=abcdefghijklmnopqrstuvwxyz; _n=0123456789
{ printf 'hf%s%s%s\n'          '_' "$_a" "$_n"
  printf 'ghp%s%s%s\n'         '_' "$_a" "$_n"
  printf 'github%spat%s%s%s\n' '_' '_' "$_a$(echo "$_a" | tr a-z A-Z)" "$_n"
  printf 'sk%sant%s%s\n'       '-' '-' "$_a"
  printf '203%s0%s113%s7\n'    '.' '.' '.'
  printf '5%sc\n'              "$(printf 'Ab%.0s' $(seq 1 23))"
} > "$_canary"
_dead=0
for cpat in 'hf_[A-Za-z0-9]{34,}' 'ghp_[A-Za-z0-9]{30,}' 'github_pat_[A-Za-z0-9_]{50,}' \
            'sk-ant-[A-Za-z0-9-]{20,}' '(^|[^0-9.])((25[0-5]|2[0-4][0-9]|1?[0-9]?[0-9])\.){3}(25[0-5]|2[0-4][0-9]|1?[0-9]?[0-9])([^0-9.]|$)' \
            '5[A-HJ-NP-Za-km-z1-9]{47}'; do
  grep -qE "$cpat" "$_canary" || { echo "  CANARY DEAD: /$cpat/ matches nothing on this platform"; _dead=$((_dead+1)); }
done
rm -f "$_canary"
[ "$_dead" -eq 0 ] || { echo "  ABORT: $_dead pattern(s) cannot fire here - a PASS would be meaningless"; exit 2; }
say "OK  canary: all 6 detection patterns fire on this platform"

# 1. compiled / weight artefacts anywhere in the tracked tree
hits=$(git ls-tree -r --name-only "$REF" | grep -iE '\.(pyc|pyo|bin|pt|pth|safetensors|gguf|onnx|so|o|a|dylib|dll|whl|egg|ckpt|h5|npz|npy)$' || true)
if [ -n "$hits" ]; then bad "compiled/weight artefacts tracked:"; echo "$hits" | head -10 | sed 's/^/      /'
else say "OK  no compiled artefacts or weights tracked"; fi

# 2. bundled datasets: source files under a data/ package are fine, payloads are not
data_payload=$(git ls-tree -r --name-only "$REF" | grep -iE '(^|/)(datasets?|data)/' | grep -ivE '\.(py|md|txt)$' || true)
if [ -n "$data_payload" ]; then bad "dataset payloads tracked:"; echo "$data_payload" | head -10 | sed 's/^/      /'
else say "OK  no bundled dataset payloads"; fi

# 3. secrets and machine identifiers (history is permanent: a leak cannot be un-shipped)
EXCL=(); for x in ${CONTRACT_EXCLUDE:-}; do EXCL+=(":(exclude)$x"); done
before=$fail
for pat in 'hf_[A-Za-z0-9]{34,}' 'ghp_[A-Za-z0-9]{30,}' 'github_pat_[A-Za-z0-9_]{50,}' \
           'sk-ant-[A-Za-z0-9-]{20,}'; do
  n=$(git grep -IE "$pat" "$REF" -- . ${EXCL[@]+"${EXCL[@]}"} 2>/dev/null | head -5 || true)
  if [ -n "$n" ]; then bad "possible secret matching /$pat/:"; echo "$n" | head -3 | sed 's/^/      /'; fi
done
# host addresses: judge each matched address on its own (a loopback on the same line must not hide a real IP)
IPRE='((25[0-5]|2[0-4][0-9]|1?[0-9]?[0-9])\.){3}(25[0-5]|2[0-4][0-9]|1?[0-9]?[0-9])'
ips=$(git grep -IhoE "(^|[^0-9.])${IPRE}([^0-9.]|\$)" "$REF" -- . ${EXCL[@]+"${EXCL[@]}"} 2>/dev/null \
      | grep -oE "$IPRE" | grep -vxE '127\.0\.0\.1|0\.0\.0\.0' | sort -u | head -5 || true)
if [ -n "$ips" ]; then bad "possible host address(es) in tracked files:"; echo "$ips" | sed 's/^/      /'; fi
[ "$fail" -eq "$before" ] && say "OK  no tokens or host addresses in tracked files"

# 4. hotkeys: SS58 is base58 (mixed case, no 0/O/I/l); plain lowercase hex hashes must NOT trip this
ss58=$(git grep -hoIE '5[A-HJ-NP-Za-km-z1-9]{47}' "$REF" -- . 2>/dev/null | grep -E '[A-HJ-NP-Z]' | grep -E '[a-km-z]' | sort -u || true)
if [ -n "$ss58" ]; then bad "SS58-shaped hotkeys tracked:"; echo "$ss58" | head -5 | sed 's/^/      /'
else say "OK  no SS58-shaped hotkeys"; fi

# 5. LICENSE / NOTICE: present AND byte-identical to upstream. The validator compares normalised line content; this gate
# is deliberately stricter (git blob equality, same on macOS and Linux). If it FAILS, do not "fix" the file to match:
# find out who edited it, or whether upstream changed - and restore it from upstream, never rewrite it by hand.
_LICENSE_BLOB="${UPSTREAM_LICENSE_BLOB:-4e8744af32ead776fd5ed4299e65a3011cb7bf11}"
_NOTICE_BLOB="${UPSTREAM_NOTICE_BLOB:-29c2a05950383849489002ac42aed08866c6cd1f}"
set -- ${LICENSE_FILES:-LICENSE.md NOTICE}
for pair in "$1:$_LICENSE_BLOB" "$2:$_NOTICE_BLOB"; do
  f="${pair%%:*}"; want="${pair#*:}"
  if ! git cat-file -e "$REF:$f" 2>/dev/null; then bad "$f MISSING"; continue; fi
  have=$(git rev-parse "$REF:$f" 2>/dev/null)
  if [ "$have" = "$want" ]; then say "OK  $f present and byte-identical to upstream (blob ${have:0:8})"
  else bad "$f CHANGED (blob $have, want $want) - the entry would be excluded"; fi
done

# 6. what the image COPIES (only training code should reach the container)
DF="${CONTRACT_DOCKERFILE:-}"
if [ -z "$DF" ]; then
  case "${CONTRACT_TRACK:-text}" in
    image) CANDS="ops/docker/standalone-image-toolkit-trainer.dockerfile dockerfiles/standalone-image-toolkit-trainer.dockerfile ops/docker/standalone-image-trainer.dockerfile dockerfiles/standalone-image-trainer.dockerfile" ;;
    *)     CANDS="ops/docker/standalone-text-trainer.dockerfile dockerfiles/standalone-text-trainer.dockerfile" ;;
  esac
  for c in $CANDS; do git cat-file -e "$REF:$c" 2>/dev/null && { DF="$c"; break; }; done
fi
if [ -z "$DF" ] || ! git cat-file -e "$REF:$DF" 2>/dev/null; then
  bad "no dockerfile at the paths the validator looks for (trainer/constants.py:1-12) - production cannot build this repo"
else
  copy_src=$(git show "$REF:$DF" | grep -E '^(COPY|ADD) ' | grep -v -- '--from=' | awk '{print $2}' | sort -u | tr '\n' ' ')
  if [ -n "${CONTRACT_COPY_ALLOW:-}" ]; then
    extra=""; for s in $copy_src; do case " $CONTRACT_COPY_ALLOW " in *" $s "*) ;; *) extra="$extra $s" ;; esac; done
    if [ -n "$extra" ]; then bad "$DF copies sources outside CONTRACT_COPY_ALLOW:$extra"
    else say "OK  $DF copies only: $copy_src"; fi
  else
    say "INFO $DF copies: $copy_src(set CONTRACT_COPY_ALLOW to enforce)"
  fi
fi

# 7. TREE SURFACE - what the validator CLONES (not what the image copies): every non-training file is anti-obfuscation
# scan surface and dedup surface for no benefit. Keep dev tooling (agents, harness, notes) OUT of the miner repo.
tops=$(git ls-tree -r --name-only "$REF" | awk -F/ '{print $1}' | sort -u)
if [ -n "${CONTRACT_ALLOWED_TOP:-}" ]; then
  allowed=" $CONTRACT_ALLOWED_TOP LICENSE.md LICENSE license.md license License.md License NOTICE NOTICE.txt notice.txt Notice.txt notice Notice "
  extra=""; for t in $tops; do case "$allowed" in *" $t "*) ;; *) extra="$extra $t" ;; esac; done
  if [ -n "$extra" ]; then bad "tree surface: top-level entries outside CONTRACT_ALLOWED_TOP:$extra"
  else say "OK  tree surface: top-level = $(echo $tops)"; fi
else
  say "INFO tree surface: top-level = $(echo $tops) (set CONTRACT_ALLOWED_TOP to enforce)"
fi

echo
if [ "$fail" -gt 0 ]; then echo "===== $fail VIOLATION(S) - DO NOT SUBMIT ====="; exit 1; fi
echo "===== repo contract clean ====="
