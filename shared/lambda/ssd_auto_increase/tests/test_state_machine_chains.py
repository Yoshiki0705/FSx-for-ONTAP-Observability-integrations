"""Lock-state chains from the design test plan, driven through one shared store.

Each test runs several invocations against one lock store and asserts the
properties the design's "Lock-state transitions" row names, not only the final
decision: correlation IDs and tokens (old versus new), the archive event
sequence under one correlation ID, cumulative UpdateFileSystem calls,
intermediate lock states (FakeTable journal), reports and the final release.

The clock is frozen per invocation (the handler's ``utcnow``), and every later
time is derived from the ``request_time`` the handler stored, so no assertion
depends on when the test runs.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from botocore.exceptions import ReadTimeoutError
from ssd_test_support import (
    FILE_SYSTEM_ID,
    FakeCloudWatch,
    FakeFsx,
    FakeLogs,
    FakeS3,
    FakeSns,
    admin_action,
    alarm_response,
    archived,
    file_system,
    log_lines,
    make_client_error,
    reports,
)

FS = FILE_SYSTEM_ID
T0 = datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc)


class Chain:
    """Runs invocations against one store and accumulates what each one did."""

    def __init__(self, load_handler: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        self._load = load_handler
        self._monkeypatch = monkeypatch
        self.store: dict[str, dict[str, Any]] = {}
        self.journal: list[tuple[str, str | None, str | None]] = []
        self.update_calls: list[dict[str, Any]] = []
        self.s3: list[FakeS3] = []
        self.sns: list[FakeSns] = []
        self.logs: list[FakeLogs] = []

    def tick(
        self,
        at: datetime,
        fsx: FakeFsx,
        *,
        alarms: dict[str, Any] | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Run one invocation at ``at``; return (result, what this tick did)."""
        s3, sns, logs = FakeS3(), FakeSns(), FakeLogs()
        loaded = self._load(
            fsx=fsx, s3=s3, sns=sns, logs=logs, lock_store=self.store, journal=self.journal,
            cloudwatch=FakeCloudWatch(alarms or alarm_response("ALARM")), env=env,
        )
        self._monkeypatch.setattr(loaded.handler, "utcnow", lambda: at)
        before = len(self.journal)
        result = loaded.handler.lambda_handler({}, None)
        self.update_calls += fsx.update_calls
        self.s3.append(s3)
        self.sns.append(sns)
        self.logs.append(logs)
        did = {
            "update_calls": list(fsx.update_calls),
            "archived": archived(s3),
            "reports": reports(sns),
            "journal": self.journal[before:],
        }
        return result, did

    def all_archived(self) -> list[tuple[str, int, str, dict[str, Any]]]:
        return archived(*self.s3)

    def all_reports(self) -> list[tuple[str, dict[str, Any]]]:
        return reports(*self.sns)


def _epoch(dt: datetime) -> int:
    return int(dt.timestamp())


def _at(request_time: int, hours: float) -> datetime:
    return datetime.fromtimestamp(request_time, tz=timezone.utc) + timedelta(hours=hours)


# -- (a) take-over of an expired evaluating item ---------------------------

OLD_OWNER = "crashed-owner"


def _expired_evaluating(store: dict[str, dict[str, Any]]) -> None:
    # The owner crashed before storing request facts: no target, no token, and
    # a lease that ended an hour before the take-over tick.
    store[FS] = {
        "file_system_id": FS,
        "state": "evaluating",
        "owner": OLD_OWNER,
        "acquired_at": _epoch(T0) - 4000,
        "expires_at": _epoch(T0) - 3600,
    }


def test_takeover_issues_a_new_token_and_names_the_superseded_id(
    load_handler, monkeypatch
) -> None:
    chain = Chain(load_handler, monkeypatch)
    _expired_evaluating(chain.store)

    result, did = chain.tick(T0, FakeFsx([file_system(capacity=1024)]))

    assert result["decision"] == "submitted"
    # The take-over put a NEW evaluating item under a new correlation ID.
    op, state, new_owner = did["journal"][0]
    assert (op, state) == ("put", "evaluating")
    assert new_owner != OLD_OWNER
    # The request carries the new token, never the superseded one.
    assert len(did["update_calls"]) == 1
    token = did["update_calls"][0]["ClientRequestToken"]
    assert token == new_owner
    assert token != OLD_OWNER
    item = chain.store[FS]
    assert item["owner"] == new_owner
    assert item["client_request_token"] == new_owner
    assert item["state"] == "submitted"
    # The archive sequence is the new evaluation's; the decision (intent)
    # event names the superseded correlation ID.
    objects = did["archived"]
    assert {cid for cid, _, _, _ in objects} == {new_owner}
    assert [(seq, event) for _, seq, event, _ in objects] == [(1, "decision"), (2, "accepted")]
    intent = objects[0][3]
    assert intent["superseded"] == OLD_OWNER
    assert intent["correlation_id"] == new_owner
    assert intent["decision"] == "increase"
    # The decision log line and the pre-call report carry the same.
    decision_line = next(line for line in log_lines(*chain.logs) if line["event"] == "decision")
    assert decision_line["superseded"] == OLD_OWNER
    assert decision_line["correlation_id"] == new_owner
    pre_call = dict(did["reports"])[f"T4 will increase {FS}"]
    assert pre_call["superseded"] == OLD_OWNER
    assert pre_call["correlation_id"] == new_owner


def _guard_case(case: str) -> tuple[FakeFsx, dict[str, Any], dict[str, str], str, str | None]:
    """(fsx, alarms, env, expected decision, expected lock state or None if released)."""
    alarms = alarm_response("ALARM")
    env: dict[str, str] = {}
    fs = file_system(capacity=1024)
    if case == "alarm_not_in_alarm":
        alarms = alarm_response("OK")
        return FakeFsx([fs]), alarms, env, case, None
    if case == "ceiling_exceeds_service_maximum":
        env = {"MAX_STORAGE_CAPACITY_GIB": "300000"}
        return FakeFsx([fs]), alarms, env, case, "blocked"
    if case == "administrative_action_in_progress":
        actions = [admin_action(status="IN_PROGRESS", request_time=T0 - timedelta(days=2))]
        return FakeFsx([file_system(capacity=1024, administrative_actions=actions)]), alarms, env, case, None
    if case == "cooldown_active":
        actions = [admin_action(status="COMPLETED", request_time=T0 - timedelta(hours=1))]
        return FakeFsx([file_system(capacity=1024, administrative_actions=actions)]), alarms, env, case, None
    if case == "ceiling_reached":
        return FakeFsx([file_system(capacity=4000)]), alarms, env, case, None
    if case == "iops_exceeds_maximum":
        fs = file_system(capacity=1024, iops_mode="USER_PROVISIONED", iops=90000)
        return FakeFsx([fs]), alarms, env, case, "blocked"
    raise AssertionError(case)


@pytest.mark.parametrize(
    "case",
    [
        "alarm_not_in_alarm",
        "ceiling_exceeds_service_maximum",
        "administrative_action_in_progress",
        "cooldown_active",
        "ceiling_reached",
        "iops_exceeds_maximum",
    ],
)
def test_takeover_reruns_every_guard(load_handler, monkeypatch, case: str) -> None:
    """After a take-over each guard still blocks: no inherited "already checked"."""
    chain = Chain(load_handler, monkeypatch)
    _expired_evaluating(chain.store)
    fsx, alarms, env, decision, final_state = _guard_case(case)

    result, did = chain.tick(T0, fsx, alarms=alarms, env=env)

    assert result["decision"] == decision
    assert did["update_calls"] == []
    _, _, new_owner = did["journal"][0]
    assert did["journal"][0][:2] == ("put", "evaluating")
    assert new_owner != OLD_OWNER
    (cid, seq, event, body), = did["archived"]
    assert (cid, seq, event) == (new_owner, 1, "decision")
    assert body["decision"] == decision
    assert body["superseded"] == OLD_OWNER
    if final_state is None:
        assert did["journal"][-1] == ("delete", "evaluating", new_owner)
        assert chain.store == {}
    else:
        assert chain.store[FS]["state"] == final_state
        assert chain.store[FS]["owner"] == new_owner
        assert chain.store[FS]["reason"] == decision
    assert all(r["correlation_id"] == new_owner for _, r in did["reports"])


# -- (b) handler-level release when the ceiling is reached -----------------


@pytest.mark.parametrize("mode", ["notify_only", "approve", "auto"])
@pytest.mark.parametrize(
    "capacity",
    [
        4096,  # target <= current: already at the ceiling
        3800,  # target above current but below the 10% minimum (ceil(3800 * 1.1) = 4180)
    ],
)
def test_ceiling_reached_reports_no_call_and_releases(
    load_handler, monkeypatch, mode: str, capacity: int
) -> None:
    chain = Chain(load_handler, monkeypatch)
    env = {"MODE": mode}

    result, did = chain.tick(T0, FakeFsx([file_system(capacity=capacity)]), env=env)

    assert result["decision"] == "ceiling_reached"
    assert did["update_calls"] == []
    # Acquired, then released by the same owner from evaluating; no other write.
    assert len(did["journal"]) == 2, did["journal"]
    (op1, state1, owner), op2 = did["journal"]
    assert (op1, state1) == ("put", "evaluating")
    assert op2 == ("delete", "evaluating", owner)
    assert chain.store == {}
    # The no-call decision is archived, logged and reported under that owner.
    (cid, seq, event, body), = did["archived"]
    assert (cid, seq, event) == (owner, 1, "decision")
    assert body["decision"] == "ceiling_reached"
    assert body["current_gib"] == capacity
    assert body["ceiling"] == 4096
    assert body["mode"] == mode
    assert body["utilization"]["status"] == "ok"
    (subject, report), = did["reports"]
    assert subject == f"T4 ceiling_reached for {FS}"
    assert report["decision"] == "ceiling_reached"
    assert report["correlation_id"] == owner
    assert report["archive_gap"] is None
    assert any(
        line["event"] == "decision" and line["decision"] == "ceiling_reached"
        for line in log_lines(*chain.logs)
    )

    # Released, not latched: the next tick evaluates afresh under a new ID.
    result2, did2 = chain.tick(T0 + timedelta(hours=1), FakeFsx([file_system(capacity=capacity)]), env=env)
    assert result2["decision"] == "ceiling_reached"
    assert did2["update_calls"] == []
    assert did2["journal"][0][2] != owner
    assert chain.store == {}


# -- (c) delayed visibility over several ticks ------------------------------


def test_delayed_visibility_unmatched_ticks_then_match_then_optimizing_then_terminal(
    load_handler, monkeypatch
) -> None:
    chain = Chain(load_handler, monkeypatch)

    # Tick 1: the call times out on the client side: ambiguous -> indeterminate.
    fsx = FakeFsx([file_system(capacity=1024)])
    fsx.update_result = ReadTimeoutError(endpoint_url="https://fsx.ap-northeast-1.amazonaws.com/")
    result, did = chain.tick(T0, fsx)
    assert result == {"decision": "ambiguous", "state": "indeterminate", "error_code": None}
    assert len(did["update_calls"]) == 1
    item = chain.store[FS]
    owner = item["owner"]
    token = item["client_request_token"]
    assert token == owner == did["update_calls"][0]["ClientRequestToken"]
    target = item["target_gib"]
    assert target == 1127
    request_time = int(item["request_time"])
    assert item["reconcile_until"] == request_time + 6 * 3600
    sequence = item["sequence"]

    def matching(status: str) -> dict[str, Any]:
        return admin_action(
            status=status, target_capacity=target,
            request_time=_at(request_time, 0) + timedelta(seconds=60),
        )

    # Ticks 2-4: AdministrativeActions does not show the request yet.
    unmatched = [
        [],  # nothing visible
        # an older update with the same target: before the recorded request time
        [admin_action(status="COMPLETED", target_capacity=target,
                      request_time=_at(request_time, -1))],
        # a later update with a different target
        [admin_action(status="IN_PROGRESS", target_capacity=target + 1,
                      request_time=_at(request_time, 0) + timedelta(seconds=60))],
    ]
    for hours, actions in zip((1, 2, 3), unmatched, strict=True):
        result, did = chain.tick(
            _at(request_time, hours),
            FakeFsx([file_system(capacity=1024, administrative_actions=actions)]),
        )
        assert result == {"decision": "reconcile_pending"}, hours
        assert did["update_calls"] == []
        assert did["archived"] == []
        assert did["reports"] == []
        assert did["journal"] == []  # the item is not rewritten while pending
        item = chain.store[FS]
        assert item["state"] == "indeterminate"
        assert (item["owner"], item["client_request_token"]) == (owner, token)
        assert item["sequence"] == sequence
        assert len(chain.update_calls) == 1

    # Tick 5: the request becomes visible: reconciled -> submitted.
    result, did = chain.tick(
        _at(request_time, 4),
        FakeFsx([file_system(capacity=1024, administrative_actions=[matching("IN_PROGRESS")])]),
    )
    assert result == {"decision": "reconciled", "state": "submitted"}
    assert did["journal"] == [("put", "submitted", owner)]
    assert chain.store[FS]["report_sent"] is True

    # Tick 6: UPDATED_OPTIMIZING -> capacity_available, state optimizing.
    result, did = chain.tick(
        _at(request_time, 5),
        FakeFsx([file_system(capacity=1127,
                             administrative_actions=[matching("UPDATED_OPTIMIZING")])]),
    )
    assert result == {"decision": "capacity_available", "state": "optimizing"}
    assert did["journal"] == [("put", "optimizing", owner)]

    # Tick 7: still optimizing; the state is kept and nothing is archived.
    optimization = admin_action(action_type="STORAGE_OPTIMIZATION", status="IN_PROGRESS")
    result, did = chain.tick(
        _at(request_time, 5.5),
        FakeFsx([file_system(capacity=1127,
                             administrative_actions=[matching("UPDATED_OPTIMIZING"), optimization])]),
    )
    assert result == {"decision": "optimizing", "status": "UPDATED_OPTIMIZING"}
    assert did["archived"] == [] and did["journal"] == []
    assert chain.store[FS]["state"] == "optimizing"

    # Tick 8: COMPLETED -> terminal, item released.
    result, did = chain.tick(
        _at(request_time, 8),
        FakeFsx([file_system(capacity=1127, administrative_actions=[matching("COMPLETED")])]),
    )
    assert result == {"decision": "terminal", "status": "COMPLETED"}
    assert did["journal"] == [("delete", "optimizing", owner)]
    assert chain.store == {}

    # Across all eight ticks: exactly one UpdateFileSystem call, one token, and
    # one archive sequence under the original correlation ID.
    assert len(chain.update_calls) == 1
    assert [s for _, s, _ in chain.journal if s == "calling"] == ["calling"]
    objects = chain.all_archived()
    assert {cid for cid, _, _, _ in objects} == {owner}
    assert [(seq, event) for _, seq, event, _ in objects] == [
        (1, "decision"), (2, "ambiguous"), (3, "reconciled"),
        (4, "capacity_available"), (5, "terminal"),
    ]
    assert objects[1][3]["request_facts"]["client_request_token"] == token
    assert objects[2][3] == {"source": "administrative_action", "status": "IN_PROGRESS"}
    assert objects[4][3]["status"] == "COMPLETED"
    # The pre-call report carries decision=increase; each later report a reason.
    report_reasons = [r.get("reason", r.get("decision")) for _, r in chain.all_reports()]
    assert report_reasons == ["increase", "ambiguous", "reconciled", "capacity_available", "terminal"]
    assert all(r["correlation_id"] == owner for _, r in chain.all_reports())


# -- (d) a new token only after not_accepted --------------------------------


def test_new_token_only_after_not_accepted_deletes_the_item(load_handler, monkeypatch) -> None:
    chain = Chain(load_handler, monkeypatch)

    # Tick 1: an ambiguous 5xx: indeterminate under owner A.
    fsx = FakeFsx([file_system(capacity=1024)])
    fsx.update_result = make_client_error("InternalServerError", "UpdateFileSystem")
    result, _ = chain.tick(T0, fsx)
    assert result["decision"] == "ambiguous"
    first = chain.store[FS]
    owner_a = first["owner"]
    assert first["client_request_token"] == owner_a
    request_time = int(first["request_time"])

    # Tick 2: the window passes without a match: manual_disposition_required.
    result, did = chain.tick(_at(request_time, 6) + timedelta(seconds=1), FakeFsx([file_system(capacity=1024)]))
    assert result == {"decision": "manual_disposition_required"}
    assert did["update_calls"] == []
    assert chain.store[FS]["state"] == "manual_disposition_required"

    # Tick 3: no disposition yet: the report repeats; no new evaluation, no token.
    result, did = chain.tick(_at(request_time, 7), FakeFsx([file_system(capacity=1024)]))
    assert result == {"decision": "manual_disposition_required"}
    assert did["update_calls"] == []
    assert did["archived"] == []
    assert did["journal"] == []
    assert [r["correlation_id"] for _, r in did["reports"]] == [owner_a]
    assert chain.store[FS]["owner"] == owner_a

    # The operator records not_accepted with the evidence used.
    chain.store[FS]["disposition"] = "not_accepted"
    chain.store[FS]["evidence"] = "no matching FILE_SYSTEM_UPDATE; no CloudTrail event"

    # Tick 4: the disposition is applied: reconciled under A, the item deleted,
    # and this invocation does not start a new evaluation.
    result, did = chain.tick(_at(request_time, 8), FakeFsx([file_system(capacity=1024)]))
    assert result == {"decision": "operator_not_accepted"}
    assert did["update_calls"] == []
    (cid, _, event, body), = did["archived"]
    assert (cid, event) == (owner_a, "reconciled")
    assert body == {"source": "operator", "resulting_state": "not_accepted"}
    assert did["journal"] == [("delete", "manual_disposition_required", owner_a)]
    assert chain.store == {}

    # Up to here every archive object and every call carries A only.
    assert {c for c, _, _, _ in chain.all_archived()} == {owner_a}
    assert [c["ClientRequestToken"] for c in chain.update_calls] == [owner_a]

    # Tick 5: a fresh evaluation with a new correlation ID and token B.
    result, did = chain.tick(_at(request_time, 9), FakeFsx([file_system(capacity=1024)]))
    assert result["decision"] == "submitted"
    owner_b = chain.store[FS]["owner"]
    assert owner_b != owner_a
    assert [c["ClientRequestToken"] for c in did["update_calls"]] == [owner_b]
    assert chain.store[FS]["client_request_token"] == owner_b
    assert {c for c, _, _, _ in did["archived"]} == {owner_b}
    intent = next(b for _, _, e, b in did["archived"] if e == "decision")
    assert intent["superseded"] is None
    assert [c["ClientRequestToken"] for c in chain.update_calls] == [owner_a, owner_b]
