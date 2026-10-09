"""Every literal SecurityGroup ``GroupDescription`` must be one EC2 accepts.

Why this exists
---------------
``shared/templates/syslog-vpce-cloudwatch.yaml`` wrote its description as a
YAML folded scalar (``GroupDescription: >``). A folded scalar keeps the final
newline, and a newline is not in the character set EC2 accepts for a security
group description. The stack entered ``ROLLBACK_COMPLETE`` with "Invalid
security group description" on 2026-10-09, while ``make cfn-lint`` and
``make cfn-guard`` had both passed the template. The live run deployed only
after a local copy changed the indicator to ``>-``.

The constraint is documented on the EC2 API: up to 255 characters, and only
``a-z, A-Z, 0-9, spaces, and ._-:/()#,@[]+=&;{}!$*`` (CreateSecurityGroup).
This test applies it to every literal ``GroupDescription`` in CFN_TEMPLATES,
the same list the cfn-lint and cfn-guard gates read. Values built with an
intrinsic function (``!Sub`` and friends) are not checked, because their final
text is only known at deploy time.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]

# EC2 CreateSecurityGroup, "Description" constraints.
# fullmatch, not ^...$: `$` also matches just before a trailing newline, which
# is exactly the defect this test exists to catch.
VALID_DESCRIPTION = re.compile(r"[a-zA-Z0-9 ._\-:/()#,@\[\]+=&;{}!$*]{1,255}")


class _CfnLoader(yaml.SafeLoader):
    """SafeLoader that accepts CloudFormation short-form intrinsic tags."""


def _intrinsic(loader: yaml.SafeLoader, tag_suffix: str, node: yaml.Node) -> dict:
    # The value is irrelevant here: any intrinsic makes the description
    # non-literal, and the check skips non-strings.
    return {"Fn::" + tag_suffix: None}


_CfnLoader.add_multi_constructor("!", _intrinsic)


def _cfn_templates() -> list[str]:
    env = {k: v for k, v in os.environ.items() if k not in ("MAKELEVEL", "MAKEFLAGS", "MFLAGS")}
    result = subprocess.run(
        ["make", "--no-print-directory", "print-CFN_TEMPLATES"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert result.returncode == 0, result.stderr.strip()
    return result.stdout.split()


def _literal_descriptions(text: str) -> list[tuple[str, str]]:
    """(logical ID, description) for each SecurityGroup with a string description."""
    template = yaml.load(text, Loader=_CfnLoader)  # noqa: S506 - SafeLoader subclass
    found = []
    for logical_id, resource in (template.get("Resources") or {}).items():
        if not isinstance(resource, dict) or resource.get("Type") != "AWS::EC2::SecurityGroup":
            continue
        description = (resource.get("Properties") or {}).get("GroupDescription")
        if isinstance(description, str):
            found.append((logical_id, description))
    return found


def _invalid(text: str) -> list[str]:
    return [
        f"{logical_id}: {description!r}"
        for logical_id, description in _literal_descriptions(text)
        if not VALID_DESCRIPTION.fullmatch(description)
    ]


@pytest.fixture(scope="module")
def templates() -> list[str]:
    paths = _cfn_templates()
    assert len(paths) >= 20, f"CFN_TEMPLATES resolved to {paths!r}"
    return paths


def test_templates_contain_security_groups_to_check(templates: list[str]) -> None:
    """Guards the guard: a loader that silently finds nothing would pass vacuously."""
    total = sum(
        len(_literal_descriptions((REPO_ROOT / path).read_text(encoding="utf-8")))
        for path in templates
    )
    assert total >= 5, f"only {total} literal GroupDescription values found"


def test_every_literal_group_description_is_accepted_by_ec2(templates: list[str]) -> None:
    failures = {}
    for path in templates:
        bad = _invalid((REPO_ROOT / path).read_text(encoding="utf-8"))
        if bad:
            failures[path] = bad
    assert not failures, (
        "these SecurityGroup descriptions contain characters EC2 rejects "
        "(a folded `>` or literal `|` scalar keeps a trailing newline; use `>-`): "
        f"{failures}"
    )


# --------------------------------------------------------------------------
# Negative and positive controls
# --------------------------------------------------------------------------

_TEMPLATE = """\
Resources:
  Sg:
    Type: AWS::EC2::SecurityGroup
    Properties:
      GroupDescription: {indicator}
        Allow syslog from FSx for ONTAP nodes (VPC CIDR).
      VpcId: !Ref VpcId
"""


@pytest.mark.parametrize("indicator", [">", "|", ">+"])
def test_block_scalar_that_keeps_the_newline_is_rejected(indicator: str) -> None:
    assert _invalid(_TEMPLATE.format(indicator=indicator))


def test_strip_chomping_is_accepted() -> None:
    assert _invalid(_TEMPLATE.format(indicator=">-")) == []


def test_intrinsic_description_is_skipped() -> None:
    text = "Resources:\n  Sg:\n    Type: AWS::EC2::SecurityGroup\n    Properties:\n      GroupDescription: !Sub '${AWS::StackName}\\n'\n"
    assert _literal_descriptions(text) == []
