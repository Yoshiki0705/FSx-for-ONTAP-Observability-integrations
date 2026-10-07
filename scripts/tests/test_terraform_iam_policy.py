"""The published IAM policy and the README IAM tables must list the same actions.

Why this exists
---------------
terraform/fsxn-monitoring-dashboard/examples/basic/iam-policy.json is the
minimum policy verified with a role limited to it, and the module README (EN
and JA) lists the same actions in a table under "Required IAM permissions
(verified)". A reader copies one or the other. If an action is added to the
module and only one of the three places is updated, the policy either fails at
apply or the table claims a permission set that was never verified. No other
gate reads the JSON or the table.

This test checks that the policy parses, keeps the placeholder account and the
default ``fsxn-monitoring`` prefix in every resource ARN, and that the action set
of each README table equals the action set of the policy.

Guard the guard
---------------
A renamed heading would make the section empty and the comparison vacuous, so a
missing section fails instead of passing. A negative control feeds the extractor
a section whose table lacks one action and requires exactly that difference, and
checks that actions after the next heading or inside a fence are not collected.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE = REPO_ROOT / "terraform/fsxn-monitoring-dashboard"
POLICY = MODULE / "examples/basic/iam-policy.json"
SECTIONS = {
    "README.md": "### Required IAM permissions (verified)",
    "README.ja.md": "### 必要な IAM 権限（確認済み）",
}

ACTION = re.compile(r"`([a-z0-9]+:[A-Za-z]+)`")
FENCE = re.compile(r"^\s*(```|~~~)")
HEADING = re.compile(r"^#{1,6}\s")


def _load_policy() -> dict[str, Any]:
    return json.loads(POLICY.read_text(encoding="utf-8"))


def _policy_actions(policy: dict[str, Any]) -> set[str]:
    """Every action of every statement, whether ``Action`` is a string or a list."""
    actions: set[str] = set()
    for statement in policy["Statement"]:
        value = statement["Action"]
        actions.update([value] if isinstance(value, str) else value)
    return actions


def _section_table_actions(text: str, heading: str) -> set[str] | None:
    """Actions in table lines between ``heading`` and the next heading.

    Returns None when the heading line is absent, so a renamed heading fails the
    caller instead of yielding an empty set.
    """
    lines = text.splitlines()
    if heading not in lines:
        return None
    actions: set[str] = set()
    in_fence = False
    for line in lines[lines.index(heading) + 1 :]:
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if HEADING.match(line):
            break
        if line.startswith("|"):
            actions.update(ACTION.findall(line))
    return actions


def _section_text(text: str, heading: str) -> str:
    lines = text.splitlines()
    start = lines.index(heading) + 1
    in_fence = False
    for offset, line in enumerate(lines[start:]):
        if FENCE.match(line):
            in_fence = not in_fence
        elif not in_fence and HEADING.match(line):
            return "\n".join(lines[start : start + offset])
    return "\n".join(lines[start:])


def test_policy_parses_and_is_placeholder_scoped() -> None:
    policy = _load_policy()
    assert policy["Version"] == "2012-10-17"
    statements = policy["Statement"]
    assert [s["Sid"] for s in statements] == ["Dashboard", "Alarms", "Topic"]
    for statement in statements:
        assert statement["Effect"] == "Allow", statement["Sid"]
        resource = statement["Resource"]
        assert isinstance(resource, str), statement["Sid"]
        assert "123456789012" in resource, resource
        assert resource.endswith((":fsxn-monitoring-*", "/fsxn-monitoring-*")), resource
    by_sid = {s["Sid"]: _policy_actions({"Statement": [s]}) for s in statements}
    assert all(a.startswith("cloudwatch:") for a in by_sid["Dashboard"] | by_sid["Alarms"])
    assert all(a.startswith("sns:") for a in by_sid["Topic"])


@pytest.mark.parametrize("readme", sorted(SECTIONS))
def test_readme_tables_match_policy(readme: str) -> None:
    text = (MODULE / readme).read_text(encoding="utf-8")
    heading = SECTIONS[readme]
    table = _section_table_actions(text, heading)
    assert table is not None, f"{readme}: heading {heading!r} not found"
    policy = _policy_actions(_load_policy())
    assert table == policy, (
        f"{readme}: only in README table: {sorted(table - policy)}; "
        f"only in iam-policy.json: {sorted(policy - table)}"
    )
    assert "examples/basic/iam-policy.json" in _section_text(text, heading), readme


def test_comparison_reports_a_missing_action() -> None:
    """Negative control: a table lacking one action must differ by exactly that action."""
    policy = _policy_actions(_load_policy())
    kept = sorted(policy - {"sns:Unsubscribe"})
    row = ", ".join(f"`{a}`" for a in kept)
    text = "\n".join(
        [
            "## Usage",
            "",
            SECTIONS["README.md"],
            "",
            "| Resource | Actions |",
            "|---|---|",
            f"| all | {row} |",
            "",
            "```text",
            "| fenced | `sns:Unsubscribe` |",
            "### Not a heading",
            "```",
            "",
            "### Next",
            "",
            "| other | `sns:Unsubscribe` |",
        ]
    )
    table = _section_table_actions(text, SECTIONS["README.md"])
    assert table is not None
    assert policy - table == {"sns:Unsubscribe"}
    assert not table - policy
    assert _section_table_actions(text, "### Absent") is None
