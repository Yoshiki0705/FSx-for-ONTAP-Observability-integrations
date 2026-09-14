"""Python that pytest runs must also be something ruff and bandit read.

Why this exists
---------------
`shared/scripts` was absent from `PY_SRC` while `scripts` and `shared/python`
were present. `make lint-py` and `make security` therefore reported clean on
this repository having never read 3,351 lines of it. The first run over that
directory found 13 ruff findings and 2 bandit B310.

The part that made it widen rather than hold still: `shared/scripts/tests` was
added to `PYTEST_DIRS`, so pytest ran it while no linter looked at it. Every
test added under a directory outside `PY_SRC` adds unchecked code, and nothing
reports that.

Adding the path repairs today's instance. This test derives the expectation from
`PYTEST_DIRS`, so the next suite added under a directory that no linter reads
fails here until the scope is widened to match. It is the same argument as
`test_test_dir_coverage.py`, applied to the other list.

Scope, stated plainly
---------------------
This checks that the two Makefile lists agree. It does not check that ruff and
bandit actually find anything, and a clean report from either still has to be
read against what they were pointed at. Agreement between two lists is a
necessary condition, not evidence of coverage.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Directories that hold no Python, or whose Python is deliberately out of lint
# scope, may appear in PYTEST_DIRS without a PY_SRC entry. Nothing qualifies
# today; the set exists so that an exemption has to be written down here rather
# than achieved by omission.
EXEMPT_PREFIXES: frozenset[str] = frozenset()


def _makefile_list(name: str) -> list[str]:
    """Read a path list from the Makefile via `make print-<VAR>`.

    Read through make rather than by parsing text, so this sees the same value
    the recipes see. MAKELEVEL and friends are stripped because a nested make
    otherwise writes "make[1]: Entering directory ..." onto stdout, which would
    be parsed as though those words were paths -- the failure
    test_test_dir_coverage.py already hit.
    """
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("MAKELEVEL", "MAKEFLAGS", "MFLAGS")
    }
    result = subprocess.run(
        ["make", "--no-print-directory", f"print-{name}"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
        check=False,
    )
    assert result.returncode == 0, (
        f"`make print-{name}` failed: {result.stderr.strip()}"
    )
    values = result.stdout.split()
    stray = [v for v in values if v.startswith("make") or v.startswith("'")]
    assert not stray, (
        f"`make print-{name}` emitted non-path output {stray}; the parse below "
        "would treat it as a directory"
    )
    assert values, f"{name} resolved to nothing"
    return values


def _covered_by_py_src(path: str, py_src: list[str]) -> bool:
    """Is `path` inside one of the PY_SRC roots?

    ruff and bandit both recurse, so a parent entry covers a child directory.
    """
    candidate = Path(path)
    return any(
        candidate == Path(root) or Path(root) in candidate.parents for root in py_src
    )


def test_every_pytest_dir_with_python_is_in_py_src() -> None:
    """A suite pytest runs must sit under something the linters read."""
    pytest_dirs = _makefile_list("PYTEST_DIRS")
    py_src = _makefile_list("PY_SRC")

    uncovered = []
    for directory in pytest_dirs:
        if any(directory.startswith(prefix) for prefix in EXEMPT_PREFIXES):
            continue
        if not (REPO_ROOT / directory).is_dir():
            # test_test_dir_coverage.py owns the existence question.
            continue
        if not any((REPO_ROOT / directory).rglob("*.py")):
            continue
        if not _covered_by_py_src(directory, py_src):
            uncovered.append(directory)

    assert not uncovered, (
        "these directories hold Python that pytest runs, but no PY_SRC entry "
        f"covers them, so ruff and bandit never read it: {sorted(uncovered)}. "
        "Add the directory (or its parent) to PY_SRC in the Makefile, or add an "
        "exemption to EXEMPT_PREFIXES here with the reason."
    )


def test_py_src_entries_exist() -> None:
    """A path that no longer exists silently narrows the scope.

    ruff and bandit do not error on a missing directory in every invocation
    shape, so a renamed tree can drop out of scope while both still report
    clean.
    """
    missing = [root for root in _makefile_list("PY_SRC") if not (REPO_ROOT / root).exists()]
    assert not missing, (
        f"PY_SRC names paths that do not exist: {missing}. The scan silently "
        "covers less than the list suggests"
    )


def test_shared_scripts_is_in_scope() -> None:
    """The specific regression from #80, pinned by name.

    The general assertion above only holds while shared/scripts/tests exists.
    Deleting that directory would make it pass again with shared/scripts
    unlinted, which is the state #80 describes.
    """
    assert _covered_by_py_src("shared/scripts", _makefile_list("PY_SRC")), (
        "shared/scripts must stay in PY_SRC. It was missing while `scripts` and "
        "`shared/python` were present, and both linters reported clean without "
        "reading it -- see #80"
    )
