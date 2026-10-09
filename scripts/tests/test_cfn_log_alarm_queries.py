"""Pin the built-in Logs Insights queries of ``cloudwatch-log-alarm.yaml``.

Why this exists
---------------
On 2026-10-09 the Terraform equivalent (``terraform/fsxn-log-alarm/``, T3) ran
against real ONTAP 9.18.1P6 admin audit lines. Two of the template's queries
had the same defects as the module's defaults: the ``failed-access-attempts``
terms ``Failure``/``denied``/``DENIED`` matched none of the 5 real
authorization failures, and a ``specific-user-activity`` query on ``admin``
matched 5,150 of 5,156 lines. ``bulk-delete-operations`` counted both lines of
each audited change (``:: Pending`` and the result). The queries were
translated from the metric-filter patterns checked in that run
(``docs/en/verification-results-cloudwatch-monitoring.md``).

What this test does and does not show
-------------------------------------
It pins the query strings and evaluates their ``like`` / ``not like`` terms
with Python ``re.search`` on audit-line shapes written from that record. The
lines are constructed (masked placeholders), not captured, and Python ``re``
is not the Logs Insights regular-expression engine. None of these queries has
been run as a LogAlarm query; a pass here is not evidence that it was.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = REPO_ROOT / "shared" / "templates" / "cloudwatch-log-alarm.yaml"

# `@message like /re/` and `@message not like /re/` terms of a filter line.
_TERM = re.compile(r"@message (not like|like) /((?:[^/\\]|\\.)*)/")


class _CfnLoader(yaml.SafeLoader):
    """SafeLoader that keeps the scalar of a CloudFormation short-form tag."""


def _intrinsic(loader: yaml.SafeLoader, tag_suffix: str, node: yaml.Node) -> object:
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return loader.construct_mapping(node)


_CfnLoader.add_multi_constructor("!", _intrinsic)


def _query(logical_id: str) -> str:
    template = yaml.load(TEMPLATE.read_text(encoding="utf-8"), Loader=_CfnLoader)  # noqa: S506 - SafeLoader subclass
    query = template["Resources"][logical_id]["Properties"]["ScheduledQueryConfiguration"]["QueryString"]
    assert isinstance(query, str), f"{logical_id} QueryString is not a plain or !Sub string"
    return query


def _filter_line(query: str) -> str:
    lines = [line.strip() for line in query.splitlines() if line.strip().startswith("| filter ")]
    assert len(lines) == 1, f"expected one filter line, got {lines!r}"
    return lines[0]


def _matches(filter_line: str, message: str) -> bool:
    """Evaluate `A or B or ...` or `A and not like B` over one message.

    Only the two shapes the built-in queries use are supported; anything else
    fails the test instead of being evaluated loosely.
    """
    body = filter_line.removeprefix("| filter ")
    terms = _TERM.findall(body)
    assert terms, f"no like terms in {filter_line!r}"
    rebuilt_or = " or ".join(f"@message {op} /{rx}/" for op, rx in terms)
    if body == rebuilt_or and all(op == "like" for op, _ in terms):
        return any(re.search(rx, message) for _, rx in terms)
    rebuilt_and = " and ".join(f"@message {op} /{rx}/" for op, rx in terms)
    if body == rebuilt_and:
        return all((re.search(rx, message) is not None) == (op == "like") for op, rx in terms)
    raise AssertionError(f"unsupported filter shape: {filter_line!r}")


# Audit-line shapes from the 2026-10-09 record (masked; constructed here).
_HEAD = "<190>Oct  9 04:39:52 FsxIdEXAMPLE-02: FsxIdEXAMPLE-02: 00000001.00000001 00000001 Fri Oct 09 2026 04:39:52 +00:00 [kern_audit:info:1234] 8003e9000000:8003e9000001 :: FsxIdEXAMPLE:http :: <source-ip>:<port> :: "
QTREE_DELETE_PENDING = _HEAD + "FsxIdEXAMPLE:fsxadmin:fsxadmin :: DELETE /api/storage/qtrees/<volume-uuid>/1 :: Pending"
QTREE_DELETE_SUCCESS = _HEAD + "FsxIdEXAMPLE:fsxadmin:fsxadmin :: DELETE /api/storage/qtrees/<volume-uuid>/1 :: Success:"
QTREE_CREATE_PENDING = _HEAD + "FsxIdEXAMPLE:fsxadmin:fsxadmin :: POST /api/storage/qtrees : {\"name\":\"t3_qt1\"} :: Pending"
QTREE_CREATE_SUCCESS = _HEAD + "FsxIdEXAMPLE:fsxadmin:fsxadmin :: POST /api/storage/qtrees : {\"name\":\"t3_qt1\"} :: Success:"
ACCOUNT_CREATE_REJECTED = _HEAD + "FsxIdEXAMPLE:fsxadmin:fsxadmin :: POST /api/security/accounts : {\"name\":\"<user>\",\"password\":\"***\"} :: Error: not authorized for that command"
READONLY_PENDING = _HEAD + "FsxIdEXAMPLE:t3-alarm-ro:fsxadmin-readonly :: POST /api/storage/qtrees :: Pending"
READONLY_REJECTED = _HEAD + "FsxIdEXAMPLE:t3-alarm-ro:fsxadmin-readonly :: POST /api/storage/qtrees :: Error: not authorized for that command"
CONTROL_PLANE = _HEAD.replace(":http ::", ":ssh ::") + "FsxIdEXAMPLE:fsx-control-plane:admin :: set -privilege diagnostic :: Success"
CONTROL_PLANE_SID = _HEAD.replace(":http ::", ":ssh ::") + "FsxIdEXAMPLE:fsx-control-plane:admin :: vserver name-mapping show :: Error: Failed to convert Windows name to SID"

ALL_LINES = [
    QTREE_DELETE_PENDING,
    QTREE_DELETE_SUCCESS,
    QTREE_CREATE_PENDING,
    QTREE_CREATE_SUCCESS,
    ACCOUNT_CREATE_REJECTED,
    READONLY_PENDING,
    READONLY_REJECTED,
    CONTROL_PLANE,
    CONTROL_PLANE_SID,
]


def _matching(filter_line: str) -> list[str]:
    return [line for line in ALL_LINES if _matches(filter_line, line)]


# --------------------------------------------------------------------------
# Pinned query strings
# --------------------------------------------------------------------------


def test_failed_access_query_is_pinned() -> None:
    assert _filter_line(_query("FailedAccessAlarm")) == "| filter @message like /Error: not authorized/"


def test_bulk_delete_query_is_pinned() -> None:
    expected = " or ".join(
        f"@message like /{word}.*::\\s{result}/"
        for word in ("DELETE", "delete", "remove")
        for result in ("Success", "Error")
    )
    assert _filter_line(_query("BulkDeleteAlarm")) == f"| filter {expected}"


def test_specific_user_query_excludes_pending() -> None:
    assert (
        _filter_line(_query("SpecificUserAlarm"))
        == "| filter @message like /${TargetPattern}/ and @message not like /Pending/"
    )


@pytest.mark.parametrize("logical_id", ["FailedAccessAlarm", "BulkDeleteAlarm", "SpecificUserAlarm"])
def test_earlier_terms_are_gone(logical_id: str) -> None:
    query = _query(logical_id)
    for stale in ("/Failure/", "/denied/", "/DENIED/", "like /DELETE/ ", "like /delete/ ", "like /remove/"):
        assert stale not in query, f"{logical_id} still carries {stale!r}"


# --------------------------------------------------------------------------
# Semantics on the recorded line shapes
# --------------------------------------------------------------------------


def test_failed_access_matches_each_rejection_once() -> None:
    assert _matching(_filter_line(_query("FailedAccessAlarm"))) == [
        ACCOUNT_CREATE_REJECTED,
        READONLY_REJECTED,
    ]


def test_bulk_delete_counts_the_result_line_only() -> None:
    assert _matching(_filter_line(_query("BulkDeleteAlarm"))) == [QTREE_DELETE_SUCCESS]


def test_specific_user_counts_completed_operations_of_the_token() -> None:
    filter_line = _filter_line(_query("SpecificUserAlarm")).replace("${TargetPattern}", "fsxadmin:fsxadmin")
    assert _matching(filter_line) == [
        QTREE_DELETE_SUCCESS,
        QTREE_CREATE_SUCCESS,
        ACCOUNT_CREATE_REJECTED,
    ]


def test_bare_admin_target_still_matches_control_plane() -> None:
    """Documents why the TargetPattern guidance names the user:role token."""
    filter_line = _filter_line(_query("SpecificUserAlarm")).replace("${TargetPattern}", "admin")
    assert CONTROL_PLANE in _matching(filter_line)


# --------------------------------------------------------------------------
# Controls for the evaluator itself
# --------------------------------------------------------------------------


def test_earlier_failed_access_terms_match_no_rejection() -> None:
    """The defect the change fixes, reproduced by the same evaluator."""
    old = "| filter @message like /Failure/ or @message like /denied/ or @message like /DENIED/"
    assert _matching(old) == []


def test_earlier_bulk_delete_terms_count_both_lines() -> None:
    old = "| filter @message like /DELETE/ or @message like /delete/ or @message like /remove/"
    assert _matching(old) == [QTREE_DELETE_PENDING, QTREE_DELETE_SUCCESS]


def test_unsupported_filter_shape_fails() -> None:
    with pytest.raises(AssertionError):
        _matches("| filter @message like /a/ or @message not like /b/", "a")
