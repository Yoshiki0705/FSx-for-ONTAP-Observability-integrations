"""Lock-state transitions: call failures, reconciliation, optimizing, take-over."""

from __future__ import annotations

from datetime import datetime, timezone

from ssd_test_support import FakeFsx, admin_action, file_system, make_client_error

FS = "fs-0123456789abcdef0"


# -- call result classification -----------------------------------------


def test_deterministic_rejection_latches_blocked(load_handler) -> None:
    fsx = FakeFsx([file_system(capacity=1024)])
    fsx.update_result = make_client_error("BadRequest", "UpdateFileSystem")
    loaded = load_handler(fsx=fsx)
    result = loaded.handler.lambda_handler({}, None)
    assert result["decision"] == "rejected"
    assert result["state"] == "blocked"
    assert loaded.lock_store[FS]["state"] == "blocked"
    # A second tick while blocked makes no further call and sends no report.
    loaded2 = load_handler(fsx=FakeFsx([file_system(capacity=1024)]), lock_store=loaded.lock_store)
    result2 = loaded2.handler.lambda_handler({}, None)
    assert result2["decision"] == "blocked"
    assert loaded2.fsx.update_calls == []
    assert loaded2.sns.published == []


def test_retryable_rejection_deletes_item(load_handler) -> None:
    fsx = FakeFsx([file_system(capacity=1024)])
    fsx.update_result = make_client_error("ThrottlingException", "UpdateFileSystem")
    loaded = load_handler(fsx=fsx)
    result = loaded.handler.lambda_handler({}, None)
    assert result["decision"] == "retryable_rejection"
    assert loaded.lock_store == {}


def test_ambiguous_result_moves_to_indeterminate(load_handler) -> None:
    fsx = FakeFsx([file_system(capacity=1024)])
    fsx.update_result = make_client_error("InternalServerError", "UpdateFileSystem")
    loaded = load_handler(fsx=fsx)
    result = loaded.handler.lambda_handler({}, None)
    assert result["decision"] == "ambiguous"
    assert result["state"] == "indeterminate"
    assert loaded.lock_store[FS]["state"] == "indeterminate"
    assert loaded.lock_store[FS]["client_request_token"]


def test_unlisted_code_is_ambiguous(load_handler) -> None:
    fsx = FakeFsx([file_system(capacity=1024)])
    fsx.update_result = make_client_error("BrandNewError", "UpdateFileSystem")
    loaded = load_handler(fsx=fsx)
    result = loaded.handler.lambda_handler({}, None)
    assert result["decision"] == "ambiguous"


# -- submitted follow-up -------------------------------------------------


def _submitted_item(target: int = 1127, report_sent: bool = True) -> dict:
    return {
        FS: {
            "file_system_id": FS,
            "state": "submitted",
            "owner": "owner-1",
            "expires_at": 9999999999,
            "target_gib": target,
            "request_time": 0,
            "report_sent": report_sent,
        }
    }


def test_submitted_reports_when_report_not_sent(load_handler) -> None:
    store = _submitted_item(report_sent=False)
    loaded = load_handler(fsx=FakeFsx([file_system(capacity=1127)]), lock_store=store)
    result = loaded.handler.lambda_handler({}, None)
    assert result["decision"] == "report_resent"
    assert loaded.fsx.update_calls == []
    assert len(loaded.sns.published) == 1


def test_submitted_to_optimizing_on_updated_optimizing(load_handler) -> None:
    store = _submitted_item()
    actions = [
        admin_action(status="UPDATED_OPTIMIZING", target_capacity=1127, request_time=datetime.now(timezone.utc))
    ]
    loaded = load_handler(
        fsx=FakeFsx([file_system(capacity=1127, administrative_actions=actions)]),
        lock_store=store,
    )
    result = loaded.handler.lambda_handler({}, None)
    assert result["decision"] == "capacity_available"
    assert loaded.lock_store[FS]["state"] == "optimizing"
    assert loaded.fsx.update_calls == []


def test_submitted_to_terminal_on_completed(load_handler) -> None:
    store = _submitted_item()
    actions = [
        admin_action(status="COMPLETED", target_capacity=1127, request_time=datetime.now(timezone.utc))
    ]
    loaded = load_handler(
        fsx=FakeFsx([file_system(capacity=1127, administrative_actions=actions)]),
        lock_store=store,
    )
    result = loaded.handler.lambda_handler({}, None)
    assert result["decision"] == "terminal"
    assert result["status"] == "COMPLETED"
    assert loaded.lock_store == {}


def test_optimizing_to_terminal_on_failed(load_handler) -> None:
    store = {
        FS: {
            "file_system_id": FS,
            "state": "optimizing",
            "owner": "owner-1",
            "expires_at": 9999999999,
            "target_gib": 1127,
            "request_time": 0,
            "report_sent": True,
        }
    }
    actions = [
        admin_action(status="FAILED", target_capacity=1127, request_time=datetime.now(timezone.utc))
    ]
    loaded = load_handler(
        fsx=FakeFsx([file_system(capacity=1127, administrative_actions=actions)]),
        lock_store=store,
    )
    result = loaded.handler.lambda_handler({}, None)
    assert result["decision"] == "terminal"
    assert result["status"] == "FAILED"
    assert loaded.lock_store == {}


# -- indeterminate reconciliation ----------------------------------------


def _indeterminate_item(reconcile_until: int) -> dict:
    return {
        FS: {
            "file_system_id": FS,
            "state": "indeterminate",
            "owner": "owner-1",
            "expires_at": 9999999999,
            "target_gib": 1127,
            "request_time": 0,
            "client_request_token": "owner-1",
            "reconcile_until": reconcile_until,
        }
    }


def test_indeterminate_reconciles_when_action_matches(load_handler) -> None:
    store = _indeterminate_item(reconcile_until=9999999999)
    actions = [
        admin_action(status="IN_PROGRESS", target_capacity=1127, request_time=datetime.now(timezone.utc))
    ]
    loaded = load_handler(
        fsx=FakeFsx([file_system(capacity=1024, administrative_actions=actions)]),
        lock_store=store,
    )
    result = loaded.handler.lambda_handler({}, None)
    assert result["decision"] == "reconciled"
    assert loaded.lock_store[FS]["state"] == "submitted"
    assert loaded.fsx.update_calls == []


def test_indeterminate_pending_before_window(load_handler) -> None:
    store = _indeterminate_item(reconcile_until=9999999999)
    loaded = load_handler(fsx=FakeFsx([file_system(capacity=1024)]), lock_store=store)
    result = loaded.handler.lambda_handler({}, None)
    assert result["decision"] == "reconcile_pending"
    assert loaded.fsx.update_calls == []


def test_indeterminate_escalates_after_window(load_handler) -> None:
    store = _indeterminate_item(reconcile_until=1)  # window already passed
    loaded = load_handler(fsx=FakeFsx([file_system(capacity=1024)]), lock_store=store)
    result = loaded.handler.lambda_handler({}, None)
    assert result["decision"] == "manual_disposition_required"
    assert loaded.lock_store[FS]["state"] == "manual_disposition_required"
    assert loaded.fsx.update_calls == []


# -- manual disposition --------------------------------------------------


def _manual_item(disposition: str | None) -> dict:
    item = {
        "file_system_id": FS,
        "state": "manual_disposition_required",
        "owner": "owner-1",
        "expires_at": 9999999999,
        "target_gib": 1127,
        "request_time": 0,
    }
    if disposition is not None:
        item["disposition"] = disposition
        item["evidence"] = "operator checked AdministrativeActions"
    return {FS: item}


def test_manual_disposition_accepted_moves_to_submitted(load_handler) -> None:
    store = _manual_item("accepted")
    loaded = load_handler(fsx=FakeFsx([file_system(capacity=1127)]), lock_store=store)
    result = loaded.handler.lambda_handler({}, None)
    assert result["decision"] == "operator_accepted"
    assert loaded.lock_store[FS]["state"] == "submitted"
    assert loaded.fsx.update_calls == []


def test_manual_disposition_not_accepted_deletes_item(load_handler) -> None:
    store = _manual_item("not_accepted")
    loaded = load_handler(fsx=FakeFsx([file_system(capacity=1024)]), lock_store=store)
    result = loaded.handler.lambda_handler({}, None)
    assert result["decision"] == "operator_not_accepted"
    assert loaded.lock_store == {}


def test_manual_disposition_no_disposition_repeats_report(load_handler) -> None:
    store = _manual_item(None)
    loaded = load_handler(fsx=FakeFsx([file_system(capacity=1024)]), lock_store=store)
    result = loaded.handler.lambda_handler({}, None)
    assert result["decision"] == "manual_disposition_required"
    assert loaded.fsx.update_calls == []


# -- calling with expired lease -> indeterminate -------------------------


def test_calling_item_expired_lease_moves_to_indeterminate(load_handler) -> None:
    store = {
        FS: {
            "file_system_id": FS,
            "state": "calling",
            "owner": "owner-1",
            "expires_at": 1,  # expired
            "target_gib": 1127,
            "request_time": 100,
            "client_request_token": "owner-1",
        }
    }
    loaded = load_handler(fsx=FakeFsx([file_system(capacity=1024)]), lock_store=store)
    result = loaded.handler.lambda_handler({}, None)
    assert result["decision"] == "ambiguous"
    assert loaded.lock_store[FS]["state"] == "indeterminate"
    assert loaded.fsx.update_calls == []


def test_calling_item_valid_lease_reports_running(load_handler) -> None:
    store = {
        FS: {
            "file_system_id": FS,
            "state": "calling",
            "owner": "owner-1",
            "expires_at": 9999999999,  # valid
            "target_gib": 1127,
            "request_time": 100,
        }
    }
    loaded = load_handler(fsx=FakeFsx([file_system(capacity=1024)]), lock_store=store)
    result = loaded.handler.lambda_handler({}, None)
    assert result["decision"] == "evaluation_already_running"
    assert loaded.fsx.update_calls == []


# -- take-over of an expired evaluating item -----------------------------


def test_take_over_expired_evaluating_item(load_handler) -> None:
    store = {
        FS: {
            "file_system_id": FS,
            "state": "evaluating",
            "owner": "crashed-owner",
            "expires_at": 1,  # expired, no pending_events
        }
    }
    loaded = load_handler(fsx=FakeFsx([file_system(capacity=1024)]), lock_store=store)
    result = loaded.handler.lambda_handler({}, None)
    # The take-over runs every guard again and submits (auto mode calls).
    assert result["decision"] == "submitted"
    assert len(loaded.fsx.update_calls) == 1


def test_accepted_then_report_failed_resends_without_calling(load_handler) -> None:
    # submitted with report_sent False models accepted-then-report-failed.
    store = _submitted_item(report_sent=False)
    loaded = load_handler(fsx=FakeFsx([file_system(capacity=1127)]), lock_store=store)
    result = loaded.handler.lambda_handler({}, None)
    assert result["decision"] == "report_resent"
    assert loaded.fsx.update_calls == []
