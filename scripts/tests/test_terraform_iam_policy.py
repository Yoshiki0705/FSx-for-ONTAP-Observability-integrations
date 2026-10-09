"""Each published deployer IAM policy and its README IAM tables must list the same actions.

Why this exists
---------------
Every Terraform module ships ``examples/basic/iam-policy.json`` for the role
that runs ``terraform apply``, and its README (EN and JA) lists the same actions
in a table under a "Required IAM permissions" heading. A reader copies one or
the other. If an action is added to a module and only one of the three places
is updated, the policy either fails at apply or the table claims a permission
set the policy does not grant. No other gate reads the JSON or the table.

The dashboard module's policy was verified with a role limited to it; the
custom-metrics module's policy is an estimate that has not been run with such a
role. The README heading says which (``(verified)`` or
``(estimated, unverified)``), and MODULES below pins that heading per module so
an estimate cannot be relabelled as verified without this test changing.

For each module this test checks that the policy parses, has the expected
statement IDs in order, keeps the placeholder account and the module's default
``name_prefix`` in every scoped resource ARN, allows ``Resource: "*"`` only on
the statements listed for it, and that the action set of each README table
equals the action set of the policy.

Resource scoping is checked per action as well as per statement. An action the
AWS service authorization reference lists without a resource type (for example
``logs:DescribeLogGroups``) is not granted by a statement scoped to an ARN, so
it may not appear in one. It belongs in a statement registered under
``wildcard_actions`` that holds exactly those actions on ``Resource: "*"``.

Guard the guard
---------------
A renamed heading would make the section empty and the comparison vacuous, so a
missing section fails instead of passing. A policy file for a module not in
MODULES fails too, so a new module cannot ship a policy that nothing compares.
Negative controls feed the extractor a section whose table lacks one action and
require exactly that difference, check that actions after the next heading or
inside a fence are not collected, and plant an unregistered policy in a
temporary tree.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

MODULES: dict[str, dict[str, Any]] = {
    "terraform/fsxn-monitoring-dashboard": {
        "sections": {
            "README.md": "### Required IAM permissions (verified)",
            "README.ja.md": "### 必要な IAM 権限（確認済み）",
        },
        "sids": ["Dashboard", "Alarms", "Topic"],
        "prefix": "fsxn-monitoring",
        "wildcard_sids": set(),
        "wildcard_actions": {},
        "control_action": "sns:Unsubscribe",
    },
    "terraform/fsxn-ontap-custom-metrics": {
        "sections": {
            "README.md": "### Required IAM permissions (estimated, unverified)",
            "README.ja.md": "### 必要な IAM 権限（推定、未検証）",
        },
        "sids": [
            "Lambda", "Role", "Queue", "Logs", "LogsRead", "Schedule", "Alarms",
            "Topic", "Network", "NetworkRead",
        ],
        "prefix": "fsxn-ontap-metrics",
        # EC2 create actions take several resource types (security group, VPC,
        # subnet, endpoint); scoping them is left open and stated in the README.
        "wildcard_sids": {"Network", "NetworkRead"},
        # Statements that must hold exactly these actions on Resource "*",
        # because the actions have no resource type (NO_RESOURCE_TYPE_ACTIONS).
        "wildcard_actions": {"LogsRead": {"logs:DescribeLogGroups"}},
        "control_action": "ec2:DescribeSubnets",
    },
    "terraform/fsxn-log-alarm": {
        "sections": {
            "README.md": "### Required IAM permissions (estimated, unverified)",
            "README.ja.md": "### 必要な IAM 権限（推定、未検証）",
        },
        "sids": ["Filters", "Alarms", "Topic"],
        "prefix": "fsxn-log-alarm",
        "wildcard_sids": set(),
        "wildcard_actions": {},
        # The metric-filter actions take the log-group resource type (the AWS
        # service-authorization reference lists logs:PutMetricFilter,
        # logs:DeleteMetricFilter and logs:DescribeMetricFilters all with the
        # log-group resource type), but the log group is a caller-supplied input,
        # not a name the module builds from name_prefix, so the statement is
        # scoped to the account's log groups rather than to the prefix. Checked
        # for the account, not the prefix suffix.
        "log_group_scoped_sids": {"Filters"},
        "control_action": "cloudwatch:DeleteAlarms",
    },
}

# Actions the AWS service authorization reference lists with no resource type,
# so a statement scoping them to an ARN does not grant them. Only these may
# appear in a "wildcard_actions" statement, and none may appear in a statement
# scoped to a resource ARN.
# logs:DescribeLogGroups:
#   https://docs.aws.amazon.com/service-authorization/latest/reference/list_logs.html
#   The reference lists it as a List action with no resource type (confidence:
#   documented, verified 2026-10-09 from the service-authorization reference,
#   which was fetchable as text at the list_logs.html URL).
# logs:DescribeMetricFilters is NOT here: the same reference lists it with the
# log-group resource type (alongside logs:DescribeLogStreams and
# logs:DescribeSubscriptionFilters), so the log-alarm module scopes it to the
# log group rather than to Resource "*".
NO_RESOURCE_TYPE_ACTIONS = {"logs:DescribeLogGroups"}

ACTION = re.compile(r"`([a-z0-9]+:[A-Za-z]+)`")
FENCE = re.compile(r"^\s*(```|~~~)")
HEADING = re.compile(r"^#{1,6}\s")

README_CASES = [(m, r) for m in sorted(MODULES) for r in sorted(MODULES[m]["sections"])]


def _policy_path(module: str) -> Path:
    return REPO_ROOT / module / "examples/basic/iam-policy.json"


def _load_policy(module: str) -> dict[str, Any]:
    return json.loads(_policy_path(module).read_text(encoding="utf-8"))


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


def _discover_policies(root: Path) -> list[str]:
    return sorted(
        str(p.relative_to(root).parents[2])
        for p in (root / "terraform").glob("*/examples/*/iam-policy.json")
    )


def _statement_problems(spec: dict[str, Any], statement: dict[str, Any]) -> list[str]:
    """Reasons a statement breaks the module's scoping rules; empty when it is fine."""
    sid = statement["Sid"]
    problems: list[str] = []
    if statement["Effect"] != "Allow":
        problems.append(f"{sid}: Effect is not Allow")
    resource = statement["Resource"]
    if not isinstance(resource, str):
        return [*problems, f"{sid}: Resource is not a single string"]
    actions = _policy_actions({"Statement": [statement]})
    if sid in spec["wildcard_actions"]:
        if resource != "*":
            problems.append(f"{sid}: must use Resource '*'")
        if actions != spec["wildcard_actions"][sid]:
            problems.append(
                f"{sid}: actions {sorted(actions)} differ from the registered "
                f"exception {sorted(spec['wildcard_actions'][sid])}"
            )
        return problems
    if sid in spec["wildcard_sids"]:
        if resource != "*":
            problems.append(f"{sid}: must use Resource '*'")
        return problems
    if sid in spec.get("log_group_scoped_sids", set()):
        # Scoped to the account's log groups, because the log group is a
        # caller-supplied input rather than a name built from name_prefix.
        if "123456789012" not in resource:
            problems.append(f"{sid}: {resource} lacks the placeholder account")
        if ":log-group:" not in resource:
            problems.append(f"{sid}: {resource} is not a log-group ARN")
        unscopable = sorted(actions & NO_RESOURCE_TYPE_ACTIONS)
        if unscopable:
            problems.append(f"{sid}: {unscopable} have no resource type and cannot be scoped to {resource}")
        return problems
    unscopable = sorted(actions & NO_RESOURCE_TYPE_ACTIONS)
    if unscopable:
        problems.append(f"{sid}: {unscopable} have no resource type and cannot be scoped to {resource}")
    prefix = spec["prefix"]
    if "123456789012" not in resource:
        problems.append(f"{sid}: {resource} lacks the placeholder account")
    if not resource.endswith((f":{prefix}-*", f"/{prefix}-*")):
        problems.append(f"{sid}: {resource} is not scoped to the {prefix}-* names")
    return problems


@pytest.mark.parametrize("module", sorted(MODULES))
def test_policy_parses_and_is_placeholder_scoped(module: str) -> None:
    spec = MODULES[module]
    policy = _load_policy(module)
    assert policy["Version"] == "2012-10-17"
    statements = policy["Statement"]
    assert [s["Sid"] for s in statements] == spec["sids"]
    problems = [p for s in statements for p in _statement_problems(spec, s)]
    assert not problems, problems


@pytest.mark.parametrize("module", sorted(MODULES))
def test_wildcard_action_exceptions_are_justified(module: str) -> None:
    """Each action-level exception names only actions without a resource type."""
    for sid, actions in MODULES[module]["wildcard_actions"].items():
        assert actions, sid
        assert actions <= NO_RESOURCE_TYPE_ACTIONS, (
            f"{module} {sid}: {sorted(actions - NO_RESOURCE_TYPE_ACTIONS)} are not "
            "listed in NO_RESOURCE_TYPE_ACTIONS with an AWS reference"
        )


def test_scoping_check_rejects_unscopable_and_widened_statements() -> None:
    """Negative controls for _statement_problems."""
    spec = MODULES["terraform/fsxn-ontap-custom-metrics"]
    scoped_arn = "arn:aws:logs:ap-northeast-1:123456789012:log-group:/aws/lambda/fsxn-ontap-metrics-*"
    # The shape this test was written against: DescribeLogGroups under a log-group ARN.
    scoped = {
        "Sid": "Logs", "Effect": "Allow", "Resource": scoped_arn,
        "Action": ["logs:CreateLogGroup", "logs:DescribeLogGroups"],
    }
    assert any("no resource type" in p for p in _statement_problems(spec, scoped))
    # The exception statement may not carry anything beyond its registered actions.
    widened = {
        "Sid": "LogsRead", "Effect": "Allow", "Resource": "*",
        "Action": ["logs:DescribeLogGroups", "logs:DeleteLogGroup"],
    }
    assert any("differ from the registered" in p for p in _statement_problems(spec, widened))
    # Nor may it be scoped.
    narrowed = {**widened, "Action": "logs:DescribeLogGroups", "Resource": scoped_arn}
    assert any("must use Resource '*'" in p for p in _statement_problems(spec, narrowed))
    # Positive control: the published shape passes.
    exact = {**narrowed, "Resource": "*"}
    assert _statement_problems(spec, exact) == []


def test_log_group_scoped_branch_controls() -> None:
    """Positive and negative controls for the log_group_scoped_sids branch.

    The log-alarm module scopes its metric-filter actions to the account's log
    groups rather than to a name_prefix, because the log group is a
    caller-supplied input. This exercises that branch directly: the published
    Filters shape passes, and a missing placeholder account, a non-log-group
    ARN, and a no-resource-type action each fail.
    """
    spec = MODULES["terraform/fsxn-log-alarm"]
    assert spec["log_group_scoped_sids"] == {"Filters"}
    log_group_arn = "arn:aws:logs:ap-northeast-1:123456789012:log-group:*"
    # Positive control: the published Filters statement passes.
    published = {
        "Sid": "Filters", "Effect": "Allow", "Resource": log_group_arn,
        "Action": [
            "logs:PutMetricFilter", "logs:DeleteMetricFilter",
            "logs:DescribeMetricFilters",
        ],
    }
    assert _statement_problems(spec, published) == []
    # Negative control: a different account than the 123456789012 placeholder
    # (000000000000 is the other allowlisted placeholder, so this is not a leak).
    wrong_account = {
        **published,
        "Resource": "arn:aws:logs:ap-northeast-1:000000000000:log-group:*",
    }
    assert any("lacks the placeholder account" in p for p in _statement_problems(spec, wrong_account))
    # Negative control: a non-log-group ARN (right account, wrong resource type).
    wrong_resource = {
        **published,
        "Resource": "arn:aws:cloudwatch:ap-northeast-1:123456789012:alarm:fsxn-log-alarm-*",
    }
    assert any("is not a log-group ARN" in p for p in _statement_problems(spec, wrong_resource))
    # Negative control: a no-resource-type action cannot sit in a scoped statement.
    unscopable = {**published, "Action": ["logs:DescribeLogGroups"]}
    assert any("no resource type" in p for p in _statement_problems(spec, unscopable))


def test_dashboard_statement_services() -> None:
    statements = _load_policy("terraform/fsxn-monitoring-dashboard")["Statement"]
    by_sid = {s["Sid"]: _policy_actions({"Statement": [s]}) for s in statements}
    assert all(a.startswith("cloudwatch:") for a in by_sid["Dashboard"] | by_sid["Alarms"])
    assert all(a.startswith("sns:") for a in by_sid["Topic"])


def test_custom_metrics_statement_services() -> None:
    statements = _load_policy("terraform/fsxn-ontap-custom-metrics")["Statement"]
    by_sid = {s["Sid"]: _policy_actions({"Statement": [s]}) for s in statements}
    expected = {
        "Lambda": "lambda:", "Role": "iam:", "Queue": "sqs:", "Logs": "logs:",
        "LogsRead": "logs:", "Schedule": "events:", "Alarms": "cloudwatch:", "Topic": "sns:",
        "Network": "ec2:", "NetworkRead": "ec2:",
    }
    for sid, service in expected.items():
        assert all(a.startswith(service) for a in by_sid[sid]), sid
    # The wildcard statements must not grant anything beyond EC2.
    assert all(a.startswith("ec2:Describe") for a in by_sid["NetworkRead"])


@pytest.mark.parametrize(("module", "readme"), README_CASES)
def test_readme_tables_match_policy(module: str, readme: str) -> None:
    text = (REPO_ROOT / module / readme).read_text(encoding="utf-8")
    heading = MODULES[module]["sections"][readme]
    table = _section_table_actions(text, heading)
    assert table is not None, f"{module}/{readme}: heading {heading!r} not found"
    policy = _policy_actions(_load_policy(module))
    assert table == policy, (
        f"{module}/{readme}: only in README table: {sorted(table - policy)}; "
        f"only in iam-policy.json: {sorted(policy - table)}"
    )
    assert "examples/basic/iam-policy.json" in _section_text(text, heading), readme


def test_every_example_iam_policy_is_registered() -> None:
    found = _discover_policies(REPO_ROOT)
    assert found, "discovery found no iam-policy.json; the glob is broken"
    unregistered = [m for m in found if m not in MODULES]
    assert not unregistered, (
        f"iam-policy.json exists for {unregistered} but MODULES has no entry, so "
        "nothing compares it with the README"
    )
    missing = [m for m in MODULES if not _policy_path(m).is_file()]
    assert not missing, f"MODULES lists {missing} but no examples/basic/iam-policy.json exists"


def test_registration_check_finds_an_unregistered_policy(tmp_path: Path) -> None:
    """Negative control: a policy for an unknown module must be discovered."""
    planted = tmp_path / "terraform" / "new-module" / "examples" / "basic"
    planted.mkdir(parents=True)
    (planted / "iam-policy.json").write_text("{}", encoding="utf-8")
    found = _discover_policies(tmp_path)
    assert found == ["terraform/new-module"]
    assert found[0] not in MODULES


@pytest.mark.parametrize("module", sorted(MODULES))
def test_comparison_reports_a_missing_action(module: str) -> None:
    """Negative control: a table lacking one action must differ by exactly that action."""
    control = MODULES[module]["control_action"]
    heading = MODULES[module]["sections"]["README.md"]
    policy = _policy_actions(_load_policy(module))
    assert control in policy, control
    kept = sorted(policy - {control})
    row = ", ".join(f"`{a}`" for a in kept)
    text = "\n".join(
        [
            "## Usage",
            "",
            heading,
            "",
            "| Resource | Actions |",
            "|---|---|",
            f"| all | {row} |",
            "",
            "```text",
            f"| fenced | `{control}` |",
            "### Not a heading",
            "```",
            "",
            "### Next",
            "",
            f"| other | `{control}` |",
        ]
    )
    table = _section_table_actions(text, heading)
    assert table is not None
    assert policy - table == {control}
    assert not table - policy
    assert _section_table_actions(text, "### Absent") is None
