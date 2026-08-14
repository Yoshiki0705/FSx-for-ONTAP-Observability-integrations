"""The always-loaded context budget must hold, and its checker must work.

Why this exists
---------------
AGENTS.md is read on every turn and cannot be made conditional. It had reached
43,518 bytes, most of it relevant to one kind of work: 173 lines of S3 Access
Point and Active Directory pitfalls, a per-vendor API table, and an
add-a-vendor procedure. Extraction to docs/agent/ brought it to 26,576 bytes.

The checker enforces the direction of that move as much as the size. Content
relocated into .kiro/ would look tidy and would silently leave the published
documentation, because .kiro/ is not committed here. So an index target that
exists but is untracked is treated as a failure, not a warning.

Each check below is paired with a case that breaks it, because a checker that
silently inspects nothing also reports success.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKER = REPO_ROOT / "scripts/check_agent_context_budget.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("cacb", CHECKER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def mod():
    assert CHECKER.is_file(), f"{CHECKER} is missing"
    return _load_module()


# --------------------------------------------------------------------------
# The real repository
# --------------------------------------------------------------------------


def test_checker_passes_on_the_real_repo() -> None:
    # sys.executable, not .venv/bin/python: CI installs into the runner's
    # interpreter and has no .venv, so a hardcoded path would fail there while
    # passing locally.
    result = subprocess.run(
        [sys.executable, str(CHECKER)],
        capture_output=True, text=True, cwd=REPO_ROOT, timeout=120,
    )
    assert result.returncode == 0, (
        f"context budget check failed:\n{result.stderr}"
    )


def test_agents_md_is_within_budget(mod) -> None:
    size = (REPO_ROOT / "AGENTS.md").stat().st_size
    assert size <= mod.AGENTS_MAX_BYTES, (
        f"AGENTS.md is {size:,} bytes against a {mod.AGENTS_MAX_BYTES:,} budget"
    )


def test_extracted_docs_exist_and_are_tracked() -> None:
    """The three relocated sections must be readable on GitHub, not just
    locally. .gitignore in this repo has swallowed new documentation before."""
    tracked = set(
        subprocess.run(["git", "ls-files"], cwd=REPO_ROOT,
                       capture_output=True, text=True).stdout.split()
    )
    for name in (
        "docs/agent/non-obvious-patterns.md",
        "docs/agent/vendor-api-reference.md",
        "docs/agent/deploying-and-adding-vendors.md",
    ):
        assert (REPO_ROOT / name).is_file(), f"{name} is missing"
        assert name in tracked, (
            f"{name} exists but is untracked, so AGENTS.md points readers at "
            "something they cannot see"
        )


def test_steering_loaders_are_thin(mod) -> None:
    steering = REPO_ROOT / ".kiro/steering"
    if not steering.is_dir():
        pytest.skip(".kiro/steering not present")
    for path in steering.glob("*.md"):
        text = path.read_text(encoding="utf-8")
        if "#[[file:" not in text:
            continue  # standalone guidance, not a loader
        assert path.stat().st_size <= mod.STEERING_LOADER_MAX_BYTES, (
            f"{path.name} is {path.stat().st_size:,} bytes; a loader holding its "
            "own prose puts that prose somewhere unpublished"
        )


def test_auto_steering_files_declare_name_and_description() -> None:
    """inclusion: auto without both fields is never registered and never read,
    with no error. The loaders added here are useless if that happens."""
    steering = REPO_ROOT / ".kiro/steering"
    if not steering.is_dir():
        pytest.skip(".kiro/steering not present")
    for path in steering.glob("*.md"):
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---"):
            continue
        end = text.find("\n---", 3)
        front = text[3:end] if end != -1 else ""
        if "inclusion: auto" not in front:
            continue
        assert "\nname:" in front, f"{path.name}: inclusion:auto without name"
        assert "\ndescription:" in front, (
            f"{path.name}: inclusion:auto without description"
        )


# --------------------------------------------------------------------------
# Negative controls
# --------------------------------------------------------------------------


def _run_checks(mod, root: Path, tracked: set[str]) -> list[str]:
    """Point the module at a synthetic tree."""
    mod.REPO_ROOT = root
    mod.AGENT_DOCS_DIR = root / "docs/agent"
    mod.tracked_files = lambda: tracked
    problems: list[str] = []
    mod.check_agents_md(problems)
    mod.check_steering_loaders(problems)
    mod.check_agent_docs_are_tracked(problems)
    return problems


@pytest.fixture
def sandbox(tmp_path: Path):
    (tmp_path / "docs/agent").mkdir(parents=True)
    (tmp_path / ".kiro/steering").mkdir(parents=True)
    return tmp_path


def test_detects_oversized_agents_md(tmp_path: Path) -> None:
    mod = _load_module()
    (tmp_path / "docs/agent").mkdir(parents=True)
    (tmp_path / "AGENTS.md").write_text("x" * (mod.AGENTS_MAX_BYTES + 1))
    problems = _run_checks(mod, tmp_path, set())
    assert any("over the" in p for p in problems), problems


def test_detects_link_to_missing_file(sandbox: Path) -> None:
    mod = _load_module()
    (sandbox / "AGENTS.md").write_text("See [notes](docs/agent/gone.md)\n")
    problems = _run_checks(mod, sandbox, {"AGENTS.md"})
    assert any("does not exist" in p for p in problems), problems


def test_detects_link_to_untracked_file(sandbox: Path) -> None:
    """The failure mode that motivated the checker: the file is there, so the
    link resolves locally, and it is absent for everyone else."""
    mod = _load_module()
    (sandbox / "docs/agent/notes.md").write_text("body\n")
    (sandbox / "AGENTS.md").write_text("See [notes](docs/agent/notes.md)\n")
    problems = _run_checks(mod, sandbox, {"AGENTS.md"})
    assert any("not tracked" in p for p in problems), problems


def test_detects_fat_steering_loader(sandbox: Path) -> None:
    mod = _load_module()
    (sandbox / "AGENTS.md").write_text("ok\n")
    (sandbox / "docs/agent/notes.md").write_text("body\n")
    loader = sandbox / ".kiro/steering/loader.md"
    loader.write_text(
        "---\ninclusion: auto\nname: loader\ndescription: d\n---\n"
        + "prose " * 600
        + "\n#[[file:docs/agent/notes.md]]\n"
    )
    problems = _run_checks(mod, sandbox, {"AGENTS.md", "docs/agent/notes.md"})
    assert any("loader should" in p for p in problems), problems


def test_detects_loader_pointing_at_nothing(sandbox: Path) -> None:
    mod = _load_module()
    (sandbox / "AGENTS.md").write_text("ok\n")
    (sandbox / ".kiro/steering/loader.md").write_text(
        "---\ninclusion: auto\nname: loader\ndescription: d\n---\n"
        "#[[file:docs/agent/absent.md]]\n"
    )
    problems = _run_checks(mod, sandbox, {"AGENTS.md"})
    assert any("does not exist" in p for p in problems), problems


def test_clean_sandbox_reports_nothing(sandbox: Path) -> None:
    """Guards the guard: if this produced problems, every control above would
    pass for the wrong reason."""
    mod = _load_module()
    (sandbox / "AGENTS.md").write_text("See [notes](docs/agent/notes.md)\n")
    (sandbox / "docs/agent/notes.md").write_text("body\n")
    (sandbox / ".kiro/steering/loader.md").write_text(
        "---\ninclusion: auto\nname: loader\ndescription: d\n---\n"
        "#[[file:docs/agent/notes.md]]\n"
    )
    problems = _run_checks(mod, sandbox, {"AGENTS.md", "docs/agent/notes.md"})
    assert problems == []
