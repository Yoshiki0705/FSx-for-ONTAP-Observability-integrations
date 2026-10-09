"""Tests for the iteration-3 review findings (review.json).

Each test names the finding it covers:

- Finding 1: ``_match_action`` requires a usable ``RequestTime`` at or after the
  recorded request time (missing, malformed, earlier and boundary cases).
- Finding 2: ``not_accepted`` runs the final service-side action lookup first
  and prefers a newly visible match over the disposition.
- Finding 3: a retryable rejection whose archive write failed keeps a durable
  release intent, so the replay releases the item instead of being reclassified
  as indeterminate (ThrottlingException and ExpiredTokenException).
- Finding 4: early no-call and blocked decisions report the bucket-default gap,
  and a replay whose object is unproven still reports the retention gap.
- Finding 5: a failed archive write and its replay produce distinct decision-log
  attempt records that carry the archive sequence (cardinality + sequence).
- Finding 6: the dual-write-failure path (archive + lock-table both fail) keeps
  the item ``calling`` for reconciliation.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from ssd_test_support import (
    FILE_SYSTEM_ID,
    FakeFsx,
    FakeLogs,
    FakeS3,
    admin_action,
    file_system,
)

FS = FILE_SYSTEM_ID


def _tick(load_handler, store, fsx, **kw):
    return load_handler(fsx=fsx, lock_store=store, **kw)


def _indeterminate_item(*, target_gib=1127, request_time=1000, owner="owner-1", sequence=2):
    return {
        FS: {
            "file_system_id": FS,
            "state": "indeterminate",
            "owner": owner,
            "expires_at": 9999999999,
            "target_gib": target_gib,
            "request_time": request_time,
            "client_request_token": owner,
            "reconcile_until": 9999999999,
            "sequence": sequence,
        }
    }


def _manual_item(*, disposition=None, target_gib=1127, request_time=1000, owner="owner-1"):
    item = {
        "file_system_id": FS,
        "state": "manual_disposition_required",
        "owner": owner,
        "expires_at": 9999999999,
        "target_gib": target_gib,
        "request_time": request_time,
        "client_request_token": owner,
        "sequence": 2,
    }
    if disposition is not None:
        item["disposition"] = disposition
    return {FS: item}


# -- Finding 1: _match_action requires a usable RequestTime --------------


def test_match_rejects_action_without_request_time(load_handler) -> None:
    """A matching target but no RequestTime is not accepted (fail closed)."""
    store = _indeterminate_item()
    # Target matches, but the action carries no RequestTime at all.
    action = admin_action(status="IN_PROGRESS", target_capacity=1127)
    loaded = _tick(
        load_handler, store,
        FakeFsx([file_system(capacity=1024, administrative_actions=[action])]),
    )
    result = loaded.handler.lambda_handler({}, None)
    assert result["decision"] == "reconcile_pending"
    assert store[FS]["state"] == "indeterminate"


def test_match_rejects_action_with_malformed_request_time(load_handler) -> None:
    """A matching target but a non-datetime RequestTime is not accepted."""
    store = _indeterminate_item()
    action = {
        "AdministrativeActionType": "FILE_SYSTEM_UPDATE",
        "Status": "IN_PROGRESS",
        "RequestTime": "2026-10-09T00:00:00Z",  # a string, not a datetime
        "TargetFileSystemValues": {"StorageCapacity": 1127},
    }
    loaded = _tick(
        load_handler, store,
        FakeFsx([file_system(capacity=1024, administrative_actions=[action])]),
    )
    result = loaded.handler.lambda_handler({}, None)
    assert result["decision"] == "reconcile_pending"
    assert store[FS]["state"] == "indeterminate"


def test_match_rejects_action_earlier_than_request_time(load_handler) -> None:
    """An action whose RequestTime predates the recorded request is not matched."""
    request_epoch = int(datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc).timestamp())
    store = _indeterminate_item(request_time=request_epoch)
    earlier = admin_action(
        status="IN_PROGRESS",
        target_capacity=1127,
        request_time=datetime(2026, 10, 9, 11, 0, tzinfo=timezone.utc),  # one hour earlier
    )
    loaded = _tick(
        load_handler, store,
        FakeFsx([file_system(capacity=1024, administrative_actions=[earlier])]),
    )
    result = loaded.handler.lambda_handler({}, None)
    assert result["decision"] == "reconcile_pending"
    assert store[FS]["state"] == "indeterminate"


def test_match_accepts_action_at_request_time_boundary(load_handler) -> None:
    """An action whose RequestTime equals the recorded request time matches."""
    boundary = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
    request_epoch = int(boundary.timestamp())
    store = _indeterminate_item(request_time=request_epoch)
    at_boundary = admin_action(
        status="IN_PROGRESS", target_capacity=1127, request_time=boundary
    )
    loaded = _tick(
        load_handler, store,
        FakeFsx([file_system(capacity=1024, administrative_actions=[at_boundary])]),
    )
    result = loaded.handler.lambda_handler({}, None)
    assert result["decision"] == "reconciled"
    assert store[FS]["state"] == "submitted"


# -- Finding 2: not_accepted runs the final match first ------------------


def test_not_accepted_prefers_newly_visible_match(load_handler) -> None:
    """A not_accepted item with a newly visible matching action reconciles.

    The service-side match (a FILE_SYSTEM_UPDATE at or after the request time
    with the recorded target) is preferred over the operator's not_accepted, so
    the item moves to submitted rather than being deleted.
    """
    boundary = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
    store = _manual_item(disposition="not_accepted", request_time=int(boundary.timestamp()))
    match = admin_action(status="IN_PROGRESS", target_capacity=1127, request_time=boundary)
    loaded = _tick(
        load_handler, store,
        FakeFsx([file_system(capacity=1024, administrative_actions=[match])]),
    )
    result = loaded.handler.lambda_handler({}, None)
    assert result["decision"] == "reconciled"
    assert store[FS]["state"] == "submitted"
    assert loaded.fsx.update_calls == []


def test_not_accepted_without_match_closes_request(load_handler) -> None:
    """With no matching action, not_accepted still closes (deletes) the item."""
    store = _manual_item(disposition="not_accepted")
    loaded = _tick(
        load_handler, store,
        FakeFsx([file_system(capacity=1024, administrative_actions=[])]),
    )
    result = loaded.handler.lambda_handler({}, None)
    assert result["decision"] == "operator_not_accepted"
    assert FS not in store


# -- Finding 3: retryable rejection replay releases ----------------------


def _calling_item(*, owner="owner-1", target_gib=1127, sequence=1):
    return {
        FS: {
            "file_system_id": FS,
            "state": "calling",
            "owner": owner,
            "expires_at": 9999999999,
            "target_gib": target_gib,
            "iops": None,
            "client_request_token": owner,
            "request_time": 1000,
            "sequence": sequence,
            "report_sent": False,
        }
    }


def _run_retryable_replay(load_handler, error_code: str) -> dict:
    """Drive a retryable rejection whose rejected archive write fails, then replay."""
    store: dict = {}
    # Tick 1: a fresh evaluation that calls UpdateFileSystem and gets the
    # retryable error; the rejected archive write fails, so the item is kept
    # calling with a durable release intent and a pending rejected event.
    fsx = FakeFsx([file_system(capacity=1024), file_system(capacity=1024)])
    from ssd_test_support import make_client_error

    fsx.update_result = make_client_error(error_code, "UpdateFileSystem")
    s3 = FakeS3(fail_put_once_on_event="rejected")
    loaded = _tick(load_handler, store, fsx, s3=s3)
    r1 = loaded.handler.lambda_handler({}, None)
    assert r1["decision"] == "retryable_rejection"
    assert store[FS]["state"] == "calling"
    assert store[FS]["release_after_flush"] == "retryable_rejection"
    assert store[FS]["pending_events"][0]["event"] == "rejected"

    # Tick 2: the next scheduled run flushes the pending rejected event (now the
    # S3 write succeeds) and applies the recorded release, deleting the item,
    # rather than letting the lease expire into indeterminate.
    s3b = FakeS3()
    loaded2 = _tick(load_handler, store, FakeFsx([file_system(capacity=1024)]), s3=s3b)
    r2 = loaded2.handler.lambda_handler({}, None)
    return {"r2": r2, "store": store}


def test_retryable_throttling_replay_releases(load_handler) -> None:
    out = _run_retryable_replay(load_handler, "ThrottlingException")
    assert out["r2"]["decision"] == "retryable_rejection"
    assert out["r2"]["released"] is True
    assert FS not in out["store"]


def test_retryable_expired_token_replay_releases(load_handler) -> None:
    out = _run_retryable_replay(load_handler, "ExpiredTokenException")
    assert out["r2"]["decision"] == "retryable_rejection"
    assert out["r2"]["released"] is True
    assert FS not in out["store"]


# -- Finding 4: bucket-default gap on no-call paths and replay -----------


def test_cooldown_no_call_reports_bucket_default_gap(load_handler) -> None:
    """A cooldown no-call decision still reports the bucket-default gap."""
    now = datetime.now(timezone.utc)
    store: dict = {}
    fs = file_system(
        capacity=1024,
        administrative_actions=[
            admin_action(status="COMPLETED", request_time=now - timedelta(hours=1))
        ],
    )
    loaded = _tick(
        load_handler, store, FakeFsx([fs]), s3=FakeS3(lock_enabled=False)
    )
    result = loaded.handler.lambda_handler({}, None)
    assert result["decision"] == "cooldown_active"
    report = next(p for p in loaded.sns.published if "cooldown_active" in p["Subject"])
    body = json.loads(report["Message"])
    assert body["bucket_default_gap"] is not None


def test_ceiling_blocked_reports_bucket_default_gap(load_handler) -> None:
    """A deterministic ceiling refusal reports the bucket-default gap."""
    store: dict = {}
    loaded = _tick(
        load_handler, store,
        FakeFsx([file_system(capacity=1024, deployment_type="SINGLE_AZ_1")]),
        s3=FakeS3(lock_enabled=False),
        env={"MAX_STORAGE_CAPACITY_GIB": "300000"},
    )
    result = loaded.handler.lambda_handler({}, None)
    assert result["decision"] == "ceiling_exceeds_service_maximum"
    report = next(p for p in loaded.sns.published if "blocked" in p["Subject"])
    body = json.loads(report["Message"])
    assert body["bucket_default_gap"] is not None


def test_alarm_ok_no_call_reports_bucket_default_gap(load_handler) -> None:
    """An alarm-not-in-ALARM no-call decision reports the bucket-default gap."""
    from ssd_test_support import alarm_response

    store: dict = {}
    loaded = _tick(
        load_handler, store, FakeFsx([file_system(capacity=1024)]),
        alarms=alarm_response("OK"), s3=FakeS3(lock_enabled=False),
    )
    result = loaded.handler.lambda_handler({}, None)
    assert result["decision"] == "alarm_not_in_alarm"
    report = next(p for p in loaded.sns.published if "alarm_not_in_alarm" in p["Subject"])
    body = json.loads(report["Message"])
    assert body["bucket_default_gap"] is not None


def test_replay_reports_unproven_retention_before_clearing(load_handler) -> None:
    """A pending event whose replayed object is retention-unproven is reported.

    The object exists (so the pending entry clears), but its retention is
    unproven, so the post-call retention gap is reported during the flush rather
    than dropped.
    """
    store: dict = {}
    seq = 3
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
    # The replayed terminal object proves with a too-short retention.
    s3 = FakeS3(unproven_object_on_event="terminal")
    loaded = _tick(
        load_handler, store,
        FakeFsx([file_system(capacity=1127, administrative_actions=[
            admin_action(status="COMPLETED", target_capacity=1127,
                         request_time=datetime.now(timezone.utc))
        ])]),
        s3=s3,
    )
    loaded.handler.lambda_handler({}, None)
    assert any(
        "retention unproven" in p["Subject"] for p in loaded.sns.published
    )


# -- Finding 5: decision-log cardinality and sequence --------------------


def test_failed_write_then_replay_logs_distinct_attempts(load_handler) -> None:
    """A failed archive write and its replay log distinct, sequence-tagged lines.

    One archive event (the terminal), two decision-log attempt records: the
    first with archive_result=write_failed, the second with written (or
    replayed). Both carry the same sequence so they tie to one archive key.
    """
    logs = FakeLogs()
    store: dict = {}
    seq_base = 2
    store[FS] = {
        "file_system_id": FS,
        "state": "optimizing",
        "owner": "owner-1",
        "expires_at": 9999999999,
        "target_gib": 1127,
        "request_time": 0,
        "report_sent": True,
        "sequence": seq_base,
    }
    s3 = FakeS3(fail_put_once_on_event="terminal")
    matching = [admin_action(status="COMPLETED", target_capacity=1127,
                             request_time=datetime.now(timezone.utc))]
    # Tick 1: terminal write fails -> pended, one write_failed log line.
    loaded = _tick(
        load_handler, store,
        FakeFsx([file_system(capacity=1127, administrative_actions=matching)]),
        s3=s3, logs=logs,
    )
    loaded.handler.lambda_handler({}, None)
    # Tick 2: flush replays the terminal -> one more log line (written), same seq.
    loaded2 = _tick(
        load_handler, store,
        FakeFsx([file_system(capacity=1127, administrative_actions=matching)]),
        s3=s3, logs=logs,
    )
    loaded2.handler.lambda_handler({}, None)

    terminal_lines = [
        json.loads(e["message"])
        for e in logs.events
        if json.loads(e["message"]).get("event") == "terminal"
    ]
    # Two attempt records for one archive event.
    assert len(terminal_lines) == 2
    results = {line["archive_result"] for line in terminal_lines}
    assert "write_failed" in results
    assert results & {"written", "replayed"}
    # Both carry the same archive sequence, so they tie to one archive key.
    sequences = {line["sequence"] for line in terminal_lines}
    assert sequences == {seq_base + 1}


# -- Finding 6: dual-write-failure keeps the item calling ----------------


def test_dual_write_failure_keeps_item_calling(load_handler) -> None:
    """An archive write failure plus a lock-table write failure keeps calling.

    The post-call accepted archive write fails (pended), and the lock-table put
    that would record the pending state also fails once. The design says the
    item then stays calling for reconciliation; it is neither advanced nor
    deleted, so the next invocation reclassifies it from the still-current state.
    """
    from ssd_test_support import make_client_error

    store: dict = {}
    fsx = FakeFsx([file_system(capacity=1024), file_system(capacity=1024)])
    fsx.update_result = {"ResponseMetadata": {"RequestId": "req-1"}}
    # The accepted archive write fails (pended), and the submitted lock put fails.
    s3 = FakeS3(fail_put_on_event="accepted")
    loaded = _tick(
        load_handler, store, fsx, s3=s3, fail_put_states={"submitted"},
    )
    result = loaded.handler.lambda_handler({}, None)
    # The call went out; the item must not have advanced past calling and must
    # not have been deleted.
    assert loaded.fsx.update_calls, "the update call should have been made"
    assert result["decision"] == "submitted"
    assert FS in store
    assert store[FS]["state"] == "calling"
