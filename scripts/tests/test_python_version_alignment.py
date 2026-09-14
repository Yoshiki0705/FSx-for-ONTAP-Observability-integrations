"""The FPolicy server image, the Lambda runtimes and the CI matrix share one Python.

Why this exists
---------------
Renovate raised a PR moving `shared/fpolicy-server/Dockerfile` from
`python:3.12-slim` to `python:3.14-slim` (#63). The image built and the server
ran on 3.14 -- that was checked before the PR was declined -- so nothing was
broken. The objection was that the FPolicy server would have been the only
component on 3.14 while 63 other files, every `Runtime: python3.12` in the
CloudFormation templates and the CI matrix, stayed on 3.12. `fpolicy_server.py`
would then run in production on a Python that no test in this repository
executes it against.

3.12 to 3.14 is a *minor* bump for Renovate, because the major is 3. The
`matchUpdateTypes: ["major"]` approval rule in `renovate.json` therefore did not
hold it, which is why that file now carries an explicit `<3.13` ceiling.

A ceiling in `renovate.json` constrains Renovate. It does not constrain a human
editing the Dockerfile, and it says nothing if the Lambda runtimes move and the
image is forgotten. This test is the part that holds regardless of who makes the
change: it derives all three versions from the files themselves and requires
them to agree, so raising Python means raising it everywhere in one commit.

Raising the version is expected to make this test fail until the last of the
three is updated. That is the intended behaviour, not an obstacle.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "shared" / "fpolicy-server" / "Dockerfile"
RENOVATE_CONFIG = REPO_ROOT / "renovate.json"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yaml"


def _dockerfile_python_version() -> str:
    """Read the ``X.Y`` from the ``FROM python:X.Y-...`` line."""
    match = re.search(
        r"^FROM\s+python:(\d+\.\d+)", DOCKERFILE.read_text(encoding="utf-8"), re.M
    )
    assert match, f"no `FROM python:X.Y` line found in {DOCKERFILE}"
    return match.group(1)


def _lambda_runtime_versions() -> set[str]:
    """Collect every ``Runtime: pythonX.Y`` across the CloudFormation templates.

    Read as text rather than parsed as YAML because the templates use
    CloudFormation short-form intrinsics (``!Ref``, ``!Sub``) that a plain YAML
    loader rejects.
    """
    versions: set[str] = set()
    for path in REPO_ROOT.rglob("*.yaml"):
        if any(
            part in {".venv", "node_modules", ".git", ".private"} for part in path.parts
        ):
            continue
        for match in re.finditer(
            r"Runtime:\s*python(\d+\.\d+)", path.read_text(encoding="utf-8")
        ):
            versions.add(match.group(1))
    return versions


def _ci_matrix_python_versions() -> set[str]:
    """Read the python-version matrix out of the CI workflow.

    Matched rather than parsed as YAML on purpose. PyYAML is only present here
    as a transitive dependency of cfn-lint; importing it in a test would make
    this file fail with an unrelated-looking ImportError the day cfn-lint stops
    pulling it. The assertion below covers the risk that comes with matching
    text: an empty result fails instead of passing vacuously.
    """
    text = CI_WORKFLOW.read_text(encoding="utf-8")
    versions: set[str] = set()
    for line in re.findall(r"^\s*python-version:\s*(\[.*\])\s*$", text, re.M):
        versions.update(re.findall(r"\d+\.\d+", line))
    assert versions, (
        "no python-version matrix found in the CI workflow. Either the matrix "
        "moved or its formatting changed -- this test reads it as text"
    )
    return versions


def test_lambda_runtimes_agree_on_one_python_version() -> None:
    """A split across the templates would make "the repository's Python" undefined."""
    versions = _lambda_runtime_versions()
    assert len(versions) == 1, (
        f"CloudFormation templates declare more than one Python runtime: "
        f"{sorted(versions)}. Pick one before raising it anywhere"
    )


def test_fpolicy_image_matches_the_lambda_runtime() -> None:
    """The container the FPolicy server runs in tracks the rest of the repository."""
    image_version = _dockerfile_python_version()
    runtime_versions = _lambda_runtime_versions()

    assert image_version in runtime_versions, (
        f"{DOCKERFILE.relative_to(REPO_ROOT)} is on Python {image_version} while "
        f"the Lambda runtimes are on {sorted(runtime_versions)}. Raise both in "
        "the same commit, and the CI matrix and the renovate.json ceiling with "
        "them -- see #63"
    )


def test_ci_matrix_matches_the_lambda_runtime() -> None:
    """Tests must run on the Python the code is deployed on.

    Without this, the suite can be green on one version while everything ships
    on another, which looks identical to being tested.
    """
    assert _ci_matrix_python_versions() == _lambda_runtime_versions()


def test_renovate_ceiling_admits_the_current_version_and_excludes_the_next() -> None:
    """The ceiling has to track the version in use, not a fixed number.

    A ceiling left behind after an upgrade blocks patch releases for the
    version actually deployed, and does so silently -- Renovate simply stops
    opening PRs, which is indistinguishable from there being none to open.
    """
    import json

    config = json.loads(RENOVATE_CONFIG.read_text(encoding="utf-8"))
    ceilings = [
        rule["allowedVersions"]
        for rule in config.get("packageRules", [])
        if rule.get("matchPackageNames") == ["python"]
        and "allowedVersions" in rule
    ]
    assert len(ceilings) == 1, (
        "expected exactly one allowedVersions ceiling for the python image, "
        f"found {ceilings}"
    )

    match = re.fullmatch(r"<(\d+)\.(\d+)", ceilings[0])
    assert match, f"unsupported ceiling form: {ceilings[0]!r}. Expected `<X.Y`"
    ceiling_major, ceiling_minor = int(match.group(1)), int(match.group(2))

    current_major, current_minor = (
        int(part) for part in _dockerfile_python_version().split(".")
    )

    # The version in use must be admitted, so patch releases still flow.
    assert (current_major, current_minor) < (ceiling_major, ceiling_minor), (
        f"the ceiling {ceilings[0]} excludes the version in use "
        f"({current_major}.{current_minor}); patch updates would be blocked"
    )
    # And the next minor must be excluded, or the ceiling holds nothing back.
    assert (current_major, current_minor + 1) >= (ceiling_major, ceiling_minor), (
        f"the ceiling {ceilings[0]} is looser than the version in use "
        f"({current_major}.{current_minor}) requires; it would admit "
        f"{current_major}.{current_minor + 1}"
    )


@pytest.mark.parametrize(
    "path", [DOCKERFILE, RENOVATE_CONFIG, CI_WORKFLOW], ids=lambda p: p.name
)
def test_the_files_this_test_reads_exist(path: Path) -> None:
    """A moved file would make the assertions above vacuous rather than failing."""
    assert path.is_file(), f"{path.relative_to(REPO_ROOT)} is missing"
