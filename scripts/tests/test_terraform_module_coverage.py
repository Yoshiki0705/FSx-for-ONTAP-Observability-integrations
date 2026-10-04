"""Every Terraform module on disk must be in the Makefile's TF_MODULE_DIRS.

Why this exists
---------------
TF_MODULE_DIRS is the input to ``make terraform`` (fmt, init, validate, and
the offline ``terraform test``). It is built from the one-level glob
``terraform/*/versions.tf``. The same shape of glob already let cfn-lint and
cfn-guard exit 0 for a template two directories down that they never read
(see test_cfn_template_coverage.py). A module nested deeper, or one without a
versions.tf, would be skipped by ``make terraform`` with the same silence.

This test derives the expectation from the filesystem: every directory under
terraform/ that holds a ``*.tf`` file, at any depth, must be in the list. It
also requires each listed module to carry what the gate depends on: a
versions.tf, at least one tests/*.tftest.hcl (``terraform test`` passes with
zero test files), and a tracked .terraform.lock.hcl (``init -lockfile=readonly``
needs it, and an untracked one would pass locally and fail in CI).

Guard the guard
---------------
A coverage test fails open: an empty list or a discovery walk that finds
nothing both pass. The tests below pin a non-empty list with a known module and
run discovery over a temporary tree with a two-level module, so a discovery
that only walks one level is caught.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

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
    ".terraform",
}

KNOWN_MODULE = "terraform/fsxn-monitoring-dashboard"


def _makefile_list(name: str) -> list[str]:
    """Read a path list from the Makefile via ``make print-<VAR>``.

    Same approach as test_cfn_template_coverage.py: MAKELEVEL/MAKEFLAGS are
    stripped so a nested make does not leak "Entering directory" lines.
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
    )
    assert result.returncode == 0, (
        f"`make print-{name}` failed: {result.stderr.strip()}"
    )
    values = result.stdout.split()
    stray = [v for v in values if v.startswith("make") or v.startswith("'")]
    assert not stray, f"`make print-{name}` emitted non-path output {stray}"
    return values


def _discover_tf_dirs(root: Path) -> list[str]:
    """Directories under ``root/terraform`` holding a ``*.tf`` file, any depth."""
    base = root / "terraform"
    if not base.is_dir():
        return []
    found: set[str] = set()
    for path in base.rglob("*.tf"):
        rel = path.relative_to(root)
        if path.is_file() and not any(part in PRUNE for part in rel.parts):
            found.add(str(rel.parent))
    return sorted(found)


@pytest.fixture(scope="module")
def tf_module_dirs() -> list[str]:
    return _makefile_list("TF_MODULE_DIRS")


def test_makefile_exposes_a_nonempty_tf_module_dirs(tf_module_dirs: list[str]) -> None:
    assert KNOWN_MODULE in tf_module_dirs, (
        f"TF_MODULE_DIRS resolved to {tf_module_dirs!r}; an empty or renamed "
        "variable would make the coverage comparison vacuous"
    )


def test_every_tf_dir_on_disk_is_in_tf_module_dirs(tf_module_dirs: list[str]) -> None:
    orphaned = set(_discover_tf_dirs(REPO_ROOT)) - set(tf_module_dirs)
    assert not orphaned, (
        "these directories hold .tf files but are not in the Makefile's "
        f"TF_MODULE_DIRS, so `make terraform` skips them silently: {sorted(orphaned)}"
    )


def test_each_module_has_versions_tests_and_tracked_lock(
    tf_module_dirs: list[str],
) -> None:
    for d in tf_module_dirs:
        module = REPO_ROOT / d
        assert (module / "versions.tf").is_file(), f"{d} has no versions.tf"
        assert list((module / "tests").glob("*.tftest.hcl")), (
            f"{d} has no tests/*.tftest.hcl; `terraform test` would pass with "
            "nothing to run"
        )
        lock = f"{d}/.terraform.lock.hcl"
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", lock],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, (
            f"{lock} is not tracked; `terraform init -lockfile=readonly` "
            "would fail in CI"
        )


def test_discovery_reaches_two_levels(tmp_path: Path) -> None:
    """Negative control: a nested module must be discovered, not skipped."""
    nested = tmp_path / "terraform" / "a" / "b"
    nested.mkdir(parents=True)
    (nested / "main.tf").write_text("# nested\n")
    vendored = tmp_path / "terraform" / "a" / ".terraform" / "modules" / "x"
    vendored.mkdir(parents=True)
    (vendored / "main.tf").write_text("# downloaded\n")
    assert _discover_tf_dirs(tmp_path) == ["terraform/a/b"]
