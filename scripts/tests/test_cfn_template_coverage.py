"""Every CloudFormation template on disk must be in the Makefile's CFN_TEMPLATES.

Why this exists
---------------
CFN_TEMPLATES is the input to the cfn-lint and cfn-guard gates (spec
requirement 10-5). Its integrations glob was ``integrations/*/template*.yaml``
-- exactly one directory deep. The pipeline-verification pattern templates live
two levels down (``integrations/pipeline-verification/pattern-2-prometheus/
template.yaml``), so the glob walked straight past them and the gates validated
a template set that silently excluded the new work.

Nothing failed. ``make cfn-lint`` and ``make cfn-guard`` both exited 0 while
never reading the pattern template, which looks identical to a template with no
findings.

The fix that shipped adds a second glob for the two-level pattern templates.
That repairs today's instance. This test repairs the mechanism: it derives the
expectation from the filesystem, so the next template added under a nesting the
globs do not reach fails here until CFN_TEMPLATES covers it.

Scope
-----
The Makefile deliberately covers three roots with per-root naming:

  - integrations/**            template*.yaml (main + EMS/FPolicy/Firehose/...)
  - shared/templates/          *.yaml (every file is a template here)
  - management-console/templates/  *.yaml

This test mirrors those roots and their naming rather than imposing one uniform
rule, so it asserts what the gate is meant to cover without flagging files the
gate was never meant to include.

Guard the guard
---------------
Every failure mode of a coverage test is a silent pass: an empty CFN_TEMPLATES,
a discovery walk that finds nothing, a renamed variable. The tests below pin a
non-empty list, pin known templates that must be discovered, and assert the
integrations two-levels-deep case specifically, so a regression to the
one-level glob is caught rather than passing vacuously.
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
}


def _makefile_list(name: str) -> list[str]:
    """Read a path list from the Makefile via ``make print-<VAR>``.

    Reading it through make rather than parsing text means the test sees the
    same value the recipes see, including variable composition across globs.
    MAKELEVEL/MAKEFLAGS are stripped so a nested make (when this runs under
    ``make test-py``) does not leak "Entering directory" lines into the value --
    the same local-vs-CI divergence test_test_dir_coverage.py documents.
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
    assert not stray, (
        f"`make print-{name}` emitted non-path output {stray}; the recipe or "
        "the make invocation is leaking diagnostics into the value"
    )
    return values


def _pruned(path: Path) -> bool:
    return any(part in PRUNE for part in path.relative_to(REPO_ROOT).parts)


def _discover_cfn_templates() -> list[str]:
    """Every CloudFormation template the gate is meant to cover, from disk.

    Mirrors the Makefile's three roots and their per-root naming so discovery
    matches the gate's intent, not a uniform rule the Makefile does not use.
    """
    found: set[str] = set()

    # integrations/** : template*.yaml at any depth (this is where the glob gap
    # was -- the pattern templates are two levels deep).
    integrations = REPO_ROOT / "integrations"
    if integrations.is_dir():
        for path in integrations.rglob("template*.yaml"):
            if path.is_file() and not _pruned(path):
                found.add(str(path.relative_to(REPO_ROOT)))

    # shared/templates/ : every *.yaml is a template here.
    shared_templates = REPO_ROOT / "shared" / "templates"
    if shared_templates.is_dir():
        for path in shared_templates.glob("*.yaml"):
            if path.is_file():
                found.add(str(path.relative_to(REPO_ROOT)))

    # management-console/templates/ : every *.yaml.
    mc_templates = REPO_ROOT / "management-console" / "templates"
    if mc_templates.is_dir():
        for path in mc_templates.glob("*.yaml"):
            if path.is_file():
                found.add(str(path.relative_to(REPO_ROOT)))

    return sorted(found)


@pytest.fixture(scope="module")
def cfn_templates() -> list[str]:
    return _makefile_list("CFN_TEMPLATES")


# --------------------------------------------------------------------------
# The real tree
# --------------------------------------------------------------------------


def test_makefile_exposes_a_nonempty_cfn_templates(cfn_templates: list[str]) -> None:
    """Guards the guard: an empty list would make the comparison vacuous."""
    assert len(cfn_templates) >= 20, (
        f"CFN_TEMPLATES resolved to {cfn_templates!r}; if the variable were "
        "renamed this test would otherwise compare against nothing and pass"
    )


def test_every_cfn_template_on_disk_is_in_cfn_templates(
    cfn_templates: list[str],
) -> None:
    on_disk = set(_discover_cfn_templates())
    listed = set(cfn_templates)
    orphaned = on_disk - listed
    assert not orphaned, (
        "these CloudFormation templates exist on disk but are not in the "
        "Makefile's CFN_TEMPLATES, so cfn-lint and cfn-guard skip them "
        f"silently: {sorted(orphaned)}"
    )


def test_cfn_templates_has_no_stale_entries(cfn_templates: list[str]) -> None:
    missing = [t for t in cfn_templates if not (REPO_ROOT / t).is_file()]
    assert not missing, (
        f"CFN_TEMPLATES lists files that do not exist: {missing}. cfn-lint "
        "would error on the path."
    )


def test_pipeline_verification_pattern_templates_are_covered(
    cfn_templates: list[str],
) -> None:
    """The specific regression: templates two levels deep under integrations/.

    A revert to the one-level ``integrations/*/template*.yaml`` glob would drop
    every pipeline-verification pattern template, and this asserts they stay in.
    """
    listed = set(cfn_templates)
    pattern_templates = [
        str(p.relative_to(REPO_ROOT))
        for p in (REPO_ROOT / "integrations" / "pipeline-verification").rglob(
            "template*.yaml"
        )
        if p.is_file()
    ]
    # Only assert when such templates exist (they land pattern by pattern).
    missing = [t for t in pattern_templates if t not in listed]
    assert not missing, (
        "pipeline-verification pattern templates exist but are not in "
        f"CFN_TEMPLATES: {sorted(missing)}. The integrations glob has likely "
        "regressed to one level deep."
    )


# --------------------------------------------------------------------------
# Negative controls
# --------------------------------------------------------------------------


def test_discovery_finds_known_templates() -> None:
    """If discovery stopped finding anything, coverage would pass trivially."""
    found = set(_discover_cfn_templates())
    for expected in (
        "integrations/datadog/template.yaml",
        "shared/templates/s3-access-point.yaml",
        "management-console/templates/console.yaml",
    ):
        assert expected in found, f"discovery missed {expected}: {sorted(found)}"


def test_discovery_finds_the_two_level_pattern_template() -> None:
    """Discovery must reach two levels deep; this is the case the glob missed.

    Guarded so it does not force the pattern template to exist forever, but as
    long as it is on disk, discovery must see it -- a discovery that only walked
    one level would silently drop it and the coverage test would pass with the
    buggy glob.
    """
    pattern_template = (
        REPO_ROOT
        / "integrations"
        / "pipeline-verification"
        / "pattern-2-prometheus"
        / "template.yaml"
    )
    if pattern_template.is_file():
        rel = str(pattern_template.relative_to(REPO_ROOT))
        assert rel in _discover_cfn_templates(), (
            f"discovery did not find {rel}; it is not walking integrations/ "
            "recursively"
        )


def test_discovery_prunes_vendored_trees() -> None:
    found = _discover_cfn_templates()
    assert not [t for t in found if "node_modules" in t or ".venv" in t]
