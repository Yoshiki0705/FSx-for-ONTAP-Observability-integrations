"""The tracked pre-commit hook must stay tracked, executable and documented.

Why this exists
---------------
The hook's activation is `core.hooksPath`, which lives in `.git/config` — per-checkout and
untracked. **No check placed in this repository can verify that the hook is active in your
clone**, and there is a worse case than "not configured": a global `core.hooksPath` takes
effect instead, so the tracked hook is present, executable, and never runs. A sibling
project found its hook existed only in a global hooks directory on a single machine.

So this file asserts the part that is checkable, and nothing more:

  - the hook is tracked (a hook only in a local `.git/hooks` protects one machine)
  - it is executable (git skips a non-executable hook silently, with no error)
  - `make hooks` exists, so the activation step has a discoverable form
  - CONTRIBUTING documents it, so the one instruction that cannot be enforced cannot
    quietly disappear either

What it deliberately does not assert: the value of `core.hooksPath`. That would fail in CI,
where it is unset by design, and a gate that is red on arrival gets switched off.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK = REPO_ROOT / ".githooks" / "pre-commit"


# git reads its target from the environment before it reads `cwd`. During a commit,
# GIT_DIR and GIT_INDEX_FILE are set, and GIT_INDEX_FILE points at the transient index
# git built for that commit rather than at .git/index. `git ls-files` reads the index,
# so a test that inherits those answers about a different tree than the one it passed
# in `cwd` — and it answers confidently, with exit 0.
#
# Nothing in this repository runs pytest from a hook today, so this is a latent defect
# rather than an active one. It is scrubbed anyway because of the direction it fails in:
# the test would report the hook tracked and the guard healthy while inspecting the
# wrong index. A sibling project hit exactly this shape — a trunk-protection test passed
# standalone and failed inside the hook, which is the survivable order; the reverse would
# have shipped.
GIT_ENV_PREFIX = "GIT_"


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(GIT_ENV_PREFIX)}
    return subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _tracked(rel: str) -> bool:
    return _git("ls-files", "--error-unmatch", rel).returncode == 0


def _init_scratch_repo(path: Path) -> None:
    """Create a throwaway repository, refusing to touch the real one instead.

    `git init <path>` reads GIT_DIR from the environment too. Inherited, it **re-initialises
    the outer repository** and leaves the scratch directory not a repository at all — and
    the only trace is a `warning: re-init:` line, which `capture_output=True` swallows. That
    is a test with a destructive side effect on the tree it was run in, which is worse than
    a wrong verdict.

    So the environment is scrubbed for the init as well, and the outcome is then verified
    structurally rather than by matching the warning text: git must report the scratch
    directory's own `.git`. Checking the result is stronger than checking the message,
    because a future git could change the wording or stop warning at all.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith(GIT_ENV_PREFIX)}
    subprocess.run(
        ["git", "init", "-q", str(path)], env=env, check=True, capture_output=True
    )
    resolved = subprocess.run(
        ["git", "rev-parse", "--absolute-git-dir"],
        cwd=path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert Path(resolved).resolve() == (path / ".git").resolve(), (
        f"the scratch repository is not where it was asked for: git reports {resolved}. "
        "An inherited GIT_DIR re-initialised another repository instead."
    )


def test_the_hook_is_tracked() -> None:
    """An untracked hook protects the machine it was written on and no other."""
    assert _tracked(".githooks/pre-commit"), (
        ".githooks/pre-commit is not tracked. A hook that exists only in a local "
        "checkout cannot protect anyone else's commits."
    )


def test_the_hook_is_executable() -> None:
    """git skips a non-executable hook without an error, so this fails silently."""
    assert HOOK.is_file(), f"{HOOK} is missing"
    assert os.access(HOOK, os.X_OK), (
        f"{HOOK.relative_to(REPO_ROOT)} is not executable. git will skip it and report "
        "nothing. Fix with: chmod +x .githooks/pre-commit"
    )


def test_the_activation_step_has_a_make_target() -> None:
    """`make hooks` is the discoverable form of the one step that cannot be enforced."""
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    assert "\nhooks:" in makefile, "the `hooks` target is gone from the Makefile"
    assert "core.hooksPath" in makefile, (
        "the `hooks` target no longer sets core.hooksPath, so it no longer activates "
        "anything"
    )


def test_git_lookups_ignore_inherited_git_environment() -> None:
    """The answer must not change when GIT_DIR points somewhere else.

    Regression guard for the shape described above: without scrubbing, running this file
    from inside a hook makes `git ls-files` read a different index and still exit 0. A
    guard that reports healthy while inspecting the wrong tree is worse than one that
    errors, so this asserts the scrubbing rather than the symptom.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as scratch:
        _init_scratch_repo(Path(scratch))
        with mock.patch.dict(
            os.environ,
            {
                "GIT_DIR": str(Path(scratch) / ".git"),
                "GIT_INDEX_FILE": str(Path(scratch) / ".git" / "index"),
            },
        ):
            assert _tracked(".githooks/pre-commit"), (
                "an inherited GIT_DIR changed the answer, so this file's git lookups are "
                "reading whatever tree the caller was working on"
            )


def test_activation_is_documented() -> None:
    """The instruction that no gate can enforce must at least not vanish."""
    contributing = (REPO_ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    assert "make hooks" in contributing, (
        "CONTRIBUTING.md no longer tells a contributor to enable the hook. This is the "
        "only mechanism for a step that cannot be checked from inside the repository."
    )
    assert "core.hooksPath" in contributing, (
        "CONTRIBUTING.md no longer explains that activation is per-clone git config, "
        "which is why a fresh clone runs no hook and says nothing about it."
    )
