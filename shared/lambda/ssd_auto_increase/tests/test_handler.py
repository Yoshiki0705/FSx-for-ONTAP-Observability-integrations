"""Handler-level tests: mode branching and the fresh-evaluation guards.

Every boto3 client is mocked by conftest. These tests assert the decision and
whether UpdateFileSystem was called, over the fresh-evaluation path (no
existing lock item).
"""

from __future__ import annotations

from ssd_test_support import FakeFsx, alarm_response, file_system


def test_auto_below_ceiling_calls_update(load_handler) -> None:
    import uuid

    loaded = load_handler(fsx=FakeFsx([file_system(capacity=1024)]))
    result = loaded.handler.lambda_handler({}, None)
    assert result["decision"] == "submitted"
    assert len(loaded.fsx.update_calls) == 1
    # ceil(1024 * 1.10) = 1127.
    assert loaded.fsx.update_calls[0]["StorageCapacity"] == 1127
    token = loaded.fsx.update_calls[0]["ClientRequestToken"]
    # The ClientRequestToken is the correlation ID (a UUID), the idempotency
    # token, and is distinct from the response request_id. Real equality: the
    # token is a parseable UUID and the submitted lock item recorded it.
    assert uuid.UUID(token)
    assert result["request_id"] == "req-123"
    assert token != result["request_id"]
    submitted = loaded.lock_store["fs-0123456789abcdef0"]
    assert submitted["state"] == "submitted"
    assert submitted["client_request_token"] == token
    # The request facts survived the calling->submitted replace (A1/B3).
    assert submitted["target_gib"] == 1127


def test_alarm_not_in_alarm_releases_and_does_not_call(load_handler) -> None:
    loaded = load_handler(
        fsx=FakeFsx([file_system(capacity=1024)]), alarms=alarm_response("OK")
    )
    result = loaded.handler.lambda_handler({}, None)
    assert result["decision"] == "alarm_not_in_alarm"
    assert loaded.fsx.update_calls == []
    assert loaded.lock_store == {}


def test_alarm_insufficient_data_does_not_call(load_handler) -> None:
    loaded = load_handler(
        fsx=FakeFsx([file_system(capacity=1024)]), alarms=alarm_response("INSUFFICIENT_DATA")
    )
    result = loaded.handler.lambda_handler({}, None)
    assert result["decision"] == "alarm_not_in_alarm"
    assert loaded.fsx.update_calls == []


def test_notify_only_computes_but_does_not_call(load_handler) -> None:
    loaded = load_handler(
        fsx=FakeFsx([file_system(capacity=1024)]),
        env={"MODE": "notify_only"},
    )
    result = loaded.handler.lambda_handler({}, None)
    assert result["decision"] == "notify_only"
    assert result["target_gib"] == 1127
    assert loaded.fsx.update_calls == []
    assert loaded.lock_store == {}
    assert any("would increase" in p["Subject"] for p in loaded.sns.published)


def test_approve_emails_command_and_does_not_call(load_handler) -> None:
    loaded = load_handler(
        fsx=FakeFsx([file_system(capacity=1024)]),
        env={"MODE": "approve"},
    )
    result = loaded.handler.lambda_handler({}, None)
    assert result["decision"] == "approve"
    assert "aws fsx update-file-system" in result["command"]
    assert "--storage-capacity 1127" in result["command"]
    assert loaded.fsx.update_calls == []


def test_ceiling_exceeds_service_maximum_latches_blocked(load_handler) -> None:
    # SINGLE_AZ_1 maximum is 196608; a 300000 ceiling exceeds it.
    loaded = load_handler(
        fsx=FakeFsx([file_system(capacity=1024, deployment_type="SINGLE_AZ_1")]),
        env={"MAX_STORAGE_CAPACITY_GIB": "300000"},
    )
    result = loaded.handler.lambda_handler({}, None)
    assert result["decision"] == "ceiling_exceeds_service_maximum"
    assert result["state"] == "blocked"
    assert loaded.fsx.update_calls == []
    assert loaded.lock_store[loaded.fsx.describe_file_systems([""])["FileSystems"][0]["FileSystemId"]]["state"] == "blocked"


def test_blocked_latch_reports_once_over_several_ticks(load_handler) -> None:
    store: dict = {}
    for _ in range(3):
        loaded = load_handler(
            fsx=FakeFsx([file_system(capacity=1024, deployment_type="SINGLE_AZ_1")]),
            env={"MAX_STORAGE_CAPACITY_GIB": "300000"},
            lock_store=store,
        )
        result = loaded.handler.lambda_handler({}, None)
    # After the first latch, later ticks report "blocked" and send no report.
    assert result["decision"] == "blocked"
    assert loaded.sns.published == []  # the third tick's loaded reporter


def test_blocked_latch_cleared_by_fingerprint_change(load_handler) -> None:
    # A changed fingerprint clears the latch and re-evaluates in the SAME
    # invocation (no extra re-evaluation interval), per the design.
    store: dict = {
        "fs-0123456789abcdef0": {
            "file_system_id": "fs-0123456789abcdef0",
            "state": "blocked",
            "owner": "old-owner",
            "expires_at": 10,
            "reason": "ceiling_exceeds_service_maximum",
            "config_fingerprint": "fingerprint-OLD",
        }
    }
    loaded = load_handler(
        fsx=FakeFsx([file_system(capacity=1024)]),
        lock_store=store,
        env={"CONFIG_FINGERPRINT": "fingerprint-v1"},
    )
    result = loaded.handler.lambda_handler({}, None)
    assert result["decision"] == "blocked_cleared"
    assert result["source"] == "configuration_change"
    # Re-evaluated now: auto below the ceiling submits an increase in this run.
    assert result["reevaluation"]["decision"] == "submitted"
    assert len(loaded.fsx.update_calls) == 1


def test_blocked_latch_cleared_by_operator_disposition(load_handler) -> None:
    # An IAM/quota fix the fingerprint does not cover: the operator records
    # disposition == cleared with evidence; the latch clears and re-evaluates.
    store: dict = {
        "fs-0123456789abcdef0": {
            "file_system_id": "fs-0123456789abcdef0",
            "state": "blocked",
            "owner": "old-owner",
            "expires_at": 10,
            "reason": "deterministic_rejection",
            "error_code": "AccessDeniedException",
            "config_fingerprint": "fingerprint-v1",  # unchanged
            "disposition": "cleared",
            "evidence": "operator granted fsx:UpdateFileSystem",
        }
    }
    loaded = load_handler(
        fsx=FakeFsx([file_system(capacity=1024)]),
        lock_store=store,
        env={"CONFIG_FINGERPRINT": "fingerprint-v1"},
    )
    result = loaded.handler.lambda_handler({}, None)
    assert result["decision"] == "blocked_cleared"
    assert result["source"] == "operator_cleared"
    assert result["reevaluation"]["decision"] == "submitted"
    # The clear archived a reconciled event citing the operator evidence.
    keys = [c["Key"] for c in loaded.s3.put_calls]
    assert any(k.endswith("reconciled.json") for k in keys)


def test_blocked_unchanged_fingerprint_unsupported_disposition_holds(load_handler) -> None:
    # Control: neither an unchanged fingerprint nor an unsupported disposition
    # releases the latch. The item stays blocked and no call or report goes out.
    store: dict = {
        "fs-0123456789abcdef0": {
            "file_system_id": "fs-0123456789abcdef0",
            "state": "blocked",
            "owner": "old-owner",
            "expires_at": 10,
            "reason": "deterministic_rejection",
            "config_fingerprint": "fingerprint-v1",
            "disposition": "acknowledged",  # not the designed "cleared" value
        }
    }
    loaded = load_handler(
        fsx=FakeFsx([file_system(capacity=1024)]),
        lock_store=store,
        env={"CONFIG_FINGERPRINT": "fingerprint-v1"},
    )
    result = loaded.handler.lambda_handler({}, None)
    assert result["decision"] == "blocked"
    assert loaded.lock_store["fs-0123456789abcdef0"]["state"] == "blocked"
    assert loaded.fsx.update_calls == []
    assert loaded.sns.published == []


def test_cooldown_active_defers(load_handler) -> None:
    from datetime import datetime, timedelta, timezone

    from ssd_test_support import admin_action

    now = datetime.now(timezone.utc)
    loaded = load_handler(
        fsx=FakeFsx(
            [
                file_system(
                    capacity=1024,
                    administrative_actions=[
                        admin_action(
                            request_time=now - timedelta(hours=1), status="COMPLETED"
                        )
                    ],
                )
            ]
        )
    )
    result = loaded.handler.lambda_handler({}, None)
    assert result["decision"] == "cooldown_active"
    assert loaded.fsx.update_calls == []
    assert loaded.lock_store == {}


def test_administrative_action_in_progress_defers(load_handler) -> None:
    from ssd_test_support import admin_action

    loaded = load_handler(
        fsx=FakeFsx(
            [
                file_system(
                    capacity=1024,
                    administrative_actions=[admin_action(status="IN_PROGRESS")],
                )
            ]
        )
    )
    result = loaded.handler.lambda_handler({}, None)
    assert result["decision"] == "administrative_action_in_progress"
    assert loaded.fsx.update_calls == []


def test_iops_user_provisioned_included_in_call(load_handler) -> None:
    loaded = load_handler(
        fsx=FakeFsx(
            [file_system(capacity=1024, iops_mode="USER_PROVISIONED", iops=3072)]
        )
    )
    result = loaded.handler.lambda_handler({}, None)
    assert result["decision"] == "submitted"
    call = loaded.fsx.update_calls[0]
    assert call["OntapConfiguration"]["DiskIopsConfiguration"]["Mode"] == "USER_PROVISIONED"
    # 3 * 1127 = 3381.
    assert call["OntapConfiguration"]["DiskIopsConfiguration"]["Iops"] == 3381


def test_iops_exceeds_maximum_latches_blocked(load_handler) -> None:
    # ap-northeast-1 first-gen max is 80,000; target 1127 needs 3381 IOPS, under
    # the max, so force a huge target by a high current capacity near the ceiling.
    loaded = load_handler(
        fsx=FakeFsx(
            [
                file_system(
                    capacity=30000,
                    iops_mode="USER_PROVISIONED",
                    iops=0,
                    deployment_type="SINGLE_AZ_1",
                )
            ]
        ),
        env={"MAX_STORAGE_CAPACITY_GIB": "40000"},
    )
    result = loaded.handler.lambda_handler({}, None)
    # ceil(30000*1.10) = 33000; 3 * 33000 = 99000 > 80000.
    assert result["decision"] == "iops_exceeds_maximum"
    assert result["state"] == "blocked"
    assert loaded.fsx.update_calls == []


def test_lock_contention_reports_already_running(load_handler) -> None:
    store = {
        "fs-0123456789abcdef0": {
            "file_system_id": "fs-0123456789abcdef0",
            "state": "evaluating",
            "owner": "other-owner",
            "expires_at": 9999999999,  # valid lease far in the future
        }
    }
    loaded = load_handler(fsx=FakeFsx([file_system(capacity=1024)]), lock_store=store)
    result = loaded.handler.lambda_handler({}, None)
    assert result["decision"] == "evaluation_already_running"
    assert loaded.fsx.update_calls == []


def test_run_time_ceiling_recheck_on_shrunk_shape(load_handler) -> None:
    # Deployed ceiling 4096 is fine for SINGLE_AZ_1, but if the shape were a
    # type with a smaller max the run-time check catches it. Here we simulate a
    # ceiling above the maximum via the env, already covered; this asserts the
    # re-check reads DeploymentType/HAPairs from DescribeFileSystems.
    loaded = load_handler(
        fsx=FakeFsx([file_system(capacity=1024, deployment_type="SINGLE_AZ_2", ha_pairs=1)]),
        env={"MAX_STORAGE_CAPACITY_GIB": "600000"},
    )
    result = loaded.handler.lambda_handler({}, None)
    # SINGLE_AZ_2 ha_pairs=1 maximum is 524288 < 600000.
    assert result["decision"] == "ceiling_exceeds_service_maximum"
