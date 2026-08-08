#!/usr/bin/env python3
"""Keep executable code blocks byte-identical between docs/ja and docs/en.

AGENTS.md: "Code examples are identical across languages." Prose is translated;
the commands are not, so a reader following the Japanese guide runs exactly what
the English guide documents.

WHAT THIS DOES AND DOES NOT TOUCH
    Synchronised   fenced blocks tagged with an executable or config language
                   (see SYNCED_LANGS): bash, yaml, json, python, sql, hcl, ...
    Left alone     untagged blocks and ```mermaid — these are ASCII architecture
                   diagrams, flow sketches and captured command output. Their
                   labels are prose, and a Japanese reader is better served by
                   "AI エージェント層" than by forcing the English label. Treating
                   a diagram as a "code example" would degrade the primary
                   language for no benefit.

DIRECTION
    English is the source for synchronised blocks, because the commands were
    authored and verified in English. That is a narrow, deliberate exception to
    Japanese being the primary language: it applies to command text only, never
    to prose, headings or diagrams.

WHY A SCRIPT RATHER THAN A ONE-OFF EDIT
    The divergence returns every time someone translates a comment inside a
    fence. `--check` makes that a test failure instead of a slow drift, and
    tests/test_code_block_sync.py runs it.

REFUSES TO GUESS
    If the two files disagree on the number of fences, or a pair disagrees on
    its language tag, the block indices no longer describe the same content and
    copying would silently move the wrong text. That is reported and skipped
    rather than resolved by guesswork.

Usage:
    python3 shared/scripts/sync-code-blocks.py            # rewrite docs/ja
    python3 shared/scripts/sync-code-blocks.py --check     # exit 1 if out of sync
    python3 shared/scripts/sync-code-blocks.py --diff      # show what would change
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
JA_DIR = REPO_ROOT / "docs" / "ja"
EN_DIR = REPO_ROOT / "docs" / "en"

# Executable or machine-parsed content. A difference here is a defect: it means
# the two guides tell the reader to run different things.
SYNCED_LANGS = frozenset(
    {
        "bash",
        "sh",
        "shell",
        "console",
        "yaml",
        "yml",
        "json",
        "python",
        "py",
        "sql",
        "hcl",
        "terraform",
        "toml",
        "ini",
        "dockerfile",
    }
)

# Diagram and free-text fences whose labels are prose. Localised on purpose.
DIAGRAM_LANGS = frozenset({"", "mermaid", "text", "txt", "plaintext", "diagram"})

FENCE_RE = re.compile(r"^(\s*)```(\S*)\s*$")


@dataclass
class Block:
    lang: str
    body: str
    start: int  # index of the opening fence line
    end: int  # index of the closing fence line


def parse_blocks(lines: list[str]) -> list[Block]:
    """Fence-aware scan. Only a bare ``` opens or closes a block, so indented
    fences inside list items are handled, and a ``` inside an open block is
    treated as the closer rather than a nested opener."""
    blocks: list[Block] = []
    open_at: int | None = None
    lang = ""
    for i, line in enumerate(lines):
        m = FENCE_RE.match(line)
        if not m:
            continue
        if open_at is None:
            open_at = i
            lang = m.group(2)
        else:
            blocks.append(Block(lang, "\n".join(lines[open_at + 1 : i]), open_at, i))
            open_at = None
            lang = ""
    return blocks


def has_cjk(text: str) -> bool:
    for ch in text:
        if ord(ch) < 0x2E80:
            continue
        if unicodedata.name(ch, "").startswith(("CJK", "HIRAGANA", "KATAKANA")):
            return True
    return False


def sync_file(ja_path: Path, en_path: Path) -> tuple[str | None, list[str], list[str]]:
    """Return (new_ja_text_or_None, changed_descriptions, problem_descriptions)."""
    ja_lines = ja_path.read_text(encoding="utf-8").splitlines()
    en_lines = en_path.read_text(encoding="utf-8").splitlines()
    ja_blocks = parse_blocks(ja_lines)
    en_blocks = parse_blocks(en_lines)
    name = ja_path.name

    if len(ja_blocks) != len(en_blocks):
        return (
            None,
            [],
            [
                f"{name}: fence count differs (ja={len(ja_blocks)} en={len(en_blocks)}); "
                "block indices no longer line up, so nothing was copied"
            ],
        )

    changed: list[str] = []
    problems: list[str] = []
    # Rewrite back-to-front so earlier line numbers stay valid.
    out = list(ja_lines)
    for idx in range(len(ja_blocks) - 1, -1, -1):
        ja_b, en_b = ja_blocks[idx], en_blocks[idx]
        if ja_b.lang != en_b.lang:
            problems.append(
                f"{name} block#{idx}: language tag differs (ja=```{ja_b.lang} en=```{en_b.lang})"
            )
            continue
        if ja_b.lang.lower() not in SYNCED_LANGS:
            continue
        if ja_b.body == en_b.body:
            continue
        if has_cjk(en_b.body):
            problems.append(
                f"{name} block#{idx}: the English block contains CJK text; "
                "fix the English side first rather than copying it into Japanese"
            )
            continue
        out[ja_b.start + 1 : ja_b.end] = en_b.body.split("\n")
        changed.append(f"{name} block#{idx} (```{ja_b.lang})")

    new_text = "\n".join(out) + "\n"
    return new_text, list(reversed(changed)), problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="report drift and exit 1; write nothing")
    ap.add_argument("--diff", action="store_true", help="print a unified diff of the changes")
    args = ap.parse_args()

    total_changed = 0
    total_problems = 0
    for ja_path in sorted(JA_DIR.glob("*.md")):
        en_path = EN_DIR / ja_path.name
        if not en_path.exists():
            continue
        new_text, changed, problems = sync_file(ja_path, en_path)

        for p in problems:
            print(f"PROBLEM  {p}")
        total_problems += len(problems)

        if not changed:
            continue
        total_changed += len(changed)
        for c in changed:
            print(f"{'DRIFT  ' if args.check else 'SYNCED '} {c}")
        if args.diff and new_text is not None:
            old = ja_path.read_text(encoding="utf-8").splitlines()
            for line in difflib.unified_diff(
                old, new_text.splitlines(), fromfile=f"a/{ja_path}", tofile=f"b/{ja_path}", lineterm="", n=1
            ):
                print("   ", line)
        if not args.check and new_text is not None:
            ja_path.write_text(new_text, encoding="utf-8")

    print()
    if args.check:
        if total_changed or total_problems:
            print(
                f"out of sync: {total_changed} block(s) differ, {total_problems} problem(s). "
                "Run: python3 shared/scripts/sync-code-blocks.py"
            )
            return 1
        print("all synchronised code blocks are identical between docs/ja and docs/en")
        return 0

    print(f"synchronised {total_changed} block(s); {total_problems} problem(s) need a human")
    return 1 if total_problems else 0


if __name__ == "__main__":
    sys.exit(main())
