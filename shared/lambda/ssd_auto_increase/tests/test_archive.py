"""Decision archive: retention proof fail-closed in auto, fail-open elsewhere."""

from __future__ import annotations

from ssd_test_support import FakeFsx, FakeS3, file_system


def test_auto_bucket_without_object_lock_blocks_call(load_handler) -> None:
    loaded = load_handler(
        fsx=FakeFsx([file_system(capacity=1024)]),
        s3=FakeS3(lock_enabled=False),
    )
    result = loaded.handler.lambda_handler({}, None)
    assert result["decision"] == "archive_retention_unproven"
    assert loaded.fsx.update_calls == []


def test_auto_governance_bucket_blocks_call(load_handler) -> None:
    # DECISION_ARCHIVE_REQUIRED_MODE is COMPLIANCE in auto; a GOVERNANCE bucket
    # fails the bucket retention check.
    loaded = load_handler(
        fsx=FakeFsx([file_system(capacity=1024)]),
        s3=FakeS3(lock_mode="GOVERNANCE"),
    )
    result = loaded.handler.lambda_handler({}, None)
    assert result["decision"] == "archive_retention_unproven"
    assert loaded.fsx.update_calls == []


def test_auto_short_retention_blocks_call(load_handler) -> None:
    loaded = load_handler(
        fsx=FakeFsx([file_system(capacity=1024)]),
        s3=FakeS3(lock_days=30),  # below the 365-day minimum
    )
    result = loaded.handler.lambda_handler({}, None)
    assert result["decision"] == "archive_retention_unproven"
    assert loaded.fsx.update_calls == []


def test_auto_intent_write_failure_blocks_call(load_handler) -> None:
    loaded = load_handler(
        fsx=FakeFsx([file_system(capacity=1024)]),
        s3=FakeS3(fail_put=True),
    )
    result = loaded.handler.lambda_handler({}, None)
    assert result["decision"] == "archive_retention_unproven"
    assert loaded.fsx.update_calls == []


def test_auto_object_retention_too_short_blocks_call(load_handler) -> None:
    loaded = load_handler(
        fsx=FakeFsx([file_system(capacity=1024)]),
        s3=FakeS3(object_days=30),  # object written with less than the minimum
    )
    result = loaded.handler.lambda_handler({}, None)
    assert result["decision"] == "archive_retention_unproven"
    assert loaded.fsx.update_calls == []


def test_notify_only_governance_bucket_continues_and_names_gap(load_handler) -> None:
    loaded = load_handler(
        fsx=FakeFsx([file_system(capacity=1024)]),
        s3=FakeS3(lock_mode="GOVERNANCE", object_mode="GOVERNANCE"),
        env={"MODE": "notify_only", "DECISION_ARCHIVE_REQUIRED_MODE": "GOVERNANCE"},
    )
    result = loaded.handler.lambda_handler({}, None)
    # notify_only fails open: the evaluation continues to the notify decision.
    assert result["decision"] == "notify_only"


def test_notify_only_write_failure_continues(load_handler) -> None:
    loaded = load_handler(
        fsx=FakeFsx([file_system(capacity=1024)]),
        s3=FakeS3(fail_put=True),
        env={"MODE": "notify_only"},
    )
    result = loaded.handler.lambda_handler({}, None)
    assert result["decision"] == "notify_only"
    assert result["archive_gap"] is not None


def test_auto_writes_intent_before_calling(load_handler) -> None:
    s3 = FakeS3()
    loaded = load_handler(fsx=FakeFsx([file_system(capacity=1024)]), s3=s3)
    result = loaded.handler.lambda_handler({}, None)
    assert result["decision"] == "submitted"
    # The intent (decision) object is written before the accepted object.
    keys = [c["Key"] for c in s3.put_calls]
    assert any("decision.json" in k for k in keys)
    assert any("accepted.json" in k for k in keys)
    decision_idx = next(i for i, k in enumerate(keys) if k.endswith("decision.json"))
    accepted_idx = next(i for i, k in enumerate(keys) if k.endswith("accepted.json"))
    assert decision_idx < accepted_idx


def test_put_object_uses_if_none_match_and_checksum(load_handler) -> None:
    s3 = FakeS3()
    loaded = load_handler(fsx=FakeFsx([file_system(capacity=1024)]), s3=s3)
    loaded.handler.lambda_handler({}, None)
    for call in s3.put_calls:
        assert call["IfNoneMatch"] == "*"
        assert call["ChecksumAlgorithm"] == "SHA256"
