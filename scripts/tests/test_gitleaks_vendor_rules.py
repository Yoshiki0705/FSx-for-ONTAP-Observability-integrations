"""gitleaks must detect every vendor credential format this repo handles.

Why this exists
---------------
This repository ships audit logs to nine external SaaS vendors, so a vendor
credential is the highest-value secret it touches. A planted-secret run showed
that 6 of 7 formats were undetected when written without an adjacent `KEY=`
assignment. Only Grafana was caught, by a gitleaks default rule.

The coverage that appeared to exist was an artifact of how it had been tested.
gitleaks' generic-api-key rule fires on the assignment keyword rather than on
the credential, so `DATADOG_API_KEY=<hex>` was reported while the same key in a
sentence was not. The setup guides in docs/ walk a reader through obtaining
these keys, which makes prose the realistic leak path -- exactly the shape that
was invisible.

A rule silently matching nothing looks identical to a clean repository, so
these tests plant one credential per format and require a finding. They also
assert the repo scans clean, because a scanner that always reports something
trains people to ignore it.

The credentials below are synthetic, in the documented shape of each vendor's
format. None is a real key.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG = REPO_ROOT / ".gitleaks.toml"

# rule id -> line of text that must be reported.
# Written into docs/en/*.md because documentation prose is the realistic path.
MUST_DETECT: dict[str, str] = {
    "sumologic-collector-url": (
        "Send events to https://endpoint4.collection.sumologic.com/receiver/v1/"
        "http/ZaVnC4dhaV1pLIFsWbCOUP6RTMbUFxURkTGSbtnAiw3JmPBVBmcHhLdvSbhVn3Vle7Wl"
    ),
    "honeycomb-ingest-key": (
        "Set the ingest key to hcaik_01jz8mq4n7yv3xr5t9wp2ksd6bfgh8jklmnpqrstuvwx"
    ),
    "newrelic-key": "The user key is NRAK-ABCDEFGHIJKLMNOPQRSTUVWX01",
    "dynatrace-api-token": (
        "Token: dt0c01.ST2EY72KQINMH574WMNVI7YN."
        "G3DFPBEJYMODIDAEX454M7YWBUVEFOWKPRVK4Q6BAGH2VRXJ4YMKQGBTVWBTHJKL"
    ),
    # Keyword-anchored rules: assert both the assignment and the prose shape,
    # because only the assignment was ever detected before.
    "datadog-api-key-in-context": "DATADOG_API_KEY=8f3a2b1c9d4e5f6a7b8c9d0e1f2a3b4c",
    "splunk-hec-token-in-context": (
        "Use the Splunk HEC token a1b2c3d4-e5f6-4a5b-8c9d-0e1f2a3b4c5d for ingest"
    ),
}

# Prose variants of the two keyword-anchored rules. These are the shapes a
# setup guide actually contains, and the shapes the punctuation-only separator
# used to miss.
PROSE_VARIANTS: dict[str, str] = {
    "datadog-api-key-in-context": (
        "Copy your Datadog API key, which looks like "
        "8f3a2b1c9d4e5f6a7b8c9d0e1f2a3b4c, into Secrets Manager."
    ),
    "splunk-hec-token-in-context": (
        "The HEC endpoint expects token a1b2c3d4-e5f6-4a5b-8c9d-0e1f2a3b4c5d."
    ),
}

# Placeholder forms used throughout the real documentation. These must NOT be
# reported: a gate that flags the documented placeholder convention gets
# allowlisted away along with the real rule.
MUST_NOT_DETECT: dict[str, str] = {
    "sumo-placeholder": (
        "https://endpoint<N>.collection.sumologic.com/receiver/v1/http/<COLLECTOR_TOKEN>"
    ),
    "datadog-env-var": 'headers = {"DD-API-KEY": os.environ["DD_API_KEY"]}',
    "datadog-secret-arn": "DATADOG_API_KEY is read from SECRET_ARN via Secrets Manager",
    "splunk-svm-uuid": "svm-uuid parameter: 12345678-1234-1234-1234-123456789abc",
    "zeroed-key": "DATADOG_API_KEY=00000000000000000000000000000000",
}

pytestmark = pytest.mark.skipif(
    shutil.which("gitleaks") is None,
    reason="gitleaks is not installed; CI installs it via the gitleaks workflow",
)


def _scan(files: dict[str, str]) -> list[dict]:
    """Write files into a temp tree and return gitleaks findings."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for name, body in files.items():
            path = root / "docs" / "en" / f"{name}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body + "\n", encoding="utf-8")
        report = root / "report.json"
        result = subprocess.run(
            ["gitleaks", "detect", "--config", str(CONFIG), "--no-git",
             "--source", str(root), "--report-format", "json",
             "--report-path", str(report), "--exit-code", "0"],
            capture_output=True, text=True, timeout=180,
        )
        assert report.is_file(), (
            f"gitleaks produced no report; it probably failed to parse "
            f"{CONFIG.name}:\n{result.stderr}"
        )
        return json.loads(report.read_text(encoding="utf-8") or "[]")


def test_gitleaks_config_parses() -> None:
    """A config that fails to parse must not read as a clean scan."""
    findings = _scan({"probe": "nothing sensitive here"})
    assert findings == []


@pytest.mark.parametrize("rule_id", sorted(MUST_DETECT))
def test_vendor_credential_is_detected(rule_id: str) -> None:
    findings = _scan({rule_id: MUST_DETECT[rule_id]})
    rules = {f["RuleID"] for f in findings}
    assert rules, (
        f"{rule_id}: a synthetic credential in documentation prose produced no "
        "finding at all. This is the state the rules were added to fix."
    )
    assert rule_id in rules, (
        f"{rule_id}: detected, but by {sorted(rules)} rather than the dedicated "
        "rule. The dedicated rule has probably stopped matching -- verify its "
        "regex against the current vendor format."
    )


@pytest.mark.parametrize("rule_id", sorted(PROSE_VARIANTS))
def test_keyword_anchored_rules_match_prose_not_only_assignments(rule_id: str) -> None:
    findings = _scan({rule_id: PROSE_VARIANTS[rule_id]})
    rules = {f["RuleID"] for f in findings}
    assert rule_id in rules, (
        f"{rule_id}: matched an assignment but not the same credential in a "
        f"sentence. Setup guides contain the sentence form. Got {sorted(rules)}."
    )


@pytest.mark.parametrize("name", sorted(MUST_NOT_DETECT))
def test_documented_placeholders_are_not_reported(name: str) -> None:
    findings = _scan({name: MUST_NOT_DETECT[name]})
    assert not findings, (
        f"{name}: the documented placeholder convention is reported as a leak "
        f"({[f['RuleID'] for f in findings]}). False positives on placeholders "
        "are how a real rule ends up allowlisted away."
    )


def test_repo_scans_clean() -> None:
    """The working tree must be clean, so any finding is signal.

    Compiled bytecode under __pycache__ used to produce 8 findings -- copies of
    example IPs from the .py files beside them, where the source form was
    already allowlisted. A permanently non-clean scan is a gate nobody reads.
    """
    report = Path(tempfile.gettempdir()) / "gitleaks-repo-scan.json"
    subprocess.run(
        ["gitleaks", "detect", "--config", str(CONFIG), "--no-git",
         "--source", str(REPO_ROOT), "--redact", "--report-format", "json",
         "--report-path", str(report), "--exit-code", "0"],
        capture_output=True, text=True, timeout=300, cwd=REPO_ROOT,
    )
    findings = json.loads(report.read_text(encoding="utf-8") or "[]")
    summary = [
        f"{Path(f['File']).name}:{f['StartLine']} {f['RuleID']}" for f in findings
    ]
    assert not findings, f"gitleaks reports findings on the working tree: {summary}"
