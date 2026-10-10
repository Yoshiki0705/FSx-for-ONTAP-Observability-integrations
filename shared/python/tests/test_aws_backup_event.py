"""Tests for the shared AWS Backup / RAM event normaliser.

Fixtures mirror the documented EventBridge event shapes
(https://docs.aws.amazon.com/aws-backup/latest/devguide/eventbridge.html and
https://docs.aws.amazon.com/ram/latest/userguide/using-eventbridge.html). All
account IDs and ARNs are documentation placeholders (123456789012).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import aws_backup_event  # noqa: E402

_ACCOUNT = "123456789012"
_REGION = "ap-northeast-1"


def _envelope(detail_type: str, source: str, detail: dict, **extra) -> dict:
    """Build an EventBridge envelope with the common top-level fields."""
    event = {
        "version": "0",
        "id": "12345678-1234-1234-1234-123456789012",
        "detail-type": detail_type,
        "source": source,
        "account": _ACCOUNT,
        "time": "2026-08-07T12:00:00Z",
        "region": _REGION,
        "detail": detail,
    }
    event.update(extra)
    return event


def backup_job(state: str, status_message: str = "") -> dict:
    detail = {
        "state": state,
        "resourceType": "FSx",
        "resourceArn": f"arn:aws:fsx:{_REGION}:{_ACCOUNT}:volume/fsvol-0abc",
        "backupVaultName": "fsxn-lag-vault",
        "backupJobId": "job-11112222",
    }
    if status_message:
        detail["statusMessage"] = status_message
    return _envelope("Backup Job State Change", "aws.backup", detail)


def copy_job(state: str) -> dict:
    detail = {
        "state": state,
        "resourceType": "FSx",
        "resourceArn": f"arn:aws:fsx:{_REGION}:{_ACCOUNT}:volume/fsvol-0abc",
        "sourceBackupVaultArn": f"arn:aws:backup:{_REGION}:{_ACCOUNT}:backup-vault:src",
        "destinationBackupVaultArn": (
            f"arn:aws:backup:{_REGION}:{_ACCOUNT}:backup-vault:lag"
        ),
        "copyJobId": "copy-33334444",
    }
    return _envelope("Copy Job State Change", "aws.backup", detail)


def restore_job(status: str) -> dict:
    detail = {
        "status": status,
        "resourceType": "FSx",
        "restoreJobId": "restore-55556666",
        "backupVaultArn": f"arn:aws:backup:{_REGION}:{_ACCOUNT}:backup-vault:lag",
    }
    return _envelope("Restore Job State Change", "aws.backup", detail)


def ram_state_change(status: str) -> dict:
    detail = {"event": "Resource Share Association", "status": status}
    return _envelope("Resource Sharing State Change", "aws.ram", detail)


def ram_cloudtrail(event_name: str) -> dict:
    detail = {
        "eventSource": "ram.amazonaws.com",
        "eventName": event_name,
        "requestParameters": {
            "resourceShareArn": (
                f"arn:aws:ram:{_REGION}:{_ACCOUNT}:resource-share/abc"
            )
        },
    }
    return _envelope("AWS API Call via CloudTrail", "aws.ram", detail)


def recovery_point(status: str) -> dict:
    arn = f"arn:aws:backup:{_REGION}:{_ACCOUNT}:recovery-point:rp-7777"
    detail = {
        "status": status,
        "backupVaultName": "fsxn-lag-vault",
        "resourceType": "FSx",
    }
    return _envelope(
        "Recovery Point State Change", "aws.backup", detail, resources=[arn]
    )


class TestExtractDetail:
    def test_returns_detail(self):
        event = backup_job("FAILED")
        assert aws_backup_event.extract_detail(event) is event["detail"]

    def test_missing_detail_raises(self):
        with pytest.raises(ValueError, match="detail is missing"):
            aws_backup_event.extract_detail({"source": "aws.backup"})

    def test_wrong_detail_type_raises(self):
        with pytest.raises(ValueError, match="Unexpected detail type"):
            aws_backup_event.extract_detail({"detail": "not-a-dict"})


class TestBackupJob:
    def test_failed(self):
        n = aws_backup_event.normalize_backup_event(backup_job("FAILED"))
        assert n["classification"] == "failed"
        assert n["state"] == "FAILED"
        assert n["source"] == "aws.backup"
        assert n["job_id"] == "job-11112222"

    def test_completed_with_status_message_is_completed_with_issues(self):
        n = aws_backup_event.normalize_backup_event(
            backup_job("COMPLETED", "AWS managed-key file system was not copied")
        )
        assert n["classification"] == "completed_with_issues"
        assert n["status_message"]
        assert n["state"] == "COMPLETED"

    def test_completed_without_status_message_is_ok(self):
        n = aws_backup_event.normalize_backup_event(backup_job("COMPLETED"))
        assert n["classification"] == "ok"
        assert n["status_message"] == ""


class TestCopyJob:
    def test_failed_reads_state_not_status(self):
        n = aws_backup_event.normalize_backup_event(copy_job("FAILED"))
        assert n["classification"] == "failed"
        assert n["state"] == "FAILED"
        assert n["job_id"] == "copy-33334444"
        assert n["destination_backup_vault_arn"].endswith("backup-vault:lag")

    def test_completed_copy_job_is_not_completed_with_issues(self):
        """Completed-with-issues is a backup-job-only concept, never copy."""
        n = aws_backup_event.normalize_backup_event(copy_job("COMPLETED"))
        assert n["classification"] == "ok"


class TestRestoreJob:
    def test_failed_reads_status_not_state(self):
        n = aws_backup_event.normalize_backup_event(restore_job("FAILED"))
        assert n["state"] == "FAILED"
        assert n["classification"] == "failed"
        assert n["job_id"] == "restore-55556666"

    def test_completed(self):
        n = aws_backup_event.normalize_backup_event(restore_job("COMPLETED"))
        assert n["state"] == "COMPLETED"
        assert n["classification"] == "ok"


class TestRam:
    def test_state_change_failed(self):
        n = aws_backup_event.normalize_backup_event(ram_state_change("failed"))
        assert n["classification"] == "failed"
        assert n["source"] == "aws.ram"

    def test_cloudtrail_disassociate_is_revoked(self):
        n = aws_backup_event.normalize_backup_event(
            ram_cloudtrail("DisassociateResourceShare")
        )
        assert n["classification"] == "revoked"
        assert n["source"] == "aws.ram"

    def test_cloudtrail_delete_is_revoked(self):
        n = aws_backup_event.normalize_backup_event(
            ram_cloudtrail("DeleteResourceShare")
        )
        assert n["classification"] == "revoked"


class TestRecoveryPoint:
    def test_resource_arn_from_top_level_resources(self):
        n = aws_backup_event.normalize_backup_event(recovery_point("COMPLETED"))
        assert n["resource_arn"].endswith("recovery-point:rp-7777")
        assert n["state"] == "COMPLETED"
        assert n["classification"] == "ok"


class TestDefaultsAndRaw:
    def test_missing_detail_raises_through_normalizer(self):
        with pytest.raises(ValueError, match="detail is missing"):
            aws_backup_event.normalize_backup_event({"source": "aws.backup"})

    def test_non_dict_detail_raises_through_normalizer(self):
        with pytest.raises(ValueError, match="Unexpected detail type"):
            aws_backup_event.normalize_backup_event({"detail": "oops"})

    def test_empty_string_defaults_and_raw_identity(self):
        event = _envelope("Backup Vault State Change", "aws.backup", {})
        n = aws_backup_event.normalize_backup_event(event)
        for key in (
            "status_message",
            "resource_type",
            "resource_arn",
            "backup_vault_name",
            "source_backup_vault_arn",
            "destination_backup_vault_arn",
            "job_id",
            "state",
        ):
            assert n[key] == "", key
        assert n["raw"] is event
