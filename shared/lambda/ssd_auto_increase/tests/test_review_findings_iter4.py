"""Tests for the iteration-4 review findings (review.json).

Each test names the finding it covers:

- Finding 1 (Exact 10% ceiling rejection): integer-rational ceiling arithmetic
  accepts an exact 1,500 -> 1,650 boundary that binary float multiplication
  would reject. (The unit cases live in test_guards.py; this file adds the
  end-to-end auto-call case.)
- Finding 2 (Incomplete and missing state-change reports): later state-change
  reports carry the design's current/target/mode/reason/cooldown/action fields,
  and a pending-event replay sends the state-change report before it advances or
  releases the lock item.
- Finding 3 (Unhandled botocore transport failures): endpoint/connect/read
  timeouts (BotoCoreError, not ClientError) are handled at the archive bucket
  check, object write, decision-log write, and SNS boundaries, in both the
  fail-open (notify_only/approve) and fail-closed (auto) modes.
"""

from __future__ import annotations

import json
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

FS = FILE_SYSTEM_ID


def _tick(load_handler, store, fsx, **kw):
    return load_handler(fsx=fsx, lock_store=store, **kw)


def _report_message(loaded, needle: str) -> dict:
    """Return the parsed JSON body of the first published report matching needle."""
    report = next(p for p in loaded.sns.published if needle in p["Subject"])
    return json.loads(report["Message"])


# -- Finding 1: exact 10% ceiling is accepted end to end -----------------


def test_exact_10_percent_ceiling_calls_in_auto(load_handler) -> None:
    """1500 GiB at a 1650 ceiling must submit, not latch ceiling_reached.

    1500 * 1.10 == 1650 exactly, but binary float multiplication produces
    1650.0000000000002, so a math.ceil floor would reject the valid ceiling.
    """
    store: dict = {}
    loaded = _tick(
        load_handler,
        store,
        FakeFsx([file_system(capacity=1500)]),
        env={"MAX_STORAGE_CAPACITY_GIB": "1650"},
    )
    r = loaded.handler.lambda_handler({}, None)
    assert r["decision"] == "submitted"
    assert store[FS]["target_gib"] == 1650
    assert loaded.fsx.update_calls[0]["StorageCapacity"] == 1650


# -- Finding 2a: later reports carry the full design payload -------------


def test_submitted_report_carries_full_payload(load_handler) -> None:
    """The submitted report carries current/target/mode/reason/cooldown/actions."""
    store: dict = {}
    loaded = _tick(load_handler, store, FakeFsx([file_system(capacity=1024)]))
    loaded.handler.lambda_handler({}, None)
    body = _report_message(loaded, "submitted increase")
    assert body["current_gib"] == 1024
    assert body["target_gib"] == 1127
    assert body["mode"] == "auto"
    assert body["reason"] == "submitted"
    assert body["cooldown_state"] == "clear"
    assert "administrative_actions" in body
    assert body["request_id"] == "req-123"


def test_capacity_available_report_carries_full_payload(load_handler) -> None:
    """The capacity_available report carries the persisted request facts."""
    store: dict = {}
    loaded = _tick(load_handler, store, FakeFsx([file_system(capacity=1024)]))
    loaded.handler.lambda_handler({}, None)
    request_time = datetime.fromtimestamp(int(store[FS]["request_time"]), tz=timezone.utc)
    actions = [
        admin_action(status="UPDATED_OPTIMIZING", target_capacity=1127, request_time=request_time)
    ]
    loaded = _tick(
        load_handler, store,
        FakeFsx([file_system(capacity=1127, administrative_actions=actions)]),
    )
    loaded.handler.lambda_handler({}, None)
    body = _report_message(loaded, "capacity available")
    assert body["current_gib"] == 1024
    assert body["target_gib"] == 1127
    assert body["mode"] == "auto"
    assert body["reason"] == "capacity_available"
    assert body["status"] == "UPDATED_OPTIMIZING"


def test_terminal_report_carries_full_payload(load_handler) -> None:
    """The terminal (final) report carries the persisted request facts."""
    store: dict = {}
    loaded = _tick(load_handler, store, FakeFsx([file_system(capacity=1024)]))
    loaded.handler.lambda_handler({}, None)
    request_time = datetime.fromtimestamp(int(store[FS]["request_time"]), tz=timezone.utc)
    actions = [
        admin_action(status="COMPLETED", target_capacity=1127, request_time=request_time)
    ]
    loaded = _tick(
        load_handler, store,
        FakeFsx([file_system(capacity=1127, administrative_actions=actions)]),
    )
    loaded.handler.lambda_handler({}, None)
    body = _report_message(loaded, "COMPLETED")
    assert body["current_gib"] == 1024
    assert body["target_gib"] == 1127
    assert body["mode"] == "auto"
    assert body["reason"] == "terminal"
    assert body["status"] == "COMPLETED"


# -- Finding 2b: pending-event replay sends the state-change report ------


def test_terminal_archive_failure_pends_report_then_sends_on_replay(load_handler) -> None:
    """A failed terminal write must not delete the item without the final report.

    Tick 1 submits. Tick 2 sees COMPLETED but the terminal archive write fails:
    the item must stay (not released) and no terminal/final report must go out.
    Tick 3 (working S3) flushes the terminal object AND sends the final report,
    only then releasing the item.
    """
    store: dict = {}
    loaded = _tick(load_handler, store, FakeFsx([file_system(capacity=1024)]))
    loaded.handler.lambda_handler({}, None)
    request_time = datetime.fromtimestamp(int(store[FS]["request_time"]), tz=timezone.utc)

    # Tick 2: COMPLETED, but the terminal put fails -> pend, do not release.
    actions = [
        admin_action(status="COMPLETED", target_capacity=1127, request_time=request_time)
    ]
    s3_fail = FakeS3(fail_put_on_event="terminal")
    loaded = _tick(
        load_handler, store,
        FakeFsx([file_system(capacity=1127, administrative_actions=actions)]),
        s3=s3_fail,
    )
    r2 = loaded.handler.lambda_handler({}, None)
    assert r2["decision"] in ("followup_pending", "pending_flush_incomplete")
    assert FS in store  # NOT released
    pending = store[FS].get("pending_events")
    assert pending and pending[-1]["event"] == "terminal"
    assert "report" in pending[-1]
    # No final report (COMPLETED) was sent on tick 2.
    assert not any("COMPLETED" in p["Subject"] for p in loaded.sns.published)

    # Tick 3: working S3 flushes the terminal object and sends the final report.
    s3_ok = FakeS3()
    loaded = _tick(
        load_handler, store,
        FakeFsx([file_system(capacity=1127, administrative_actions=actions)]),
        s3=s3_ok,
    )
    r3 = loaded.handler.lambda_handler({}, None)
    assert r3.get("post_applied") == "release"
    assert store == {}
    assert any(k["Key"].endswith("terminal.json") for k in s3_ok.put_calls)
    body = _report_message(loaded, "COMPLETED")
    assert body["reason"] == "terminal"
    assert body["target_gib"] == 1127


def test_terminal_replay_report_failure_keeps_item(load_handler) -> None:
    """If the replayed terminal report fails, the item is not released.

    The archive object becomes durable on replay, but the final report's SNS
    publish fails: the design gates the release on both, so the item stays with
    its pending entry for the next invocation.
    """
    store: dict = {}
    loaded = _tick(load_handler, store, FakeFsx([file_system(capacity=1024)]))
    loaded.handler.lambda_handler({}, None)
    request_time = datetime.fromtimestamp(int(store[FS]["request_time"]), tz=timezone.utc)
    actions = [
        admin_action(status="COMPLETED", target_capacity=1127, request_time=request_time)
    ]

    # Tick 2: terminal put fails -> pend with report.
    loaded = _tick(
        load_handler, store,
        FakeFsx([file_system(capacity=1127, administrative_actions=actions)]),
        s3=FakeS3(fail_put_on_event="terminal"),
    )
    loaded.handler.lambda_handler({}, None)
    assert FS in store

    # Tick 3: object write succeeds on replay but the final report fails.
    loaded = _tick(
        load_handler, store,
        FakeFsx([file_system(capacity=1127, administrative_actions=actions)]),
        s3=FakeS3(),
        sns=FakeSns(fail_on_subject="COMPLETED"),
    )
    r3 = loaded.handler.lambda_handler({}, None)
    assert r3.get("report_pending") is True
    assert FS in store  # still not released, report still owed
    assert store[FS].get("pending_events")


# -- Finding 3: botocore transport failures ------------------------------


def test_bucket_check_transport_failure_fails_closed_in_auto(load_handler) -> None:
    """A GetBucketObjectLockConfiguration transport timeout fails auto closed.

    No UpdateFileSystem call is made, and the outcome is archive_retention_unproven
    -- the same as a service error on the bucket check.
    """
    store: dict = {}
    loaded = _tick(
        load_handler, store,
        FakeFsx([file_system(capacity=1024)]),
        s3=FakeS3(fail_bucket_check_transport=True),
    )
    r = loaded.handler.lambda_handler({}, None)
    assert r["decision"] == "archive_retention_unproven"
    assert loaded.fsx.update_calls == []


def test_bucket_check_transport_failure_fails_open_in_notify_only(load_handler) -> None:
    """A bucket-check transport timeout continues notify_only with a reported gap."""
    store: dict = {}
    loaded = _tick(
        load_handler, store,
        FakeFsx([file_system(capacity=1024)]),
        s3=FakeS3(fail_bucket_check_transport=True),
        env={"MODE": "notify_only"},
    )
    r = loaded.handler.lambda_handler({}, None)
    assert r["decision"] == "notify_only"
    assert loaded.fsx.update_calls == []
    body = _report_message(loaded, "would increase")
    assert body["bucket_default_gap"]


def test_object_write_transport_failure_fails_intent_closed_in_auto(load_handler) -> None:
    """A PutObject transport timeout on the intent fails the auto call closed."""
    store: dict = {}
    loaded = _tick(
        load_handler, store,
        FakeFsx([file_system(capacity=1024)]),
        s3=FakeS3(fail_put_transport=True),
    )
    r = loaded.handler.lambda_handler({}, None)
    assert r["decision"] == "archive_retention_unproven"
    assert loaded.fsx.update_calls == []


def test_post_call_object_write_transport_failure_pends(load_handler) -> None:
    """A PutObject transport timeout on the accepted event pends, not drops.

    The call happened; the accepted-event write failed with a transport error,
    so the item advances to submitted carrying the pending event, exactly as a
    service PutObject error would.
    """
    store: dict = {}

    class _TransportOnAccepted(FakeS3):
        """Fail put_object with a transport error only for the accepted event."""

        def put_object(self, **kwargs):  # type: ignore[override]
            from ssd_test_support import make_transport_error

            if kwargs.get("Key", "").endswith("accepted.json"):
                raise make_transport_error()
            return super().put_object(**kwargs)

    loaded = _tick(
        load_handler, store,
        FakeFsx([file_system(capacity=1024)]),
        s3=_TransportOnAccepted(),
    )
    r = loaded.handler.lambda_handler({}, None)
    assert r["decision"] == "submitted"
    assert len(loaded.fsx.update_calls) == 1
    pending = store[FS].get("pending_events")
    assert pending and pending[0]["event"] == "accepted"


def test_decision_log_transport_failure_does_not_block(load_handler) -> None:
    """A CloudWatch Logs transport timeout must not stop the archive or call."""
    store: dict = {}
    loaded = _tick(
        load_handler, store,
        FakeFsx([file_system(capacity=1024)]),
        logs=FakeLogs(fail=True, transport=True),
    )
    r = loaded.handler.lambda_handler({}, None)
    assert r["decision"] == "submitted"
    assert len(loaded.fsx.update_calls) == 1


def test_sns_transport_failure_persists_delivery_state(load_handler) -> None:
    """An SNS transport timeout on the submitted report leaves report_sent False.

    The next invocation resends. This proves the SNS boundary persists delivery
    state on a transport failure, not only a service error.
    """
    store: dict = {}
    loaded = _tick(
        load_handler, store,
        FakeFsx([file_system(capacity=1024)]),
        sns=FakeSns(fail_on_subject="submitted increase", transport=True),
    )
    r1 = loaded.handler.lambda_handler({}, None)
    assert r1["decision"] == "submitted"
    assert store[FS]["report_sent"] is False

    loaded = _tick(load_handler, store, FakeFsx([file_system(capacity=1127)]))
    r2 = loaded.handler.lambda_handler({}, None)
    assert r2["decision"] == "report_resent"
    assert store[FS]["report_sent"] is True


def test_blocked_bucket_check_transport_failure_fails_open_report(load_handler) -> None:
    """A blocked-latch refusal still reports when the bucket check times out.

    The deterministic refusal makes no call, so a bucket-check transport failure
    is disclosed in the report (fail-open), not fatal.
    """
    store: dict = {}
    loaded = _tick(
        load_handler, store,
        FakeFsx([file_system(capacity=1024, deployment_type="SINGLE_AZ_1")]),
        s3=FakeS3(fail_bucket_check_transport=True),
        env={"MAX_STORAGE_CAPACITY_GIB": "300000"},
    )
    r = loaded.handler.lambda_handler({}, None)
    assert r["decision"] == "ceiling_exceeds_service_maximum"
    assert loaded.fsx.update_calls == []
