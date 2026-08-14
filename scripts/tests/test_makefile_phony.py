"""Every Makefile target must be declared .PHONY.

Why this exists
---------------
When a target shares its name with a real directory, make finds the directory,
concludes the target is already up to date, prints "up to date" and runs no
recipe -- exiting 0. A gate that never runs is indistinguishable from a gate
that passes, and the failure is silent in the direction that matters.

This repository is full of directory names that read like verbs: docs/,
scripts/, guard/, shared/, integrations/. `make docs` and `make security` are
exactly the shapes that break.

The tests below check the real Makefile and then check the checker itself
against a deliberately broken Makefile, because a parser that silently matches
nothing would also report a clean result.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = REPO_ROOT / "Makefile"

# A target definition: name at column 0 followed by ':' that is not part of an
# assignment operator (:=, ::=, +=, ?=).
TARGET_RE = re.compile(r"^(?P<name>[A-Za-z0-9_][A-Za-z0-9_.%/-]*)\s*:(?![=:])")
ASSIGN_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_]*\s*(:=|::=|\+=|\?=|=)")


def _logical_lines(text: str) -> list[str]:
    """Join backslash continuations so multi-line .PHONY reads as one line."""
    joined: list[str] = []
    buffer = ""
    for raw in text.splitlines():
        if raw.rstrip().endswith("\\"):
            buffer += raw.rstrip()[:-1] + " "
            continue
        joined.append(buffer + raw)
        buffer = ""
    if buffer:
        joined.append(buffer)
    return joined


def defined_targets(text: str) -> set[str]:
    """Target names defined in a Makefile.

    Excludes variable assignments, special targets (.PHONY and friends), and
    pattern rules, none of which .PHONY applies to.
    """
    found: set[str] = set()
    for line in _logical_lines(text):
        if not line or line[0] in " \t#":
            continue
        if ASSIGN_RE.match(line):
            continue
        match = TARGET_RE.match(line)
        if not match:
            continue
        name = match.group("name")
        if name.startswith(".") or "%" in name:
            continue
        found.add(name)
    return found


def declared_phony(text: str) -> set[str]:
    """Names listed across all .PHONY declarations."""
    names: set[str] = set()
    for line in _logical_lines(text):
        if not line.startswith(".PHONY"):
            continue
        _, _, rest = line.partition(":")
        names.update(rest.split())
    return names


def undeclared_targets(text: str) -> set[str]:
    return defined_targets(text) - declared_phony(text)


# --------------------------------------------------------------------------
# The real Makefile
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def makefile_text() -> str:
    assert MAKEFILE.is_file(), f"{MAKEFILE} is missing"
    return MAKEFILE.read_text(encoding="utf-8")


def test_makefile_exists_and_defines_targets(makefile_text: str) -> None:
    """Guards the guard: an empty parse would make every other test vacuous."""
    targets = defined_targets(makefile_text)
    assert len(targets) >= 10, (
        f"parsed only {len(targets)} targets ({sorted(targets)}); the parser is "
        "probably not matching this Makefile's syntax, which would make the "
        "phony check pass without checking anything"
    )


def test_all_targets_are_declared_phony(makefile_text: str) -> None:
    missing = undeclared_targets(makefile_text)
    assert not missing, (
        "these Makefile targets are not declared .PHONY: "
        f"{sorted(missing)}. If any shares a name with a file or directory, "
        "make will report it up to date and run no recipe."
    )


def test_no_target_is_shadowed_by_a_real_path(makefile_text: str) -> None:
    """The subset of the above that fails silently today rather than someday."""
    shadowed = sorted(
        name
        for name in defined_targets(makefile_text) - declared_phony(makefile_text)
        if (REPO_ROOT / name).exists()
    )
    assert not shadowed, (
        f"targets shadowed by an existing path and not .PHONY: {shadowed}. "
        "`make <name>` currently runs nothing and exits 0."
    )


def test_phony_declares_no_unknown_names(makefile_text: str) -> None:
    """A .PHONY entry with no matching target is a renamed or deleted target."""
    stale = declared_phony(makefile_text) - defined_targets(makefile_text)
    assert not stale, (
        f".PHONY lists names with no target definition: {sorted(stale)}. "
        "Either the target was renamed or the recipe was removed."
    )


# --------------------------------------------------------------------------
# Negative controls: prove the checker detects a break
# --------------------------------------------------------------------------

BROKEN = """\
PY := python3
SRC = src

.PHONY: test lint

test:
\t$(PY) -m pytest

lint:
\truff check $(SRC)

security:
\tbandit -r $(SRC)

docs:
\tmkdocs build
"""


def test_checker_detects_targets_missing_from_phony() -> None:
    assert undeclared_targets(BROKEN) == {"security", "docs"}


def test_checker_ignores_variable_assignments() -> None:
    """`PY :=` and `SRC =` must not be mistaken for targets named PY and SRC."""
    targets = defined_targets(BROKEN)
    assert "PY" not in targets
    assert "SRC" not in targets
    assert targets == {"test", "lint", "security", "docs"}


def test_checker_ignores_pattern_rules_and_special_targets() -> None:
    text = ".PHONY: build\n\nprint-%:\n\t@echo $($*)\n\nbuild:\n\ttrue\n"
    assert defined_targets(text) == {"build"}
    assert undeclared_targets(text) == set()


def test_checker_reads_multiline_phony() -> None:
    text = ".PHONY: one \\\n        two\n\none:\n\ttrue\n\ntwo:\n\ttrue\n"
    assert declared_phony(text) == {"one", "two"}
    assert undeclared_targets(text) == set()
