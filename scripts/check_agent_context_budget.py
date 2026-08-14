#!/usr/bin/env python3
"""Keep always-loaded agent context small, and keep its index reachable.

Why this exists
---------------
AGENTS.md is read on every single turn and cannot be made conditional. It had
grown to 43,518 bytes across 854 lines, and most of that was material relevant
to one kind of work: a 173-line table of S3 Access Point and Active Directory
pitfalls, a per-vendor API lookup table, and a step-by-step procedure for adding
a vendor. An agent editing a CloudFormation template paid for all of it.

The content moved to docs/agent/, which is tracked, with a one-line index entry
left in AGENTS.md and a conditional loader in .kiro/steering/. The direction
matters: .kiro/ is not published, so putting the body there would delete it from
the public documentation while appearing to preserve it.

Nothing stops the file growing back. This check does.

It enforces four things:
  1. AGENTS.md stays under a byte budget.
  2. Every path AGENTS.md points at exists.
  3. Every path AGENTS.md points at is tracked by git, because an untracked
     index target is invisible to everyone reading the repository on GitHub.
  4. Steering loaders stay thin -- a loader that accumulates its own prose
     recreates the problem one directory over, where it is also unpublished.

Silent on success. Exits 1 with an explanation otherwise.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# AGENTS.md is 26,576 bytes after the extraction. The budget leaves room to
# grow but not room to absorb another 11 KB section. Raising it is a decision,
# which is the point of it being a number in a file rather than a habit.
AGENTS_MAX_BYTES = 30_000

# A steering file under .kiro/ should be front matter plus a pointer. Prose
# belongs in a tracked file. This allows a short "when to read this" note.
STEERING_LOADER_MAX_BYTES = 2_000

# Documentation that exists to be loaded conditionally, not always.
AGENT_DOCS_DIR = REPO_ROOT / "docs/agent"

MARKDOWN_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)#]+)(?:#[^)]*)?\)")
FILE_REF_RE = re.compile(r"#\[\[file:([^\]]+)\]\]")


def tracked_files() -> set[str]:
    result = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True
    )
    return set(result.stdout.split())


def check_agents_md(problems: list[str]) -> None:
    path = REPO_ROOT / "AGENTS.md"
    if not path.is_file():
        problems.append("AGENTS.md is missing")
        return

    size = path.stat().st_size
    if size > AGENTS_MAX_BYTES:
        over = size - AGENTS_MAX_BYTES
        problems.append(
            f"AGENTS.md is {size:,} bytes, {over:,} over the {AGENTS_MAX_BYTES:,} "
            "byte budget. It is loaded on every turn and cannot be made "
            "conditional. Move work-specific material to docs/agent/ and leave a "
            "one-line index entry, or raise the budget deliberately with a reason."
        )

    text = path.read_text(encoding="utf-8")
    tracked = tracked_files()

    for target in sorted(set(MARKDOWN_LINK_RE.findall(text))):
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        rel = target.lstrip("./")
        resolved = REPO_ROOT / rel
        if not resolved.exists():
            problems.append(
                f"AGENTS.md links to {target}, which does not exist. An index "
                "entry pointing nowhere is worse than no entry: it reads as "
                "though the material is available."
            )
        elif resolved.is_file() and rel not in tracked:
            problems.append(
                f"AGENTS.md links to {target}, which exists but is not tracked "
                "by git. It is invisible to anyone reading this repository on "
                "GitHub. Either track it or stop pointing at it."
            )


def check_steering_loaders(problems: list[str]) -> None:
    steering = REPO_ROOT / ".kiro/steering"
    if not steering.is_dir():
        return
    tracked = tracked_files()
    for path in sorted(steering.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        size = path.stat().st_size
        refs = FILE_REF_RE.findall(text) + [
            t for t in MARKDOWN_LINK_RE.findall(text)
            if not t.startswith(("http://", "https://", "mailto:"))
        ]

        # Only loaders are size-checked. A steering file that carries no
        # reference is standalone guidance, which is a different thing.
        if refs and size > STEERING_LOADER_MAX_BYTES:
            problems.append(
                f".kiro/steering/{path.name} is {size:,} bytes. A loader should "
                f"be front matter plus a pointer (budget {STEERING_LOADER_MAX_BYTES:,}). "
                "Prose here is unpublished, because .kiro/ is not committed -- "
                "move it into the tracked file it points at."
            )

        for ref in refs:
            rel = ref.lstrip("./")
            # Loader references may be written relative to the steering file.
            candidates = [REPO_ROOT / rel, (path.parent / ref).resolve()]
            if not any(c.exists() for c in candidates):
                problems.append(
                    f".kiro/steering/{path.name} references {ref}, which does "
                    "not exist. The loader will load nothing, silently."
                )
                continue
            for candidate in candidates:
                if not candidate.is_file():
                    continue
                try:
                    as_rel = str(candidate.relative_to(REPO_ROOT))
                except ValueError:
                    continue
                if as_rel not in tracked:
                    problems.append(
                        f".kiro/steering/{path.name} references {ref}, which is "
                        "not tracked by git. Since .kiro/ is also unpublished, "
                        "this content exists only on one machine."
                    )
                break


def check_agent_docs_are_tracked(problems: list[str]) -> None:
    """docs/agent/ is the published home for the extracted material.

    .gitignore in this repo carries blanket rules that have silently swallowed
    new documentation before, so an untracked file here is a realistic failure
    rather than a hypothetical one.
    """
    if not AGENT_DOCS_DIR.is_dir():
        return
    tracked = tracked_files()
    for path in sorted(AGENT_DOCS_DIR.rglob("*.md")):
        rel = str(path.relative_to(REPO_ROOT))
        if rel not in tracked:
            problems.append(
                f"{rel} is not tracked by git. AGENTS.md points readers here, "
                "and .gitignore in this repo has swallowed new docs before; "
                "force-add it after confirming it carries no account id, "
                "resource id or private IP."
            )


def main() -> int:
    problems: list[str] = []
    check_agents_md(problems)
    check_steering_loaders(problems)
    check_agent_docs_are_tracked(problems)

    if not problems:
        return 0

    print("Agent context budget problems:", file=sys.stderr)
    for problem in problems:
        print(f"  - {problem}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
