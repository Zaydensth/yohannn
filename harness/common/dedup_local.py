#!/usr/bin/env python3
# Portions copied verbatim from gradients-ai/G.O.D (Grads LLC, Gradients.io) remain under its license and NOTICE:
# see third_party/README.md and third_party/G.O.D/{LICENSE.md,NOTICE}.
"""Local replica of the G.O.D validator's T0/T1/T2 de-dup gate — runnable WITHOUT
ANTHROPIC_API_KEY.

Why this exists: `ops/tools/tournament/dedup_check.py` (the official miner self-check in
gradients-ai/G.O.D) calls `run_pairwise_dedup`, which pays for an Anthropic API
call for the T2 step. When the thing running this tool IS Claude Code (an agent), that
call is redundant spend — the agent can do the T2 reading itself. T0 and T1 are fully
deterministic (no model involved at all in the real gate either), so this script replicates
them BYTE-FOR-BYTE from validator source, and for T2 it prepares the exact bundle the real
judge agent would be given (file summary, stats, the real system/user prompt text) as a
markdown report, so a Claude Code session can read it and render the same verdict the judge
would, without an API key.

Source of truth (gradients-ai/G.O.D main @ b49ba8644a0d705e9b035746eceaa05614742934; re-verified 2026-10-09 by a
byte diff of every file below and an equality check of both T2 prompts against repo_dedup_config.json):
- validator/infrastructure/repo_dedup.py
    _normalize_text        :77-89   (T1 normalization — copied verbatim below)
    _normalized_digest      :92-102  (T1 digest construction — copied verbatim below)
    _file_summary            :105-138 (file-level summary — copied verbatim below)
    _hash_dup_pairs          :195-216 (T0/T1 verdict logic — replicated below)
    repo_dedup_config.json   (T2 system_prompt / user_prompt_template — quoted verbatim
                               into the generated report, not paraphrased)
- validator/tournament/repo_diff_report.py
    _safe_rel / _is_text / _collect_files   :72-97  (file selection — copied verbatim below)
    repo_diff_report_config.json            (excluded_parts/suffixes/names — copied verbatim)
- validator/tournament/constants.py:136-140  (TOURN_DEDUP_* — for context)

NOT replicated (impossible without the model, and not needed to get a definitive T0/T1
verdict): the actual Claude judgement call in `_judge_pair` (repo_dedup.py:434-479). This
tool prepares its inputs and stops. `_snapshot_god_source` (repo_dedup.py:263-296) is also
not run — this script assumes it is invoked FROM within a Claude Code session that already
has read access to a G.O.D checkout, so the runtime-contract verification the T2 judge does
against `./_god_source` should be done by hand (or by pointing an agent at a G.O.D clone)
using the questions this report lists.

Usage:
    python3 dedup_local.py <repo_a_path_or_url> <repo_b_path_or_url> \
        [--hash-a <commit>] [--hash-b <commit>] [--out report.md]

Python 3.9-compatible, stdlib only. Never writes into either repo being compared.
"""

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# --------------------------------------------------------------------------------------- #
# File selection — verbatim from validator/tournament/repo_diff_report.py:72-97 plus the
# excluded_* lists from repo_diff_report_config.json (both re-checked at b49ba8644a0d on 2026-10-09).
# --------------------------------------------------------------------------------------- #
EXCLUDED_PARTS = {
    ".git", ".hg", ".svn", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "node_modules", "dist", "build", ".next", ".venv", "venv",
}
EXCLUDED_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".zip", ".tar", ".gz", ".bz2", ".xz",
    ".7z", ".bin", ".pt", ".pth", ".safetensors", ".onnx", ".npy", ".npz", ".lock",
}
EXCLUDED_NAMES = {"LICENSE", "LICENSE.md", "NOTICE"}


def _safe_rel(path: Path) -> bool:
    """repo_diff_report.py:72-79, verbatim."""
    return (
        not path.is_absolute()
        and ".." not in path.parts
        and not any(part in EXCLUDED_PARTS for part in path.parts)
        and path.name not in EXCLUDED_NAMES
        and path.suffix.lower() not in EXCLUDED_SUFFIXES
    )


def _is_text(path: Path) -> bool:
    """repo_diff_report.py:82-86, verbatim."""
    try:
        return b"\x00" not in path.read_bytes()[:4096]
    except OSError:
        return False


def _collect_files(root: Path) -> Set[str]:
    """repo_diff_report.py:89-97, verbatim."""
    files = set()
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if _safe_rel(rel) and _is_text(path):
            files.add(str(rel))
    return files


def _read_text(path: Path) -> str:
    """repo_dedup.py:70-74, verbatim."""
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


# --------------------------------------------------------------------------------------- #
# T1 normalization — verbatim from validator/infrastructure/repo_dedup.py:77-102.
# --------------------------------------------------------------------------------------- #
def _normalize_text(content: str) -> str:
    """repo_dedup.py:77-89, verbatim.

    Strip whitespace, blank lines and whole-line comments; collapse internal runs.
    Catches reformatting / comment / ordering-of-blank-lines disguises cheaply. Subtler
    rewrites are left to T2.
    """
    lines: List[str] = []
    for raw in content.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lines.append(" ".join(stripped.split()))
    return "\n".join(lines)


def _normalized_digest(root: Path) -> Tuple[str, int]:
    """repo_dedup.py:92-102, verbatim (path bytes ARE part of the digest)."""
    digest = hashlib.sha256()
    total = 0
    for rel in sorted(_collect_files(root)):
        normalized = _normalize_text(_read_text(root / rel))
        total += len(normalized)
        digest.update(rel.encode())
        digest.update(b"\0")
        digest.update(normalized.encode())
        digest.update(b"\0")
    return digest.hexdigest(), total


def _file_summary(repo_a: Path, repo_b: Path) -> str:
    """repo_dedup.py:105-138, verbatim (deterministic file-level summary)."""
    files_a = _collect_files(repo_a)
    files_b = _collect_files(repo_b)

    identical: List[str] = []
    differing: List[str] = []
    only_a: List[str] = []
    only_b: List[str] = []
    for rel in sorted(files_a | files_b):
        in_a, in_b = rel in files_a, rel in files_b
        if in_a and in_b:
            (identical if _read_text(repo_a / rel) == _read_text(repo_b / rel) else differing).append(rel)
        elif in_a:
            only_a.append(rel)
        else:
            only_b.append(rel)

    def _section(title: str, items: List[str]) -> str:
        if not items:
            return "{}: none".format(title)
        return "{} ({}):\n".format(title, len(items)) + "\n".join("  - {}".format(r) for r in items)

    return "\n".join(
        [
            _section("Identical in both (shared baseline)", identical),
            _section("Present in both but DIFFERENT", differing),
            _section("Only in A", only_a),
            _section("Only in B", only_b),
        ]
    ), identical, differing, only_a, only_b


# --------------------------------------------------------------------------------------- #
# The real T2 judge prompts (repo_dedup_config.json), quoted verbatim so the report asks the
# EXACT questions the real judge would be given — not a paraphrase.
# --------------------------------------------------------------------------------------- #
T2_SYSTEM_PROMPT = (
    "You are a forensic code reviewer for an ML training-code competition. The one threat "
    "you stop is SPAM: one author resubmitting the SAME functional submission under different "
    "identities, lightly disguised, to farm bracket randomness. For a pair, decide: same "
    "functional submission (a disguised copy) or genuinely distinct.\n\n"
    "The repos are forks of a common open-source baseline (the 'boss'), so shared baseline "
    "code is expected and is NOT evidence of copying — judge only the DELTAS each makes over "
    "the baseline and over each other. JUDGE FUNCTION, NOT AUTHORSHIP: the test is whether the "
    "two would produce meaningfully different training runs, not whether they look same-author. "
    "Shared style, naming, structure or lineage are NOT evidence, and there is no 'different "
    "author' requirement.\n\n"
    "DECISION RULE (apply literally; identical inputs must give identical verdicts):\n"
    "1. Enumerate the concrete deltas over the baseline.\n"
    "2. Classify each as COSMETIC or GENUINE. A delta is GENUINE only if you can AFFIRMATIVELY "
    "CONFIRM it is a NEW mechanism/logic that actually executes at training time and changes "
    "the run or the resulting model.\n"
    "3. Return 'distinct' if at least ONE delta is confirmed genuine — then stop. Return "
    "'duplicate' if every delta is cosmetic OR you cannot confirm that any delta is genuine. "
    "Return 'drop_evasion' if the content is dominated by padding, auto-generated files, or "
    "obfuscation designed to defeat this comparison.\n\n"
    "This competition tolerates false positives — every flag goes to human review before any "
    "elimination — so when in doubt, lean 'duplicate'.\n\n"
    "COSMETIC (never makes a pair distinct):\n"
    "- renaming, reordering, reformatting, comment/docstring edits, or rewriting code to do the "
    "same thing a different way.\n"
    "- dead code: never-called functions, unused params, unreachable branches.\n"
    "- PARAMETER TUNING: changing the value(s) of existing knobs (LR, seed, batch size, ratios, "
    "table entries) — however MANY you change. N cosmetic deltas never sum to one genuine "
    "delta.\n"
    "- RE-PARAMETRISATION: feeding an existing knob its value a different way — e.g. a "
    "hardcoded constant vs a small lookup-table over the same quantity (or vice versa), even "
    "wrapped in a helper. Choosing a value for an existing knob is not a new mechanism, so this "
    "is cosmetic EVEN IF the two pick different values for some model/task.\n"
    "- FAKE DIFFERENTIATORS: training runs in a locked container where real config (model, "
    "dataset, task, hours, reward functions, env configs) arrives as CLI args and only a small "
    "fixed set of infra env vars is set — every other env var is UNSET. Code that only diverges "
    "on conditions that never hold at training time is dead: branching on an unset env var, an "
    "unpopulated baseline-stats field, a reward input not passed, a hostname/date/missing-file/"
    "CLI-flag the trainer never sets, or a config key / library feature absent from the pinned "
    "dependency or silently ignored.\n\n"
    "GENUINE (any ONE confirmed delta makes the pair distinct — the work we WANT to allow): a "
    "genuinely NEW mechanism, logic or behaviour — NOT a new value for an existing knob — e.g. "
    "an LR finder, model-soup / checkpoint-combination, a new subsampling/packing/scheduling "
    "scheme, an early-stopping/overfitting policy, a different checkpoint-selection method; a "
    "true baseline bug fix; or materially different training/data/optimisation logic. It must "
    "actually execute and change behaviour.\n\n"
    "When one of the two is the boss and the other's deltas are all cosmetic, the other is a "
    "'duplicate' of the boss.\n\n"
    "When given the validator source and pinned deps, verify any claimed differentiator "
    "actually executes at training time; read the real code when unsure. TIE-BREAK: if you "
    "cannot confirm a differentiator is a real, live NEW mechanism, treat it as cosmetic, "
    "return 'duplicate', and NAME the input you could not verify. Do not give it the benefit "
    "of the doubt.\n\n"
    "Set confidence to how clear the call is: >=0.8 when a delta is unambiguously a genuine "
    "new mechanism, or unambiguously all cosmetic/tuning; lower only when you genuinely cannot "
    "tell."
)

T2_USER_PROMPT_TEMPLATE = (
    "Two candidate submissions are checked out as subdirectories of the current working "
    "directory:\n"
    "- Repository A at ./{dir_a}\n"
    "- Repository B at ./{dir_b}\n\n"
    "They are forks of a shared open-source baseline, so common files are expected and are "
    "not themselves evidence of copying. A deterministic file-level summary follows; use it "
    "to focus your reading:\n\n{file_summary}\n\n{runtime_context}\n\n"
    "Use Read, Glob and Grep to inspect the ACTUAL contents under ./{dir_a} and ./{dir_b} — "
    "especially the files that differ — before deciding. Follow your instructions: enumerate "
    "the deltas, classify each, and return 'distinct' ONLY if you can confirm at least one "
    "genuine, live NEW mechanism/logic that changes training behaviour (NOT a re-parametrisation "
    "or a different value for an existing knob); return 'duplicate' if every delta is cosmetic "
    "OR you cannot confirm a genuine new mechanism (lean 'duplicate' when in doubt, and name "
    "the input you could not verify); 'drop_evasion' if the content is dominated by padding/"
    "obfuscation designed to defeat this comparison.\n\n"
    "IMPORTANT — this verdict is published for public audit: in your reason refer to the "
    "submissions ONLY as \"Repository A\" and \"Repository B\". Do NOT mention directory names, "
    "repository URLs, authors, organisations, or GitHub usernames; cite file paths and code "
    "only.\n\n"
    "Respond with ONLY a single JSON object and nothing else (no prose, no code fences):\n"
    '{{"relationship": "duplicate" | "distinct" | "drop_evasion", "confidence": <number '
    '0.0-1.0>, "reason": "<concise justification citing concrete code evidence, using only '
    "'Repository A' / 'Repository B'>\"}}"
)


# --------------------------------------------------------------------------------------- #
# Cloning / local resolution
# --------------------------------------------------------------------------------------- #
def _looks_like_url(spec: str) -> bool:
    return bool(re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://", spec)) or spec.startswith("git@")


def _run(cmd: List[str], cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, cwd=str(cwd) if cwd else None, check=True, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )


def _git_head(repo: Path) -> Optional[str]:
    try:
        return _run(["git", "rev-parse", "HEAD"], cwd=repo).stdout.strip()
    except Exception:
        return None


def _resolve_repo(spec: str, label: str, commit: Optional[str], temp_root: Path) -> Tuple[Path, Optional[str]]:
    """Return (path, head_commit_or_None). Never writes into a path we did not create."""
    if _looks_like_url(spec):
        dest = temp_root / label
        clone_cmd = ["git", "clone", spec, str(dest)]
        if not commit:
            clone_cmd[2:2] = ["--depth", "1"]
        _run(clone_cmd)
        if commit:
            _run(["git", "checkout", commit], cwd=dest)
        return dest, _git_head(dest)

    src = Path(spec).expanduser().resolve()
    if not src.is_dir():
        raise RuntimeError("path does not exist or is not a directory: {}".format(src))
    if commit and (src / ".git").exists():
        # Copy first so we never mutate the caller's checkout with a checkout command.
        dest = temp_root / label
        shutil.copytree(src, dest, ignore=shutil.ignore_patterns(".git"))
        return dest, commit  # trust the caller-supplied hash; nothing to check out against
    head = _git_head(src) if (src / ".git").exists() else None
    return src, head


# --------------------------------------------------------------------------------------- #
# Report rendering
# --------------------------------------------------------------------------------------- #
def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return "{:.1f}{}".format(n, unit)
        n /= 1024.0
    return "{:.1f}TB".format(n)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Local T0/T1 replica + T2 bundle prep for the G.O.D dedup gate (no API key needed)."
    )
    parser.add_argument("repo_a", help="Repo A: local path or git clone URL")
    parser.add_argument("repo_b", help="Repo B: local path or git clone URL")
    parser.add_argument("--hash-a", help="commit hash for repo A (default: HEAD of the checkout)")
    parser.add_argument("--hash-b", help="commit hash for repo B (default: HEAD of the checkout)")
    parser.add_argument("--out", help="write the T2 bundle markdown here (default: dedup_report_<a>_vs_<b>.md)")
    args = parser.parse_args()

    temp_root = Path(tempfile.mkdtemp(prefix="dedup-local-"))
    try:
        print("Repository A: {} @ {}".format(args.repo_a, args.hash_a or "HEAD"))
        print("Repository B: {} @ {}".format(args.repo_b, args.hash_b or "HEAD"))
        print("Resolving both repos locally (no ANTHROPIC_API_KEY needed for T0/T1)...\n")

        path_a, head_a = _resolve_repo(args.repo_a, "A", args.hash_a, temp_root)
        path_b, head_b = _resolve_repo(args.repo_b, "B", args.hash_b, temp_root)

        digest_a, chars_a = _normalized_digest(path_a)
        digest_b, chars_b = _normalized_digest(path_b)

        # T0/T1 verdict — replicates repo_dedup.py:195-216 for exactly one pair.
        if head_a and head_a == head_b:
            tier, verdict, reason = "T0", "DUPLICATE", "Identical commit hash ({}).".format(head_a)
        elif digest_a == digest_b:
            tier, verdict, reason = "T1", "DUPLICATE", "Identical normalized content (sha256 {}).".format(digest_a[:16])
        else:
            tier, verdict, reason = "T0/T1", "no hash match", "Not caught by deterministic hashing; needs T2."

        print("=" * 72)
        print("T0/T1 VERDICT: {}".format(verdict))
        print("Tier:       {}".format(tier))
        print("Reason:     {}".format(reason))
        print("head A:     {}".format(head_a or "(no .git / not a git checkout)"))
        print("head B:     {}".format(head_b or "(no .git / not a git checkout)"))
        print("T1 digest A: {}  ({} normalized chars)".format(digest_a, chars_a))
        print("T1 digest B: {}  ({} normalized chars)".format(digest_b, chars_b))
        print("=" * 72)

        summary_text, identical, differing, only_a, only_b = _file_summary(path_a, path_b)

        shared = len(identical) + len(differing)
        total_files = shared + len(only_a) + len(only_b)
        pct_identical_of_shared = (100.0 * len(identical) / shared) if shared else 0.0

        # Byte-level "how much of A is actually novel vs shared with B" stat.
        bytes_a_total = sum((path_a / f).stat().st_size for f in (set(identical) | set(differing) | set(only_a)))
        bytes_a_identical = sum((path_a / f).stat().st_size for f in identical)
        bytes_a_own_pct = (100.0 * (bytes_a_total - bytes_a_identical) / bytes_a_total) if bytes_a_total else 0.0

        if verdict == "DUPLICATE":
            print("\nT2 skipped: T0/T1 already gives a definitive verdict (real gate does the same —")
            print("hash-duplicate clusters are collapsed before any pair reaches the judge).")
            t2_needed = False
        else:
            print("\nT2 REQUIRED -> the judge's reading is needed -> see {}".format(
                args.out or "dedup_report_{}_vs_{}.md".format(path_a.name, path_b.name)
            ))
            t2_needed = True

        out_path = Path(args.out) if args.out else Path.cwd() / "dedup_report_{}_vs_{}.md".format(
            re.sub(r"[^A-Za-z0-9_.-]+", "_", str(args.repo_a))[-40:],
            re.sub(r"[^A-Za-z0-9_.-]+", "_", str(args.repo_b))[-40:],
        )

        lines = [
            "# Local dedup bundle — {} vs {}".format(args.repo_a, args.repo_b),
            "",
            "Generated by `dedup_local.py` (T0/T1 replica of "
            "`validator/infrastructure/repo_dedup.py` @ G.O.D `b49ba8644a0d`; T2 is NOT called — "
            "this bundle is meant to be read by a Claude Code agent directly).",
            "",
            "## T0/T1 verdict (deterministic, matches the real gate exactly)",
            "",
            "- **Verdict: {}**".format(verdict),
            "- Tier: {}".format(tier),
            "- Reason: {}".format(reason),
            "- head A: `{}`".format(head_a or "n/a"),
            "- head B: `{}`".format(head_b or "n/a"),
            "- T1 digest A: `{}` ({} normalized chars)".format(digest_a, chars_a),
            "- T1 digest B: `{}` ({} normalized chars)".format(digest_b, chars_b),
            "",
            "## File-level stats",
            "",
            "- Total distinct paths across both: {}".format(total_files),
            "- Shared paths (present in both): {} ({} identical, {} differing)".format(
                shared, len(identical), len(differing)
            ),
            "- Only in A: {}  |  Only in B: {}".format(len(only_a), len(only_b)),
            "- Of shared paths, {:.1f}% are byte-identical".format(pct_identical_of_shared),
            "- Of Repository A's total tracked bytes ({}), {:.1f}% is NOT byte-identical to "
            "anything in B (own/differing content) — the rest is verbatim-shared baseline."
            .format(_human_size(bytes_a_total), bytes_a_own_pct),
            "",
            "## Deterministic file summary (repo_dedup.py:_file_summary, verbatim logic)",
            "",
            "```",
            summary_text,
            "```",
            "",
        ]

        if t2_needed:
            lines += [
                "## T2 — questions the real judge is asked (repo_dedup_config.json, quoted verbatim)",
                "",
                "The real gate sends the EXACT text below to a Claude agent with tools "
                "`Read`/`Glob`/`Grep` only (no `Write`/`Edit`/`Bash`; repo_dedup.py:454-455), plus a checkout of "
                "the validator's own source at `./_god_source` so it can verify claimed "
                "differentiators are real training-time inputs (repo_dedup.py:335-379). "
                "To finish this check without an API key: open Repository A and Repository B "
                "below yourself (or point a Claude Code session at them alongside a G.O.D "
                "checkout for `./_god_source`), answer the DECISION RULE below literally, and "
                "render the same JSON verdict the judge would.",
                "",
                "Repository A path: `{}`".format(path_a),
                "Repository B path: `{}`".format(path_b),
                "",
                "### system_prompt",
                "",
                "```",
                T2_SYSTEM_PROMPT,
                "```",
                "",
                "### user_prompt_template (dir_a/dir_b/file_summary already filled in below; "
                "`runtime_context` omitted here — it just points at `./_god_source`, see note "
                "above)",
                "",
                "```",
                T2_USER_PROMPT_TEMPLATE.format(
                    dir_a=path_a.name, dir_b=path_b.name, file_summary=summary_text,
                    runtime_context="(see note above: point yourself or an agent at a G.O.D "
                    "checkout to verify claimed differentiators against ./_god_source)",
                ),
                "```",
                "",
                "### Files present in both but DIFFERENT (read these first)",
                "",
            ]
            if differing:
                lines += ["- `{}`".format(f) for f in differing]
            else:
                lines += ["(none — all shared files are byte-identical; only-in-A/only-in-B "
                           "files below are the entire delta)"]
            lines += [
                "",
                "### Only in Repository A",
                "",
            ]
            lines += (["- `{}`".format(f) for f in only_a] if only_a else ["(none)"])
            lines += ["", "### Only in Repository B", ""]
            lines += (["- `{}`".format(f) for f in only_b] if only_b else ["(none)"])
            lines += [""]

        out_path.write_text("\n".join(lines))
        print("\nBundle written to {}".format(out_path))
        return 0
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
