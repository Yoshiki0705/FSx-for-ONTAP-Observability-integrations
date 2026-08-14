"""The bandit baseline must not grow into a place to hide findings.

Why this exists
---------------
bandit had never run against this repository. Its first execution reported 6
Medium findings, all B314: stdlib xml.etree parsing of FSx for ONTAP audit
logs, whose filenames and usernames originate from whoever touches the volume.

Those 6 are recorded in .bandit-baseline.json so the gate can be blocking
without starting red. But a baseline is a mechanism for manufactured silence:
appending to it is indistinguishable from fixing the finding, and the diff is
a machine-generated JSON blob that nobody reads.

This test pins the shape of the baseline. Adding a suppression now requires
editing an assertion, which puts the decision in the pull request.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections import Counter
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE = REPO_ROOT / ".bandit-baseline.json"

# The findings accepted at adoption. B314 = xml.etree parsing untrusted XML.
# ElementTree does not resolve external entities, so this is an
# entity-expansion denial-of-service exposure rather than XXE exfiltration.
EXPECTED_RULES = {"B314"}
EXPECTED_COUNT = 6


@pytest.fixture(scope="module")
def results() -> list[dict]:
    assert BASELINE.is_file(), (
        f"{BASELINE.name} is missing. `make security` passes it with -b, and "
        "bandit treats a missing baseline as a hard error, so the gate would "
        "fail loudly rather than silently -- but regenerate it deliberately, "
        "do not let it be recreated by accident."
    )
    data = json.loads(BASELINE.read_text(encoding="utf-8"))
    return data["results"]


def test_baseline_holds_only_the_reviewed_rule_ids(results: list[dict]) -> None:
    actual = {r["test_id"] for r in results}
    unexpected = actual - EXPECTED_RULES
    assert not unexpected, (
        f"the bandit baseline now suppresses rule ids that were never "
        f"reviewed: {sorted(unexpected)}. Fix the finding, or add the rule id "
        "to EXPECTED_RULES here with a note explaining the decision."
    )


def test_baseline_has_not_grown(results: list[dict]) -> None:
    assert len(results) <= EXPECTED_COUNT, (
        f"the baseline has grown from {EXPECTED_COUNT} to {len(results)} "
        f"entries: {Counter(r['test_id'] for r in results)}. New findings must "
        "be fixed rather than appended to the baseline."
    )


def test_baseline_records_no_high_severity(results: list[dict]) -> None:
    high = [
        f"{r['filename']}:{r['line_number']} {r['test_id']}"
        for r in results
        if r["issue_severity"].upper() == "HIGH"
    ]
    assert not high, f"HIGH severity findings must never be baselined: {high}"


def test_baselined_locations_still_exist(results: list[dict]) -> None:
    """A stale entry means the code moved and the suppression now covers a
    different line, or nothing at all."""
    missing = sorted(
        {r["filename"] for r in results if not (REPO_ROOT / r["filename"]).is_file()}
    )
    assert not missing, (
        f"the baseline references files that no longer exist: {missing}. "
        "Regenerate it so it stops suppressing findings by accident."
    )


def test_baseline_does_not_suppress_a_new_finding(tmp_path: Path) -> None:
    """The claim a baseline makes is "these 6 and nothing else". Verify the
    second half.

    A baseline that matched too broadly would report clean on genuinely new
    findings, which is the failure mode that makes baselines dangerous: the
    gate keeps passing and the suppression is invisible in the diff.
    """
    # Prefer the venv copy (the pinned one the Makefile uses) but fall back to
    # PATH: CI installs from requirements-dev.txt into the runner's interpreter
    # and has no .venv, so a venv-only lookup would silently skip there --
    # turning the one control that proves the baseline is honest into a no-op
    # exactly where it matters most.
    venv_bandit = REPO_ROOT / ".venv/bin/bandit"
    bandit = venv_bandit if venv_bandit.is_file() else shutil.which("bandit")
    if not bandit:
        pytest.skip("bandit not installed; run `make install`")

    planted = tmp_path / "planted.py"
    planted.write_text(
        "import subprocess\n"
        "def run(cmd: str) -> None:\n"
        "    subprocess.call(cmd, shell=True)\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [str(bandit), "-q", "-r", str(tmp_path), "-ll", "-b", str(BASELINE)],
        capture_output=True, text=True, cwd=REPO_ROOT, timeout=180,
    )
    assert result.returncode != 0, (
        "bandit reported clean on a planted shell=True call while using the "
        "baseline. The baseline is suppressing more than the 6 reviewed "
        f"findings.\nstdout:\n{result.stdout}"
    )
    assert "B602" in result.stdout or "shell=True" in result.stdout


def test_makefile_passes_the_baseline_to_bandit() -> None:
    """Guards the wiring: without -b the gate reports the 6 and fails, which
    is noisy but safe. The dangerous direction is the baseline existing while
    nothing consumes it, so the 6 look resolved."""
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    assert "BANDIT_BASELINE" in makefile
    assert "-b $(BANDIT_BASELINE)" in makefile, (
        "the security target does not pass the baseline to bandit"
    )
