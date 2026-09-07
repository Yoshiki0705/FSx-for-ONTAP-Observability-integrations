"""Property-based test for bilingual comparison — structural differences detected.

Uses Hypothesis to generate random Markdown document pairs with injected
structural differences and verifies that the bilingual comparator correctly
detects them.

# Feature: new-relic-e2e-verification, Property 5: Structural differences are detected

**Validates: Requirements 7.2, 7.3**
"""

from __future__ import annotations

import os
import tempfile

from hypothesis import given, settings
from hypothesis import strategies as st

from scripts.verification.bilingual_comparator import compare_setup_guides


# --- Hypothesis strategies for Markdown generation ---


@st.composite
def heading_text(draw: st.DrawFn) -> str:
    """Generate valid heading text (non-empty, no newlines, no leading '#')."""
    text = draw(
        st.text(
            alphabet=st.characters(
                whitelist_categories=("L", "N", "P", "S", "Z"),
                blacklist_characters="\n\r#|`",
            ),
            min_size=1,
            max_size=25,
        )
    )
    result = text.strip()
    return result if result else "Heading"


@st.composite
def code_block_content(draw: st.DrawFn) -> str:
    """Generate valid code block content (no backticks)."""
    content = draw(
        st.text(
            alphabet=st.characters(
                whitelist_categories=("L", "N", "P", "S", "Z"),
                blacklist_characters="`\n\r",
            ),
            min_size=1,
            max_size=40,
        )
    )
    return content.strip() or "echo hello"


@st.composite
def table_cell(draw: st.DrawFn) -> str:
    """Generate a valid table cell (no pipes or newlines)."""
    cell = draw(
        st.text(
            alphabet=st.characters(
                whitelist_categories=("L", "N", "P", "S"),
                blacklist_characters="|\n\r`",
            ),
            min_size=1,
            max_size=12,
        )
    )
    result = cell.strip()
    return result if result else "cell"


@st.composite
def markdown_with_headings(draw: st.DrawFn) -> str:
    """Generate a Markdown document guaranteed to have at least one heading."""
    parts: list[str] = []

    # At least 1 heading, up to 4
    num_headings = draw(st.integers(min_value=1, max_value=4))
    for _ in range(num_headings):
        level = draw(st.integers(min_value=1, max_value=6))
        text = draw(heading_text())
        parts.append(f"{'#' * level} {text}")
        parts.append("")
        parts.append("Some paragraph text here.")
        parts.append("")

    # Optionally add code blocks
    num_code_blocks = draw(st.integers(min_value=0, max_value=2))
    for _ in range(num_code_blocks):
        content = draw(code_block_content())
        parts.append("```bash")
        parts.append(content)
        parts.append("```")
        parts.append("")

    # Optionally add tables
    num_tables = draw(st.integers(min_value=0, max_value=1))
    for _ in range(num_tables):
        num_cols = draw(st.integers(min_value=2, max_value=4))
        num_rows = draw(st.integers(min_value=1, max_value=3))
        header_cells = [draw(table_cell()) for _ in range(num_cols)]
        parts.append("| " + " | ".join(header_cells) + " |")
        parts.append("| " + " | ".join(["---"] * num_cols) + " |")
        for _ in range(num_rows):
            row_cells = [draw(table_cell()) for _ in range(num_cols)]
            parts.append("| " + " | ".join(row_cells) + " |")
        parts.append("")

    return "\n".join(parts)


@st.composite
def markdown_with_code_blocks(draw: st.DrawFn) -> str:
    """Generate a Markdown document guaranteed to have at least one code block."""
    parts: list[str] = []

    # Optionally add headings
    num_headings = draw(st.integers(min_value=0, max_value=3))
    for _ in range(num_headings):
        level = draw(st.integers(min_value=1, max_value=6))
        text = draw(heading_text())
        parts.append(f"{'#' * level} {text}")
        parts.append("")
        parts.append("Some paragraph text here.")
        parts.append("")

    # At least 1 code block, up to 3
    num_code_blocks = draw(st.integers(min_value=1, max_value=3))
    for _ in range(num_code_blocks):
        content = draw(code_block_content())
        parts.append("```bash")
        parts.append(content)
        parts.append("```")
        parts.append("")

    # Optionally add tables
    num_tables = draw(st.integers(min_value=0, max_value=1))
    for _ in range(num_tables):
        num_cols = draw(st.integers(min_value=2, max_value=4))
        num_rows = draw(st.integers(min_value=1, max_value=3))
        header_cells = [draw(table_cell()) for _ in range(num_cols)]
        parts.append("| " + " | ".join(header_cells) + " |")
        parts.append("| " + " | ".join(["---"] * num_cols) + " |")
        for _ in range(num_rows):
            row_cells = [draw(table_cell()) for _ in range(num_cols)]
            parts.append("| " + " | ".join(row_cells) + " |")
        parts.append("")

    return "\n".join(parts)


@st.composite
def markdown_with_tables(draw: st.DrawFn) -> str:
    """Generate a Markdown document guaranteed to have at least one table."""
    parts: list[str] = []

    # Optionally add headings
    num_headings = draw(st.integers(min_value=0, max_value=3))
    for _ in range(num_headings):
        level = draw(st.integers(min_value=1, max_value=6))
        text = draw(heading_text())
        parts.append(f"{'#' * level} {text}")
        parts.append("")
        parts.append("Some paragraph text here.")
        parts.append("")

    # Optionally add code blocks
    num_code_blocks = draw(st.integers(min_value=0, max_value=2))
    for _ in range(num_code_blocks):
        content = draw(code_block_content())
        parts.append("```bash")
        parts.append(content)
        parts.append("```")
        parts.append("")

    # At least 1 table, up to 2
    num_tables = draw(st.integers(min_value=1, max_value=2))
    for _ in range(num_tables):
        num_cols = draw(st.integers(min_value=2, max_value=4))
        num_rows = draw(st.integers(min_value=1, max_value=3))
        header_cells = [draw(table_cell()) for _ in range(num_cols)]
        parts.append("| " + " | ".join(header_cells) + " |")
        parts.append("| " + " | ".join(["---"] * num_cols) + " |")
        for _ in range(num_rows):
            row_cells = [draw(table_cell()) for _ in range(num_cols)]
            parts.append("| " + " | ".join(row_cells) + " |")
        parts.append("")

    return "\n".join(parts)


# --- Difference injection helpers ---


def _inject_heading_level_mismatch(content: str) -> str:
    """Change the level of the first heading found.

    Shifts the heading level: if level < 6, increment by 1; otherwise set to 1.
    """
    lines = content.split("\n")
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("#") and not stripped.startswith("```"):
            level = 0
            for ch in stripped:
                if ch == "#":
                    level += 1
                else:
                    break
            if level <= 6 and len(stripped) > level and stripped[level] == " ":
                new_level = (level % 6) + 1
                rest = stripped[level:]
                lines[i] = "#" * new_level + rest
                return "\n".join(lines)
    return content


def _inject_code_block_content_difference(content: str) -> str:
    """Modify the content of the first code block found."""
    lines = content.split("\n")
    in_code_block = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```"):
            if not in_code_block:
                in_code_block = True
                continue
            else:
                break
        elif in_code_block:
            lines[i] = line + " --injected-difference"
            return "\n".join(lines)
    return content


def _fenced_line_numbers(lines: list[str]) -> set[int]:
    """Indices of lines inside a fenced code block, fence markers included.

    Both table injectors below look for "starts with | and ends with |", which a single
    `|` inside a ```bash block satisfies. Injecting there edits a code block instead of a
    table, so the comparator correctly reports a `code_block` difference and the test's
    assertion on a `table` difference fails — a defect in the generator, not in the
    comparator. Found by hypothesis with the falsifying example:

        ```bash
        |
        ```

        | 0 | 0 |
        | --- | --- |
        | 0 | 0 |
    """
    fenced: set[int] = set()
    in_fence = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            fenced.add(i)
            continue
        if in_fence:
            fenced.add(i)
    return fenced


def _inject_table_row_count_mismatch(content: str) -> str:
    """Add an extra row to the first table found, ignoring fenced blocks."""
    lines = content.split("\n")
    fenced = _fenced_line_numbers(lines)
    in_table = False
    last_table_line = -1

    for i, line in enumerate(lines):
        if i in fenced:
            continue
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            in_table = True
            last_table_line = i
        elif in_table:
            break

    if last_table_line >= 0:
        last_row = lines[last_table_line].strip()
        col_count = last_row.count("|") - 1
        new_row = "| " + " | ".join(["extra"] * col_count) + " |"
        lines.insert(last_table_line + 1, new_row)

    return "\n".join(lines)


def _inject_table_column_count_mismatch(content: str) -> str:
    """Add an extra column to all rows of the first table found, ignoring fenced blocks.

    Inserts a new pipe-delimited column before the trailing pipe
    in each row of the first table, creating a column count mismatch.
    """
    lines = content.split("\n")
    fenced = _fenced_line_numbers(lines)
    in_table = False
    table_start = -1
    table_end = -1

    for i, line in enumerate(lines):
        if i in fenced:
            continue
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            if not in_table:
                in_table = True
                table_start = i
            table_end = i
        elif in_table:
            break

    if table_start >= 0:
        for i in range(table_start, table_end + 1):
            stripped = lines[i].strip()
            # Check if it's a separator row (contains only |, -, :, spaces)
            inner = stripped.strip("|")
            is_separator = bool(inner) and all(
                c in "-:| " for c in inner
            ) and "-" in inner
            if is_separator:
                # Add a separator column: "| --- | --- |" → "| --- | --- | --- |"
                lines[i] = stripped + " --- |"
            else:
                # Add a data column: "| a | b |" → "| a | b | extra |"
                lines[i] = stripped + " extra |"

    return "\n".join(lines)


# --- Property 5: Structural differences are detected ---


@given(base_doc=markdown_with_headings())
@settings(max_examples=100)
def test_heading_level_mismatch_detected(base_doc: str) -> None:
    """Heading level mismatches are detected by the bilingual comparator.

    # Feature: new-relic-e2e-verification, Property 5: Structural differences are detected

    **Validates: Requirements 7.2, 7.3**

    For any Markdown document with at least one heading, injecting a heading
    level mismatch produces a comparison result with status == "fail" and
    non-empty differences list with correct diff_type, section, expected,
    and actual values.
    """
    modified = _inject_heading_level_mismatch(base_doc)

    # If injection didn't change anything, skip
    if modified == base_doc:
        return

    with tempfile.TemporaryDirectory() as tmpdir:
        ja_path = os.path.join(tmpdir, "ja", "setup-guide.md")
        en_path = os.path.join(tmpdir, "en", "setup-guide.md")
        os.makedirs(os.path.dirname(ja_path))
        os.makedirs(os.path.dirname(en_path))

        with open(ja_path, "w", encoding="utf-8") as f:
            f.write(base_doc)
        with open(en_path, "w", encoding="utf-8") as f:
            f.write(modified)

        result = compare_setup_guides(ja_path, en_path)

    assert result.status == "fail", (
        f"Expected status 'fail' for heading level mismatch, got '{result.status}'"
    )
    assert len(result.differences) >= 1, (
        "Expected at least 1 difference for heading level mismatch, got 0"
    )
    # Verify difference fields are populated
    for diff in result.differences:
        assert diff.diff_type in ("heading", "code_block", "table"), (
            f"Unexpected diff_type: {diff.diff_type}"
        )
        assert diff.section, "section must be non-empty"
        assert diff.expected, "expected must be non-empty"
        assert diff.actual, "actual must be non-empty"


@given(base_doc=markdown_with_code_blocks())
@settings(max_examples=100)
def test_code_block_content_difference_detected(base_doc: str) -> None:
    """Code block content differences are detected by the bilingual comparator.

    # Feature: new-relic-e2e-verification, Property 5: Structural differences are detected

    **Validates: Requirements 7.2, 7.3**

    For any Markdown document with at least one code block, injecting a
    content difference produces a comparison result with status == "fail"
    and non-empty differences list with diff_type == "code_block".
    """
    modified = _inject_code_block_content_difference(base_doc)

    # If injection didn't change anything, skip
    if modified == base_doc:
        return

    with tempfile.TemporaryDirectory() as tmpdir:
        ja_path = os.path.join(tmpdir, "ja", "setup-guide.md")
        en_path = os.path.join(tmpdir, "en", "setup-guide.md")
        os.makedirs(os.path.dirname(ja_path))
        os.makedirs(os.path.dirname(en_path))

        with open(ja_path, "w", encoding="utf-8") as f:
            f.write(base_doc)
        with open(en_path, "w", encoding="utf-8") as f:
            f.write(modified)

        result = compare_setup_guides(ja_path, en_path)

    assert result.status == "fail", (
        f"Expected status 'fail' for code block content difference, got '{result.status}'"
    )
    assert len(result.differences) >= 1, (
        "Expected at least 1 difference for code block content difference, got 0"
    )
    # At least one difference should be a code_block type
    code_block_diffs = [d for d in result.differences if d.diff_type == "code_block"]
    assert len(code_block_diffs) >= 1, (
        f"Expected at least 1 code_block difference, got {len(code_block_diffs)}. "
        f"All diffs: {result.differences}"
    )
    for diff in code_block_diffs:
        assert diff.section, "section must be non-empty"
        assert diff.expected, "expected must be non-empty"
        assert diff.actual, "actual must be non-empty"


@given(base_doc=markdown_with_tables())
@settings(max_examples=100)
def test_table_row_count_mismatch_detected(base_doc: str) -> None:
    """Table row count mismatches are detected by the bilingual comparator.

    # Feature: new-relic-e2e-verification, Property 5: Structural differences are detected

    **Validates: Requirements 7.2, 7.3**

    For any Markdown document with at least one table, injecting an extra
    row produces a comparison result with status == "fail" and non-empty
    differences list with diff_type == "table".
    """
    modified = _inject_table_row_count_mismatch(base_doc)

    # If injection didn't change anything, skip
    if modified == base_doc:
        return

    with tempfile.TemporaryDirectory() as tmpdir:
        ja_path = os.path.join(tmpdir, "ja", "setup-guide.md")
        en_path = os.path.join(tmpdir, "en", "setup-guide.md")
        os.makedirs(os.path.dirname(ja_path))
        os.makedirs(os.path.dirname(en_path))

        with open(ja_path, "w", encoding="utf-8") as f:
            f.write(base_doc)
        with open(en_path, "w", encoding="utf-8") as f:
            f.write(modified)

        result = compare_setup_guides(ja_path, en_path)

    assert result.status == "fail", (
        f"Expected status 'fail' for table row count mismatch, got '{result.status}'"
    )
    assert len(result.differences) >= 1, (
        "Expected at least 1 difference for table row count mismatch, got 0"
    )
    # At least one difference should be a table type
    table_diffs = [d for d in result.differences if d.diff_type == "table"]
    assert len(table_diffs) >= 1, (
        f"Expected at least 1 table difference, got {len(table_diffs)}. "
        f"All diffs: {result.differences}"
    )
    for diff in table_diffs:
        assert diff.section, "section must be non-empty"
        assert diff.expected, "expected must be non-empty"
        assert diff.actual, "actual must be non-empty"


@given(base_doc=markdown_with_tables())
@settings(max_examples=100)
def test_table_column_count_mismatch_detected(base_doc: str) -> None:
    """Table column count mismatches are detected by the bilingual comparator.

    # Feature: new-relic-e2e-verification, Property 5: Structural differences are detected

    **Validates: Requirements 7.2, 7.3**

    For any Markdown document with at least one table, injecting an extra
    column produces a comparison result with status == "fail" and non-empty
    differences list with diff_type == "table".
    """
    modified = _inject_table_column_count_mismatch(base_doc)

    # If injection didn't change anything, skip
    if modified == base_doc:
        return

    with tempfile.TemporaryDirectory() as tmpdir:
        ja_path = os.path.join(tmpdir, "ja", "setup-guide.md")
        en_path = os.path.join(tmpdir, "en", "setup-guide.md")
        os.makedirs(os.path.dirname(ja_path))
        os.makedirs(os.path.dirname(en_path))

        with open(ja_path, "w", encoding="utf-8") as f:
            f.write(base_doc)
        with open(en_path, "w", encoding="utf-8") as f:
            f.write(modified)

        result = compare_setup_guides(ja_path, en_path)

    assert result.status == "fail", (
        f"Expected status 'fail' for table column count mismatch, got '{result.status}'"
    )
    assert len(result.differences) >= 1, (
        "Expected at least 1 difference for table column count mismatch, got 0"
    )
    # At least one difference should be a table type
    table_diffs = [d for d in result.differences if d.diff_type == "table"]
    assert len(table_diffs) >= 1, (
        f"Expected at least 1 table difference, got {len(table_diffs)}. "
        f"All diffs: {result.differences}"
    )
    for diff in table_diffs:
        assert diff.section, "section must be non-empty"
        assert diff.expected, "expected must be non-empty"
        assert diff.actual, "actual must be non-empty"
