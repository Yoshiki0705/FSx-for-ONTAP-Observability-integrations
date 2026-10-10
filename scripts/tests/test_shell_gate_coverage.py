"""Every bilingual docs/ja tree the shell gate is meant to cover must be visited.

Why this exists
---------------
``shared/scripts/check-bilingual-sync.sh`` is a directory-walk gate: it decides
what to parity-check by enumerating directories, not by reading a fixed list.
That is the same shape as the CloudFormation ``CFN_TEMPLATES`` glob, and it had
the same latent failure. Its vendor loop reached ``integrations/<vendor>/docs/``
one level deep; the pattern setup guides live two levels down
(``integrations/pipeline-verification/pattern-N/docs/{ja,en}``) and were never
gated for JA/EN parity. Nothing failed -- the gate exited 0 while never looking
at those trees, which is indistinguishable from them being in sync.

``test_cfn_template_coverage.py`` repairs that mechanism for templates by
deriving the expectation from disk and failing when the gate's enumeration
misses a disk item. This test is the shell-gate equivalent. It drives the gate
in its ``--list-scope`` mode (which prints the ja/ directory of every pair the
gate would check, using the same enumeration loops as a normal run) and asserts
that every ``docs/ja`` tree on disk, within the roots the gate declares it
covers, appears in that enumeration. A revert of the gate's nested ``find`` to a
one-level walk drops the pattern trees from the listing and turns this test red.

Scope
-----
The gate deliberately covers two roots, mirrored here rather than imposing a
uniform rule the gate does not use:

  - the top-level ``docs/`` pair, and
  - every ``docs/ja`` under ``integrations/`` at any depth.

``management-console/docs`` is intentionally **not** in the gate's scope, so it
is intentionally not expected here either -- the test asserts what the gate is
meant to cover, not a wider rule that would flag trees the gate never claimed.

Guard the guard
---------------
Every failure mode of a coverage test is a silent pass: an empty enumeration, a
disk walk that finds nothing, a listing mode that drifted from the real check.
The tests below pin a non-empty enumeration, pin the disk walk to known trees,
and assert the two-levels-deep pattern case specifically, so a regression to the
one-level walk is caught rather than passing vacuously.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE = REPO_ROOT / "shared" / "scripts" / "check-bilingual-sync.sh"

PRUNE = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".hypothesis",
    ".playwright-mcp",
    "dist",
    ".private",
    ".agents",
}


def _pruned(path: Path) -> bool:
    return any(part in PRUNE for part in path.relative_to(REPO_ROOT).parts)


def _normalize(rel: str) -> str:
    """Collapse the vendor loop's ``<dir>//docs/ja`` double slash.

    The gate builds vendor paths as ``"$vendor_dir/docs/ja"`` where
    ``$vendor_dir`` already ends in ``/``. Normalizing here keeps the gate's
    enumeration code untouched while the comparison stays apples-to-apples.
    """
    while "//" in rel:
        rel = rel.replace("//", "/")
    return rel


def _gate_enumeration() -> list[str]:
    """The ja/ dirs the gate would check, from its own ``--list-scope`` output.

    Driving the gate rather than re-parsing its globs means the test sees the
    exact set the real run enumerates; the two cannot drift apart.
    """
    result = subprocess.run(
        ["bash", str(GATE), "--list-scope"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        f"`check-bilingual-sync.sh --list-scope` exited {result.returncode}: "
        f"{result.stderr.strip()}"
    )
    scope = [
        _normalize(line[len("SCOPE ") :].strip())
        for line in result.stdout.splitlines()
        if line.startswith("SCOPE ")
    ]
    return sorted(set(scope))


def _discover_ja_dirs() -> list[str]:
    """Every docs/ja tree the gate is meant to cover, derived from disk.

    Mirrors the gate's two declared roots: the top-level ``docs/`` pair and
    every ``docs/ja`` under ``integrations/`` at any depth. Does not include
    ``management-console/docs``, which the gate does not walk.
    """
    found: set[str] = set()

    top = REPO_ROOT / "docs" / "ja"
    if top.is_dir():
        found.add("docs/ja")

    integrations = REPO_ROOT / "integrations"
    if integrations.is_dir():
        for path in integrations.rglob("docs/ja"):
            if path.is_dir() and not _pruned(path):
                found.add(str(path.relative_to(REPO_ROOT)))

    return sorted(found)


@pytest.fixture(scope="module")
def enumeration() -> list[str]:
    return _gate_enumeration()


# --------------------------------------------------------------------------
# The real tree
# --------------------------------------------------------------------------


def test_gate_enumerates_something(enumeration: list[str]) -> None:
    """Guards the guard: an empty enumeration would make the diff vacuous."""
    assert len(enumeration) >= 10, (
        f"--list-scope produced {enumeration!r}; if the mode or the loops "
        "broke, this test would otherwise compare against nothing and pass"
    )


def test_every_ja_dir_on_disk_is_enumerated(enumeration: list[str]) -> None:
    on_disk = set(_discover_ja_dirs())
    visited = set(enumeration)
    skipped = on_disk - visited
    assert not skipped, (
        "these docs/ja trees exist on disk within the gate's declared roots "
        "but check-bilingual-sync.sh does not enumerate them, so their JA/EN "
        f"parity is never checked: {sorted(skipped)}"
    )


def test_enumeration_has_no_phantom_dirs(enumeration: list[str]) -> None:
    """Anything the gate claims to check must actually exist on disk."""
    missing = [d for d in enumeration if not (REPO_ROOT / d).is_dir()]
    assert not missing, (
        f"--list-scope reported directories that do not exist: {missing}"
    )


def test_two_level_pattern_docs_are_enumerated(enumeration: list[str]) -> None:
    """The specific regression: docs/ja two levels deep under integrations/.

    A revert to the one-level vendor loop (dropping the nested ``find``) would
    stop visiting the pipeline-verification pattern trees, and this asserts they
    stay enumerated.
    """
    visited = set(enumeration)
    patterns_root = REPO_ROOT / "integrations" / "pipeline-verification"
    deep = [
        str(p.relative_to(REPO_ROOT))
        for p in patterns_root.rglob("docs/ja")
        if p.is_dir()
        and p.parent.parent.name.startswith("pattern-")
    ]
    # Only assert when such trees exist (patterns land one at a time).
    skipped = [d for d in deep if d not in visited]
    assert not skipped, (
        "pipeline-verification pattern docs/ja trees exist but are not "
        f"enumerated by the gate: {sorted(skipped)}. The nested find has likely "
        "regressed to a one-level vendor walk."
    )


# --------------------------------------------------------------------------
# Negative controls
# --------------------------------------------------------------------------


def test_discovery_finds_known_trees() -> None:
    """If the disk walk stopped finding anything, coverage would pass trivially."""
    found = set(_discover_ja_dirs())
    for expected in (
        "docs/ja",
        "integrations/datadog/docs/ja",
    ):
        assert expected in found, f"discovery missed {expected}: {sorted(found)}"


def test_discovery_reaches_two_levels_deep() -> None:
    """Discovery must reach the depth the one-level glob missed.

    Guarded so it does not force a specific pattern to exist forever, but as
    long as one is on disk, discovery must see it -- a walk that only reached one
    level would silently drop it and the coverage comparison would pass with a
    buggy gate.
    """
    deep = (
        REPO_ROOT
        / "integrations"
        / "pipeline-verification"
        / "pattern-2-prometheus"
        / "docs"
        / "ja"
    )
    if deep.is_dir():
        rel = str(deep.relative_to(REPO_ROOT))
        assert rel in _discover_ja_dirs(), (
            f"discovery did not find {rel}; it is not walking integrations/ "
            "recursively"
        )
