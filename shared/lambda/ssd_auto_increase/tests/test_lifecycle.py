"""End-to-end lifecycle and failure-recovery tests driven through shared state.

These drive several invocations against one lock_store, so the real
calling->submitted->optimizing->terminal transitions run (not preconstructed
items). This is the path that a single-invocation assertion cannot cover: a
request fact dropped on the submitted write only surfaces on the next
invocation's _match_action.

Failure-capable fakes (FakeSns, FakeS3 per-event failure, FakeLogs) exercise
report failure, post-call archive recovery, the decision log, and the
retention-mode matrix.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ssd_test_support import (
    FILE_SYSTEM_ID,
    FakeFsx,
    FakeLogs,
    FakeS3,
    FakeSns,
    admin_action,
    file_system,
)

FS_ID = FILE_SYSTEM_ID


def _tick(load_handler, store, fsx, **kw):
    return load_handler(fsx=fsx, lock_store=store, **kw)


# -- full auto lifecycle across invocations ------------------------------


def test_auto_full_lifecycle_submitted_optimizing_terminal(load_handler) -> None:
    """Drive one request through its whole life, re-running _match_action.

    This fails if the calling->submitted write drops target_gib/request_time,
    because the second invocation's _match_action would never match (the defect
    A1/B3 guards against).
    """
    store: dict = {}

    # Tick 1: fresh auto evaluation submits the increase.
    loaded = _tick(load_handler, store, FakeFsx([file_system(capacity=1024)]))
    r1 = loaded.handler.lambda_handler({}, None)
    assert r1["decision"] == "submitted"
    assert store[FS_ID]["state"] == "submitted"
    target = store[FS_ID]["target_gib"]
    assert target == 1127

    # Derive the admin action's RequestTime from the recorded request_time the
    # handler stored (epoch seconds), not from a wall-clock reading taken before
    # the first invocation. _match_action compares action_time >= request_time,
    # so using the stored integer avoids an integer-second-truncation boundary
    # flake when the two readings land on the same second.
    request_time = datetime.fromtimestamp(
        int(store[FS_ID]["request_time"]), tz=timezone.utc
    )

    # Tick 2: the matching FILE_SYSTEM_UPDATE is IN_PROGRESS -> awaiting_action,
    # the lock stays submitted (no release).
    actions = [admin_action(status="IN_PROGRESS", target_capacity=1127, request_time=request_time)]
    loaded = _tick(
        load_handler, store, FakeFsx([file_system(capacity=1024, administrative_actions=actions)])
    )
    r2 = loaded.handler.lambda_handler({}, None)
    assert r2["decision"] in ("awaiting_action", "optimizing")
    assert store[FS_ID]["state"] == "submitted"

    # Tick 3: UPDATED_OPTIMIZING -> capacity_available, state optimizing.
    actions = [
        admin_action(status="UPDATED_OPTIMIZING", target_capacity=1127, request_time=request_time)
    ]
    loaded = _tick(
        load_handler, store, FakeFsx([file_system(capacity=1127, administrative_actions=actions)])
    )
    r3 = loaded.handler.lambda_handler({}, None)
    assert r3["decision"] == "capacity_available"
    assert store[FS_ID]["state"] == "optimizing"

    # Tick 4: COMPLETED -> terminal, item released.
    actions = [admin_action(status="COMPLETED", target_capacity=1127, request_time=request_time)]
    loaded = _tick(
        load_handler, store, FakeFsx([file_system(capacity=1127, administrative_actions=actions)])
    )
    r4 = loaded.handler.lambda_handler({}, None)
    assert r4["decision"] == "terminal"
    assert r4["status"] == "COMPLETED"
    assert store == {}


# -- terminal / error matrix (CANCELLED and all terminal statuses) -------


def _seed_submitted(store, *, state="submitted", sequence=2):
    store[FS_ID] = {
        "file_system_id": FS_ID,
        "state": state,
        "owner": "owner-1",
        "expires_at": 9999999999,
        "target_gib": 1127,
        "request_time": 0,
        "report_sent": True,
        "sequence": sequence,
    }


def test_cancelled_from_submitted_is_terminal(load_handler) -> None:
    store: dict = {}
    _seed_submitted(store)
    actions = [
        admin_action(status="CANCELLED", target_capacity=1127, request_time=datetime.now(timezone.utc))
    ]
    loaded = _tick(
        load_handler, store, FakeFsx([file_system(capacity=1024, administrative_actions=actions)])
    )
    r = loaded.handler.lambda_handler({}, None)
    assert r["decision"] == "terminal"
    assert r["status"] == "CANCELLED"
    assert store == {}


def test_cancelled_from_optimizing_is_terminal(load_handler) -> None:
    store: dict = {}
    _seed_submitted(store, state="optimizing")
    actions = [
        admin_action(status="CANCELLED", target_capacity=1127, request_time=datetime.now(timezone.utc))
    ]
    loaded = _tick(
        load_handler, store, FakeFsx([file_system(capacity=1127, administrative_actions=actions)])
    )
    r = loaded.handler.lambda_handler({}, None)
    assert r["decision"] == "terminal"
    assert r["status"] == "CANCELLED"
    assert store == {}


def test_completed_from_optimizing_is_terminal(load_handler) -> None:
    store: dict = {}
    _seed_submitted(store, state="optimizing")
    actions = [
        admin_action(status="COMPLETED", target_capacity=1127, request_time=datetime.now(timezone.utc))
    ]
    loaded = _tick(
        load_handler, store, FakeFsx([file_system(capacity=1127, administrative_actions=actions)])
    )
    r = loaded.handler.lambda_handler({}, None)
    assert r["decision"] == "terminal"
    assert store == {}


def test_failed_from_submitted_is_terminal(load_handler) -> None:
    store: dict = {}
    _seed_submitted(store)
    actions = [
        admin_action(status="FAILED", target_capacity=1127, request_time=datetime.now(timezone.utc))
    ]
    loaded = _tick(
        load_handler, store, FakeFsx([file_system(capacity=1024, administrative_actions=actions)])
    )
    r = loaded.handler.lambda_handler({}, None)
    assert r["decision"] == "terminal"
    assert r["status"] == "FAILED"
    assert store == {}


# -- report failure recovery (report_sent only true after SNS succeeds) --


def test_submitted_report_failure_is_recovered_next_invocation(load_handler) -> None:
    """An SNS failure on the submitted report leaves report_sent False.

    The next invocation (with a working SNS) resends and only then marks it
    True. This fails if report_sent is set True before SNS succeeds (B3).
    """
    store: dict = {}
    # Tick 1: auto submits, but the submitted report publish fails.
    failing_sns = FakeSns(fail_on_subject="submitted increase")
    loaded = _tick(
        load_handler, store, FakeFsx([file_system(capacity=1024)]), sns=failing_sns
    )
    r1 = loaded.handler.lambda_handler({}, None)
    assert r1["decision"] == "submitted"
    # The pre-call report ("will increase") did go out; the submitted one did not.
    assert store[FS_ID]["state"] == "submitted"
    assert store[FS_ID]["report_sent"] is False

    # Tick 2: SNS works; the report is resent and report_sent becomes True.
    loaded = _tick(load_handler, store, FakeFsx([file_system(capacity=1127)]))
    r2 = loaded.handler.lambda_handler({}, None)
    assert r2["decision"] == "report_resent"
    assert store[FS_ID]["report_sent"] is True
    assert any("submitted increase" in p["Subject"] for p in loaded.sns.published)


# -- post-call archive recovery (pending replay) -------------------------


def test_post_call_archive_failure_pends_then_flushes(load_handler) -> None:
    """A failed accepted-event write is pended and flushed next invocation.

    The call still happened (fsx update_calls == 1), the item advances to
    submitted carrying pending_events, and the next invocation writes the
    pending accepted object before acting. This fails if pending events are
    dropped by the submitted replace or never flushed (A3/B3).
    """
    store: dict = {}
    # Fail only the accepted (post-call) put; the intent (decision) put succeeds.
    s3 = FakeS3(fail_put_on_event="accepted")
    loaded = _tick(load_handler, store, FakeFsx([file_system(capacity=1024)]), s3=s3)
    r1 = loaded.handler.lambda_handler({}, None)
    assert r1["decision"] == "submitted"
    assert len(loaded.fsx.update_calls) == 1
    pending = store[FS_ID].get("pending_events")
    assert pending and pending[0]["event"] == "accepted"
    # The intent decision object was written; the accepted one was not.
    keys = [c["Key"] for c in s3.put_calls]
    assert any(k.endswith("decision.json") for k in keys)
    assert not any(k.endswith("accepted.json") for k in keys)

    # Tick 2: a working S3 flushes the pending accepted event before acting.
    s3b = FakeS3()
    loaded = _tick(load_handler, store, FakeFsx([file_system(capacity=1127)]), s3=s3b)
    loaded.handler.lambda_handler({}, None)
    flushed_keys = [c["Key"] for c in s3b.put_calls]
    assert any(k.endswith("accepted.json") for k in flushed_keys)
    assert "pending_events" not in store[FS_ID]


def test_post_call_retention_gap_is_reported(load_handler) -> None:
    """A written-but-unproven post-call object reports a gap, not silence.

    The accepted object is written but its retention proof is too short; the
    design requires the gap be named, not treated as a clean write (B5).
    """
    store: dict = {}
    s3 = FakeS3(unproven_object_on_event="accepted")
    loaded = _tick(load_handler, store, FakeFsx([file_system(capacity=1024)]), s3=s3)
    r = loaded.handler.lambda_handler({}, None)
    assert r["decision"] == "submitted"
    assert any(
        "post-call archive retention unproven" in p["Subject"] for p in loaded.sns.published
    )


# -- decision log emission -----------------------------------------------


def test_decision_log_receives_events(load_handler) -> None:
    """The decision log group receives one line per archive event (B1)."""
    store: dict = {}
    logs = FakeLogs()
    loaded = _tick(load_handler, store, FakeFsx([file_system(capacity=1024)]), logs=logs)
    loaded.handler.lambda_handler({}, None)
    # Intent (decision) and accepted both logged.
    messages = [e["message"] for e in logs.events]
    assert any('"event": "decision"' in m for m in messages)
    assert any('"event": "accepted"' in m for m in messages)
    # The log line carries the correlation ID and the file system ID.
    assert all('"file_system_id": "fs-0123456789abcdef0"' in m for m in messages)


def test_decision_log_failure_does_not_block_evaluation(load_handler) -> None:
    """A decision-log write failure must not stop the archive or the call."""
    store: dict = {}
    logs = FakeLogs(fail=True)
    loaded = _tick(load_handler, store, FakeFsx([file_system(capacity=1024)]), logs=logs)
    r = loaded.handler.lambda_handler({}, None)
    assert r["decision"] == "submitted"
    assert len(loaded.fsx.update_calls) == 1


# -- no-call archive-gap disclosure --------------------------------------


def test_alarm_not_in_alarm_write_failure_names_gap(load_handler) -> None:
    """A deterministic no-call path whose archive write fails reports the gap."""
    from ssd_test_support import alarm_response

    store: dict = {}
    s3 = FakeS3(fail_put=True)
    loaded = _tick(
        load_handler,
        store,
        FakeFsx([file_system(capacity=1024)]),
        s3=s3,
        alarms=alarm_response("OK"),
    )
    r = loaded.handler.lambda_handler({}, None)
    assert r["decision"] == "alarm_not_in_alarm"
    assert r["archive_gap"] == "write failed"
    report = next(p for p in loaded.sns.published if "alarm_not_in_alarm" in p["Subject"])
    assert '"archive_gap": "write failed"' in report["Message"]


def test_blocked_latch_write_failure_names_gap(load_handler) -> None:
    """The deterministic refusal latch names the gap when the archive fails."""
    store: dict = {}
    s3 = FakeS3(fail_put=True)
    loaded = _tick(
        load_handler,
        store,
        FakeFsx([file_system(capacity=1024, deployment_type="SINGLE_AZ_1")]),
        s3=s3,
        env={"MAX_STORAGE_CAPACITY_GIB": "300000"},
    )
    r = loaded.handler.lambda_handler({}, None)
    assert r["decision"] == "ceiling_exceeds_service_maximum"
    assert r["archive_gap"] == "write failed"


# -- retention-mode matrix across all three modes ------------------------


def test_retention_matrix_governance_in_each_mode(load_handler) -> None:
    """GOVERNANCE: blocks the call in auto, continues in notify_only/approve.

    auto requires COMPLIANCE (load_config enforces it), so a GOVERNANCE run in
    auto cannot be constructed; the deploy-time precondition and load_config
    reject it. These two prove the fail-open side.
    """
    for mode in ("notify_only", "approve"):
        store: dict = {}
        loaded = _tick(
            load_handler,
            store,
            FakeFsx([file_system(capacity=1024)]),
            s3=FakeS3(lock_mode="GOVERNANCE", object_mode="GOVERNANCE"),
            env={"MODE": mode, "DECISION_ARCHIVE_REQUIRED_MODE": "GOVERNANCE"},
        )
        r = loaded.handler.lambda_handler({}, None)
        assert r["decision"] == mode
        assert loaded.fsx.update_calls == []


def test_archive_sequences_are_unique_across_lifecycle(load_handler) -> None:
    """Every archive object key across the lifecycle is distinct.

    The intent, accepted, capacity_available and terminal events must each get
    their own sequence; a follow-up event reusing the intent/accepted sequence
    would overwrite (If-None-Match: *) or clash. Collect every put key across
    the four invocations and assert they are unique.
    """
    store: dict = {}
    all_keys: list[str] = []

    s3a = FakeS3()
    loaded = _tick(load_handler, store, FakeFsx([file_system(capacity=1024)]), s3=s3a)
    loaded.handler.lambda_handler({}, None)
    all_keys += [c["Key"] for c in s3a.put_calls]

    # Derive RequestTime from the recorded request_time (epoch seconds), not a
    # pre-invocation wall-clock reading, to avoid an integer-second boundary flake.
    request_time = datetime.fromtimestamp(
        int(store[FS_ID]["request_time"]), tz=timezone.utc
    )
    actions = [
        admin_action(status="UPDATED_OPTIMIZING", target_capacity=1127, request_time=request_time)
    ]
    s3b = FakeS3()
    loaded = _tick(
        load_handler, store,
        FakeFsx([file_system(capacity=1127, administrative_actions=actions)]), s3=s3b,
    )
    loaded.handler.lambda_handler({}, None)
    all_keys += [c["Key"] for c in s3b.put_calls]

    actions = [admin_action(status="COMPLETED", target_capacity=1127, request_time=request_time)]
    s3c = FakeS3()
    loaded = _tick(
        load_handler, store,
        FakeFsx([file_system(capacity=1127, administrative_actions=actions)]), s3=s3c,
    )
    loaded.handler.lambda_handler({}, None)
    all_keys += [c["Key"] for c in s3c.put_calls]

    assert len(all_keys) == len(set(all_keys)), f"duplicate archive keys: {all_keys}"
    # The follow-up events carry sequences past the accepted event's, not 0.
    assert any(k.endswith("capacity_available.json") for k in all_keys)
    assert any(k.endswith("terminal.json") for k in all_keys)
    assert not any("/0-" in k for k in all_keys)


def test_retention_matrix_compliance_auto_calls(load_handler) -> None:
    """COMPLIANCE in auto proves and calls."""
    store: dict = {}
    loaded = _tick(
        load_handler,
        store,
        FakeFsx([file_system(capacity=1024)]),
        s3=FakeS3(lock_mode="COMPLIANCE", object_mode="COMPLIANCE"),
    )
    r = loaded.handler.lambda_handler({}, None)
    assert r["decision"] == "submitted"
    assert len(loaded.fsx.update_calls) == 1
