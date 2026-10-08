"""Every terraform/<module>/README.md must have a structurally matching README.ja.md.

Why this exists
---------------
shared/scripts/check-bilingual-sync.sh walks docs/ and integrations/*/docs/
only, so a module README under terraform/ is outside every bilingual gate. A
module is fetched and read on its own (see "Obtaining the module" in its
README), so its two README files are the only documentation a reader has, and
one language drifting from the other goes unnoticed.

This test discovers every terraform/*/README.md from disk, requires a sibling
README.ja.md, and compares, outside fenced code blocks, the heading level
sequence and the number of table lines. Fenced code blocks (info string and
body) must be identical, since commands and HCL are not translated.

Guard the guard
---------------
Discovery that finds nothing passes vacuously, so every known module README
must be found. A comparison that never reports a difference also passes, so a
negative control feeds it a pair with one missing ``##`` and requires a report.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
KNOWN_READMES = (
    "terraform/fsxn-monitoring-dashboard/README.md",
    "terraform/fsxn-ontap-custom-metrics/README.md",
)

FENCE = re.compile(r"^\s*(```|~~~)(.*)$")
HEADING = re.compile(r"^(#{1,6})\s")


def _structure(text: str) -> tuple[list[int], int, list[tuple[str, str]]]:
    """Heading levels, table-line count, and fenced blocks of a Markdown text."""
    levels: list[int] = []
    tables = 0
    blocks: list[tuple[str, str]] = []
    fence: str | None = None
    info = ""
    body: list[str] = []
    for line in text.splitlines():
        m = FENCE.match(line)
        if fence is None:
            if m:
                fence, info, body = m.group(1), m.group(2).strip(), []
                continue
            h = HEADING.match(line)
            if h:
                levels.append(len(h.group(1)))
            elif line.startswith("|"):
                tables += 1
        elif m and m.group(1) == fence and not m.group(2).strip():
            blocks.append((info, "\n".join(body)))
            fence = None
        else:
            body.append(line)
    return levels, tables, blocks


def _differences(en: str, ja: str) -> list[str]:
    en_levels, en_tables, en_blocks = _structure(en)
    ja_levels, ja_tables, ja_blocks = _structure(ja)
    found: list[str] = []
    if en_levels != ja_levels:
        found.append(f"heading levels differ: en={en_levels} ja={ja_levels}")
    if en_tables != ja_tables:
        found.append(f"table line count differs: en={en_tables} ja={ja_tables}")
    if en_blocks != ja_blocks:
        found.append(
            f"fenced code blocks differ: en has {len(en_blocks)}, ja has "
            f"{len(ja_blocks)}, or a block's info string or body is not identical"
        )
    return found


def _discover_readmes(root: Path) -> list[str]:
    return sorted(
        str(p.relative_to(root)) for p in (root / "terraform").glob("*/README.md")
    )


def test_discovery_finds_every_known_module_readme() -> None:
    found = _discover_readmes(REPO_ROOT)
    missing = [r for r in KNOWN_READMES if r not in found]
    assert not missing, f"discovery returned {found}, missing {missing}"


def test_every_module_readme_has_a_matching_japanese_readme() -> None:
    problems: list[str] = []
    for rel in _discover_readmes(REPO_ROOT):
        en_path = REPO_ROOT / rel
        ja_path = en_path.with_name("README.ja.md")
        if not ja_path.is_file():
            problems.append(f"{rel}: no sibling README.ja.md")
            continue
        for diff in _differences(
            en_path.read_text(encoding="utf-8"), ja_path.read_text(encoding="utf-8")
        ):
            problems.append(f"{rel}: {diff}")
    assert not problems, "\n".join(problems)


def test_comparison_reports_a_missing_heading(tmp_path: Path) -> None:
    """Negative control: a pair with one missing ## must be reported."""
    module = tmp_path / "terraform" / "m"
    module.mkdir(parents=True)
    en = "# m\n\n## A\n\n| a |\n|---|\n\n```bash\nls\n```\n\n## B\n"
    ja = "# m\n\n## あ\n\n| a |\n|---|\n\n```bash\nls\n```\n"
    (module / "README.md").write_text(en, encoding="utf-8")
    (module / "README.ja.md").write_text(ja, encoding="utf-8")
    assert _discover_readmes(tmp_path) == ["terraform/m/README.md"]
    diffs = _differences(en, ja)
    assert any("heading levels" in d for d in diffs), diffs
    assert not _differences(en, en)


def test_fenced_lines_are_not_counted_as_headings() -> None:
    """A shell comment inside a fence is not a heading."""
    text = "## A\n\n```bash\n# comment\n| not a table\n```\n"
    levels, tables, blocks = _structure(text)
    assert levels == [2]
    assert tables == 0
    assert blocks == [("bash", "# comment\n| not a table")]
