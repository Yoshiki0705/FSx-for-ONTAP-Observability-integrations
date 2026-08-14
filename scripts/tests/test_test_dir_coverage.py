"""Every test directory on disk must be listed in the Makefile's PYTEST_DIRS.

Why this exists
---------------
scripts/verification/tests/ (172 tests) and
shared/lambda-layers/log-parser/tests/ (12 tests) existed, passed, and were
referenced by no automation. CI listed vendors inline, AGENTS.md documented a
different and staler command, and neither list mentioned those two
directories. They ran only when a human remembered a command from the docs.

Nothing failed. The suites simply never executed, which looks identical to
having no coverage gap at all.

The fix is not to add the two paths -- that repairs today's instance and
leaves the mechanism intact. This test derives the expectation from the
filesystem, so the next suite added under a new directory fails here until it
is listed in the Makefile.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# Directories that are not part of the source tree.
PRUNE = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache",
         ".hypothesis", ".playwright-mcp", "dist", ".private"}


def _makefile_list(name: str) -> list[str]:
    """Read a path list from the Makefile via `make print-<VAR>`.

    Reading it through make rather than by parsing text means the test sees
    the same value the recipes see, including any variable composition.
    """
    result = subprocess.run(
        ["make", f"print-{name}"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, (
        f"`make print-{name}` failed: {result.stderr.strip()}"
    )
    return result.stdout.split()


def _discover_test_dirs() -> list[str]:
    """Every directory named `tests` that actually contains Python tests."""
    found: list[str] = []
    for path in REPO_ROOT.rglob("tests"):
        if not path.is_dir():
            continue
        if any(part in PRUNE for part in path.relative_to(REPO_ROOT).parts):
            continue
        # A tests/ dir with no test_*.py holds fixtures, not tests. guard/tests
        # is one of these: its suite is run-guard-selftest.sh, invoked by the
        # cfn-guard-selftest target rather than by pytest.
        if not any(path.rglob("test_*.py")):
            continue
        found.append(str(path.relative_to(REPO_ROOT)))
    return sorted(found)


@pytest.fixture(scope="module")
def pytest_dirs() -> list[str]:
    return _makefile_list("PYTEST_DIRS")


def test_makefile_exposes_a_nonempty_pytest_dirs(pytest_dirs: list[str]) -> None:
    """Guards the guard: an empty list would make the comparison vacuous."""
    assert len(pytest_dirs) >= 10, (
        f"PYTEST_DIRS resolved to {pytest_dirs!r}; if the variable were renamed "
        "this test would otherwise compare against nothing and pass"
    )


def test_every_test_dir_on_disk_is_in_pytest_dirs(pytest_dirs: list[str]) -> None:
    on_disk = set(_discover_test_dirs())
    listed = set(pytest_dirs)
    orphaned = on_disk - listed
    assert not orphaned, (
        f"these test directories exist and contain test_*.py but are not in "
        f"the Makefile's PYTEST_DIRS, so no automation runs them: "
        f"{sorted(orphaned)}"
    )


def test_pytest_dirs_has_no_stale_entries(pytest_dirs: list[str]) -> None:
    missing = [d for d in pytest_dirs if not (REPO_ROOT / d).is_dir()]
    assert not missing, (
        f"PYTEST_DIRS lists directories that do not exist: {missing}. "
        "pytest would fail on the path, or silently collect nothing."
    )


def test_ci_delegates_to_the_makefile() -> None:
    """CI must call the Makefile target, not re-list the paths.

    A second copy of the vendor list in ci.yaml is how the two lists drifted
    apart the first time.
    """
    ci = (REPO_ROOT / ".github/workflows/ci.yaml").read_text(encoding="utf-8")
    assert re.search(r"make\s+(test-py|test\b)", ci), (
        "ci.yaml does not invoke `make test-py`; if CI keeps its own path list "
        "it can once again inspect a different tree than local runs"
    )
    # The previous inline loop, which is the shape being prevented.
    assert "for vendor in datadog grafana" not in ci, (
        "ci.yaml still contains an inline vendor list; move it to the "
        "Makefile's VENDOR_TEST_DIRS so there is one list"
    )


# --------------------------------------------------------------------------
# Negative controls
# --------------------------------------------------------------------------


def test_discovery_finds_the_known_suites() -> None:
    """If discovery stopped finding anything, the coverage test would pass
    trivially. Pin a few directories that are known to exist."""
    found = set(_discover_test_dirs())
    for expected in (
        "shared/python/tests",
        "scripts/verification/tests",
        "shared/lambda-layers/log-parser/tests",
    ):
        assert expected in found, f"discovery missed {expected}: {sorted(found)}"


def test_discovery_skips_fixture_only_dirs() -> None:
    """guard/tests holds .yaml fixtures and a shell runner, no test_*.py.
    Including it would make pytest fail on an empty collection."""
    assert (REPO_ROOT / "guard/tests").is_dir()
    assert "guard/tests" not in _discover_test_dirs()


def test_discovery_prunes_vendored_trees() -> None:
    found = _discover_test_dirs()
    assert not [d for d in found if "node_modules" in d or ".venv" in d]
