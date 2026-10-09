"""Tests for the iteration-2 review findings (review-b).

Each test names the finding it covers:

- Finding 2: the second pre-call snapshot reruns both the administrative-action
  and the cooldown guard, and a guard that now blocks supersedes the archived
  increase intent with an explicit no-call decision event.
- Finding 3: reconciled / capacity_available / terminal write failures pend the
  event (handled "as for accepted") instead of advancing or releasing; and a
  successful write whose DynamoDB acknowledgement fails is flushed idempotently
  (the If-None-Match: * re-PUT precondition failure is proven, not "write
  failed", so the pending entry clears).
- Finding 4: later reports carry the original evaluation's correlation ID
  (item.owner), and a failed initial blocked report is retried.
- Finding 5: the absent/short bucket-default retention cases for notify_only and
  approve, and the fail-closed outcome recorded to the decision log with its
  decision value preserved.
- Finding 6: the approve command is one shell-safe argument for both IOPS modes.
- Finding 9: every STORAGE_OPTIMIZATION status except COMPLETED blocks; every
  FILE_SYSTEM_UPDATE active status blocks.
"""

from __future__ import annotations

import json
import shlex
from datetime import datetime, timedelta, timezone

from ssd_test_support import (
    FILE_SYSTEM_ID,
    FakeFsx,
    FakeS3,
    FakeSns,
    admin_action,
    alarm_response,
    file_system,
)

FS = FILE_SYSTEM_ID


def _tick(load_handler, store, fsx, **kw):
    return load_handler(fsx=fsx, lock_store=store, **kw)


# -- Finding 2: second pre-call snapshot reruns both guards ---------------


def test_second_snapshot_cooldown_supersedes_intent(load_handler) -> None:
    """A cooldown that becomes visible on the second read blocks the call.

    The first DescribeFileSystems shows no cooldown; the second (held under the
    lock, after the intent is archived) shows a recent terminal action that
    starts the six-hour cooldown. The call must not go out, and the archived
    increase intent must be superseded by a cooldown_active decision event.
    """
    store: dict = {}
    now = datetime.now(timezone.utc)
    clean = file_system(capacity=1024)
    # Second describe shows a 1-hour-old COMPLETED action -> cooldown active.
    with_cooldown = file_system(
        capacity=1024,
        administrative_actions=[
            admin_action(status="COMPLETED", request_time=now - timedelta(hours=1))
        ],
    )
    # FakeFsx returns the first element then pops; feed clean (evaluate read),
    # then with_cooldown (the second pre-call read in _auto).
    fsx = FakeFsx([clean, with_cooldown])
    s3 = FakeS3()
    loaded = _tick(load_handler, store, fsx, s3=s3)
    result = loaded.handler.lambda_handler({}, None)
    assert result["decision"] == "cooldown_active"
    assert result.get("superseded_intent") is True
    assert loaded.fsx.update_calls == []
    # The intent was archived, then a superseding cooldown_active decision.
    keys = [c["Key"] for c in s3.put_calls]
    assert any(k.endswith("decision.json") for k in keys)
    # Two decision events (intent + superseding), distinct sequences.
    decision_keys = [k for k in keys if k.endswith("decision.json")]
    assert len(decision_keys) == 2
    assert len(set(decision_keys)) == 2


def test_second_snapshot_admin_action_supersedes_intent(load_handler) -> None:
    """An admin action appearing on the second read supersedes the intent."""
    store: dict = {}
    clean = file_system(capacity=1024)
    with_action = file_system(
        capacity=1024, administrative_actions=[admin_action(status="IN_PROGRESS")]
    )
    fsx = FakeFsx([clean, with_action])
    s3 = FakeS3()
    loaded = _tick(load_handler, store, fsx, s3=s3)
    result = loaded.handler.lambda_handler({}, None)
    assert result["decision"] == "administrative_action_in_progress"
    assert result.get("superseded_intent") is True
    assert loaded.fsx.update_calls == []
    decision_keys = [c["Key"] for c in s3.put_calls if c["Key"].endswith("decision.json")]
    assert len(decision_keys) == 2


# -- Finding 3: follow-up events pend on write failure --------------------


def _seed_submitted(store, *, state="submitted", sequence=2, owner="owner-1"):
    store[FS] = {
        "file_system_id": FS,
        "state": state,
        "owner": owner,
        "expires_at": 9999999999,
        "target_gib": 1127,
        "request_time": 0,
        "report_sent": True,
        "sequence": sequence,
    }


def _matching(status: str):
    return [admin_action(status=status, target_capacity=1127, request_time=datetime.now(timezone.utc))]


def test_terminal_write_failure_pends_and_keeps_item(load_handler) -> None:
    """A failed terminal write pends the event; the item is NOT deleted."""
    store: dict = {}
    _seed_submitted(store, state="optimizing")
    s3 = FakeS3(fail_put_on_event="terminal")
    loaded = _tick(
        load_handler, store,
        FakeFsx([file_system(capacity=1127, administrative_actions=_matching("COMPLETED"))]),
        s3=s3,
    )
    result = loaded.handler.lambda_handler({}, None)
    assert result["decision"] == "followup_pending"
    # The item survives (not released) and carries the pending terminal event.
    assert FS in store
    pending = store[FS]["pending_events"]
    assert pending and pending[0]["event"] == "terminal"


def test_capacity_available_write_failure_pends(load_handler) -> None:
    """A failed capacity_available write pends and keeps the submitted state."""
    store: dict = {}
    _seed_submitted(store, state="submitted")
    s3 = FakeS3(fail_put_on_event="capacity_available")
    loaded = _tick(
        load_handler, store,
        FakeFsx([file_system(capacity=1127, administrative_actions=_matching("UPDATED_OPTIMIZING"))]),
        s3=s3,
    )
    result = loaded.handler.lambda_handler({}, None)
    assert result["decision"] == "followup_pending"
    assert store[FS]["state"] == "submitted"  # not advanced to optimizing
    assert store[FS]["pending_events"][0]["event"] == "capacity_available"


def test_reconciled_write_failure_pends_indeterminate(load_handler) -> None:
    """A failed reconciled write from indeterminate pends and keeps the state."""
    store: dict = {
        FS: {
            "file_system_id": FS,
            "state": "indeterminate",
            "owner": "owner-1",
            "expires_at": 9999999999,
            "target_gib": 1127,
            "request_time": 0,
            "client_request_token": "owner-1",
            "reconcile_until": 9999999999,
            "sequence": 2,
        }
    }
    s3 = FakeS3(fail_put_on_event="reconciled")
    loaded = _tick(
        load_handler, store,
        FakeFsx([file_system(capacity=1024, administrative_actions=_matching("IN_PROGRESS"))]),
        s3=s3,
    )
    result = loaded.handler.lambda_handler({}, None)
    assert result["decision"] == "followup_pending"
    assert store[FS]["state"] == "indeterminate"  # not advanced to submitted
    assert store[FS]["pending_events"][0]["event"] == "reconciled"


def test_pending_flush_is_idempotent_when_object_already_written(load_handler) -> None:
    """A pending event whose object already exists flushes without stranding.

    Models the success/acknowledgement race: a prior invocation wrote the S3
    object but its DynamoDB clear failed, so the item still lists the pending
    event. The flush re-PUTs the same key; If-None-Match: * fails the
    precondition, which the archive proves as already-written (not "write
    failed"), so the pending entry clears instead of becoming unflushable.
    """
    store: dict = {}
    # Seed an optimizing item with a pending terminal event whose object the
    # (fictional) earlier invocation already wrote.
    seq = 3
    terminal_key = (
        f"fsx-ssd-auto-increase/{FS}/owner-1/{seq}-terminal.json"
    )
    store[FS] = {
        "file_system_id": FS,
        "state": "optimizing",
        "owner": "owner-1",
        "expires_at": 9999999999,
        "target_gib": 1127,
        "request_time": 0,
        "report_sent": True,
        "sequence": 2,
        "pending_events": [
            {"event": "terminal", "body": {"status": "COMPLETED"}, "sequence": seq}
        ],
    }
    s3 = FakeS3(preexisting_keys={terminal_key})
    loaded = _tick(
        load_handler, store,
        FakeFsx([file_system(capacity=1127, administrative_actions=_matching("COMPLETED"))]),
        s3=s3,
    )
    result = loaded.handler.lambda_handler({}, None)
    # The pending event flushed (its object proven already-written), so the
    # handler proceeded to act; the item is not stuck with pending_events.
    assert result["decision"] != "pending_flush_incomplete"
    if FS in store:
        assert "pending_events" not in store[FS]


# -- Finding 4: original correlation ID in later reports ------------------


def test_capacity_available_report_uses_original_owner_id(load_handler) -> None:
    """The capacity_available report carries item.owner, not the follower ID."""
    store: dict = {}
    _seed_submitted(store, state="submitted", owner="original-owner-123")
    loaded = _tick(
        load_handler, store,
        FakeFsx([file_system(capacity=1127, administrative_actions=_matching("UPDATED_OPTIMIZING"))]),
    )
    loaded.handler.lambda_handler({}, None)
    report = next(p for p in loaded.sns.published if "capacity available" in p["Subject"])
    body = json.loads(report["Message"])
    assert body["correlation_id"] == "original-owner-123"


def test_terminal_report_uses_original_owner_id(load_handler) -> None:
    store: dict = {}
    _seed_submitted(store, state="optimizing", owner="original-owner-xyz")
    loaded = _tick(
        load_handler, store,
        FakeFsx([file_system(capacity=1127, administrative_actions=_matching("COMPLETED"))]),
    )
    loaded.handler.lambda_handler({}, None)
    report = next(p for p in loaded.sns.published if "COMPLETED" in p["Subject"])
    body = json.loads(report["Message"])
    assert body["correlation_id"] == "original-owner-xyz"


def test_submitted_resent_report_uses_original_owner_id(load_handler) -> None:
    """A resent submitted report carries item.owner, not the follower ID."""
    store: dict = {}
    store[FS] = {
        "file_system_id": FS,
        "state": "submitted",
        "owner": "original-owner-abc",
        "expires_at": 9999999999,
        "target_gib": 1127,
        "request_time": 0,
        "report_sent": False,
        "request_id": "req-xyz",
        "sequence": 2,
    }
    loaded = _tick(load_handler, store, FakeFsx([file_system(capacity=1127)]))
    result = loaded.handler.lambda_handler({}, None)
    assert result["decision"] == "report_resent"
    report = next(p for p in loaded.sns.published if "submitted increase" in p["Subject"])
    body = json.loads(report["Message"])
    assert body["correlation_id"] == "original-owner-abc"


def test_failed_initial_blocked_report_is_retried(load_handler) -> None:
    """A blocked latch whose first report fails is retried next invocation.

    Finding 4: a failed initial blocked publish must not make the latch silent.
    report_sent is persisted False, and a later invocation (SNS working) resends.
    """
    store: dict = {}
    # Tick 1: ceiling exceeds the maximum; SNS fails on the blocked report.
    failing_sns = FakeSns(fail=True)
    loaded = _tick(
        load_handler, store,
        FakeFsx([file_system(capacity=1024, deployment_type="SINGLE_AZ_1")]),
        sns=failing_sns,
        env={"MAX_STORAGE_CAPACITY_GIB": "300000"},
    )
    r1 = loaded.handler.lambda_handler({}, None)
    assert r1["decision"] == "ceiling_exceeds_service_maximum"
    assert store[FS]["state"] == "blocked"
    assert store[FS]["report_sent"] is False

    # Tick 2: SNS works; the blocked report is retried and report_sent becomes True.
    loaded = _tick(
        load_handler, store,
        FakeFsx([file_system(capacity=1024, deployment_type="SINGLE_AZ_1")]),
        env={"MAX_STORAGE_CAPACITY_GIB": "300000"},
    )
    r2 = loaded.handler.lambda_handler({}, None)
    assert r2["decision"] == "blocked_report_sent"
    assert store[FS]["report_sent"] is True
    assert any("blocked" in p["Subject"] for p in loaded.sns.published)


def test_blocked_report_uses_original_owner_id_on_retry(load_handler) -> None:
    store: dict = {}
    failing_sns = FakeSns(fail=True)
    loaded = _tick(
        load_handler, store,
        FakeFsx([file_system(capacity=1024, deployment_type="SINGLE_AZ_1")]),
        sns=failing_sns,
        env={"MAX_STORAGE_CAPACITY_GIB": "300000"},
    )
    loaded.handler.lambda_handler({}, None)
    owner = store[FS]["owner"]
    loaded = _tick(
        load_handler, store,
        FakeFsx([file_system(capacity=1024, deployment_type="SINGLE_AZ_1")]),
        env={"MAX_STORAGE_CAPACITY_GIB": "300000"},
    )
    loaded.handler.lambda_handler({}, None)
    report = next(p for p in loaded.sns.published if "blocked" in p["Subject"])
    body = json.loads(report["Message"])
    assert body["correlation_id"] == owner


# -- Finding 5: bucket-default retention matrix for notify_only/approve ---


def test_notify_only_absent_bucket_default_names_gap(load_handler) -> None:
    """notify_only with Object Lock disabled reports the bucket-default gap."""
    for mode in ("notify_only", "approve"):
        store: dict = {}
        loaded = _tick(
            load_handler, store, FakeFsx([file_system(capacity=1024)]),
            s3=FakeS3(lock_enabled=False, lock_mode="COMPLIANCE"),
            env={"MODE": mode, "DECISION_ARCHIVE_REQUIRED_MODE": "COMPLIANCE"},
        )
        result = loaded.handler.lambda_handler({}, None)
        assert result["decision"] == mode
        assert loaded.fsx.update_calls == []
        report = next(p for p in loaded.sns.published if FS in p["Message"])
        body = json.loads(report["Message"])
        assert body["bucket_default_gap"] is not None


def test_notify_only_short_bucket_default_names_gap(load_handler) -> None:
    """notify_only with a too-short bucket default retention reports the gap."""
    for mode in ("notify_only", "approve"):
        store: dict = {}
        loaded = _tick(
            load_handler, store, FakeFsx([file_system(capacity=1024)]),
            s3=FakeS3(lock_days=30),  # below the 365-day minimum
            env={"MODE": mode, "DECISION_ARCHIVE_REQUIRED_MODE": "COMPLIANCE"},
        )
        result = loaded.handler.lambda_handler({}, None)
        assert result["decision"] == mode
        report = next(p for p in loaded.sns.published if FS in p["Message"])
        body = json.loads(report["Message"])
        assert body["bucket_default_gap"] is not None


def test_notify_only_proven_bucket_default_no_gap(load_handler) -> None:
    """notify_only with a compliant bucket default reports no bucket gap."""
    store: dict = {}
    loaded = _tick(
        load_handler, store, FakeFsx([file_system(capacity=1024)]),
        s3=FakeS3(lock_mode="COMPLIANCE", lock_days=365),
        env={"MODE": "notify_only", "DECISION_ARCHIVE_REQUIRED_MODE": "COMPLIANCE"},
    )
    result = loaded.handler.lambda_handler({}, None)
    assert result["decision"] == "notify_only"
    report = next(p for p in loaded.sns.published if "would increase" in p["Subject"])
    body = json.loads(report["Message"])
    assert body["bucket_default_gap"] is None


def test_auto_retention_unproven_records_decision_in_log(load_handler) -> None:
    """Finding 5: the auto fail-closed outcome keeps its decision value.

    The decision-log line for the refused evaluation must say
    archive_retention_unproven, not be overwritten to increase.
    """
    from ssd_test_support import FakeLogs

    store: dict = {}
    logs = FakeLogs()
    loaded = _tick(
        load_handler, store, FakeFsx([file_system(capacity=1024)]),
        s3=FakeS3(lock_enabled=False), logs=logs,
    )
    result = loaded.handler.lambda_handler({}, None)
    assert result["decision"] == "archive_retention_unproven"
    messages = [e["message"] for e in logs.events]
    assert any('"decision": "archive_retention_unproven"' in m for m in messages)
    # It must not have been overwritten to the increase decision in the log.
    unproven_lines = [m for m in messages if "archive_retention_unproven" in m]
    assert unproven_lines
    for line in unproven_lines:
        parsed = json.loads(line)
        assert parsed["decision"] == "archive_retention_unproven"


# -- Finding 6: approve command is one shell-safe argument ----------------


def test_approve_command_user_provisioned_is_one_shell_token(load_handler) -> None:
    """The --ontap-configuration JSON must be a single shell argument.

    shlex.split must keep the JSON as one token after --ontap-configuration, so
    a pasted command passes one AWS CLI JSON value, not several arguments.
    """
    loaded = load_handler(
        fsx=FakeFsx([file_system(capacity=1024, iops_mode="USER_PROVISIONED", iops=3072)]),
        env={"MODE": "approve"},
    )
    result = loaded.handler.lambda_handler({}, None)
    command = result["command"]
    tokens = shlex.split(command)
    idx = tokens.index("--ontap-configuration")
    json_token = tokens[idx + 1]
    # The next single token must parse as the full JSON value (not split).
    parsed = json.loads(json_token)
    assert parsed["DiskIopsConfiguration"]["Mode"] == "USER_PROVISIONED"
    assert parsed["DiskIopsConfiguration"]["Iops"] == 3381
    # And it is the last pair: no stray tokens from a split JSON.
    assert len(tokens) == idx + 2


def test_approve_command_automatic_tokenizes_cleanly(load_handler) -> None:
    """AUTOMATIC IOPS: no --ontap-configuration, and the command tokenizes."""
    loaded = load_handler(
        fsx=FakeFsx([file_system(capacity=1024, iops_mode="AUTOMATIC")]),
        env={"MODE": "approve"},
    )
    result = loaded.handler.lambda_handler({}, None)
    tokens = shlex.split(result["command"])
    assert "--ontap-configuration" not in tokens
    assert tokens[:3] == ["aws", "fsx", "update-file-system"]
    assert "--storage-capacity" in tokens
    assert tokens[tokens.index("--storage-capacity") + 1] == "1127"


# -- Finding 9: STORAGE_OPTIMIZATION and FILE_SYSTEM_UPDATE status matrix --


def test_storage_optimization_non_completed_statuses_block(load_handler) -> None:
    """Every STORAGE_OPTIMIZATION status except COMPLETED blocks a new call.

    The design lists CANCELLED among the documented statuses; it is terminal for
    a FILE_SYSTEM_UPDATE but a STORAGE_OPTIMIZATION that is not COMPLETED still
    blocks, so it is included in this negative control alongside the others.
    """
    for status in (
        "PENDING", "IN_PROGRESS", "UPDATED_OPTIMIZING", "OPTIMIZING", "PAUSED",
        "FAILED", "CANCELLED",
    ):
        loaded = load_handler(
            fsx=FakeFsx([
                file_system(
                    capacity=1024,
                    administrative_actions=[
                        admin_action(action_type="STORAGE_OPTIMIZATION", status=status)
                    ],
                )
            ]),
        )
        result = loaded.handler.lambda_handler({}, None)
        assert result["decision"] == "administrative_action_in_progress", status
        assert loaded.fsx.update_calls == []


def test_storage_optimization_completed_does_not_block(load_handler) -> None:
    """A COMPLETED STORAGE_OPTIMIZATION does not block (it no longer counts)."""
    loaded = load_handler(
        fsx=FakeFsx([
            file_system(
                capacity=1024,
                administrative_actions=[
                    admin_action(action_type="STORAGE_OPTIMIZATION", status="COMPLETED")
                ],
            )
        ]),
    )
    result = loaded.handler.lambda_handler({}, None)
    # COMPLETED optimization no longer blocks; the increase proceeds.
    assert result["decision"] == "submitted"


def test_file_system_update_active_statuses_block(load_handler) -> None:
    """Every active FILE_SYSTEM_UPDATE status blocks a new call."""
    for status in ("PENDING", "IN_PROGRESS", "UPDATED_OPTIMIZING", "OPTIMIZING", "PAUSED"):
        loaded = load_handler(
            fsx=FakeFsx([
                file_system(
                    capacity=1024,
                    administrative_actions=[admin_action(status=status)],
                )
            ]),
        )
        result = loaded.handler.lambda_handler({}, None)
        assert result["decision"] == "administrative_action_in_progress", status
        assert loaded.fsx.update_calls == []
