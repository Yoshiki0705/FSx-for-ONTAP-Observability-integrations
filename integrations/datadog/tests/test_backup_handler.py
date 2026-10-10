"""Unit tests for the Datadog AWS Backup / RAM Lambda handler (backup_handler.py).

Tests cover:
- Backup Job FAILED → classification "failed"
- Backup Job COMPLETED + statusMessage → "completed_with_issues"
- RAM CloudTrail revocation → "revoked"
- Malformed event → 400
- Datadog API retry logic (429, 500)
- API key retrieval from Secrets Manager (JSON, plain, caching)

Fixtures use documentation placeholder account IDs / ARNs (123456789012).
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add lambda directory to path (vendor conftest also wires shared/python).
sys.path.insert(0, str(Path(__file__).parent.parent / "lambda"))

_ACCOUNT = "123456789012"
_REGION = "ap-northeast-1"


@pytest.fixture(autouse=True)
def backup_env_vars(monkeypatch):
    """Set required environment variables for backup handler tests."""
    monkeypatch.setenv("DATADOG_SITE", "datadoghq.com")
    monkeypatch.setenv(
        "API_KEY_SECRET_ARN",
        "arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:dd-api-key",
    )
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("DD_ENV", "test")
    monkeypatch.setenv("ENABLE_GZIP", "false")


@pytest.fixture
def reset_backup_handler():
    """Reset module-level cache and reimport backup_handler for isolation."""
    if "backup_handler" in sys.modules:
        del sys.modules["backup_handler"]
    import backup_handler
    backup_handler._api_key_cache = None
    return backup_handler


def _envelope(detail_type, source, detail, **extra):
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


@pytest.fixture
def backup_job_failed():
    return _envelope(
        "Backup Job State Change",
        "aws.backup",
        {
            "state": "FAILED",
            "resourceType": "FSx",
            "backupVaultName": "fsxn-lag-vault",
            "backupJobId": "job-11112222",
        },
    )


@pytest.fixture
def backup_completed_with_issues():
    return _envelope(
        "Backup Job State Change",
        "aws.backup",
        {
            "state": "COMPLETED",
            "statusMessage": "An AWS managed-key file system was not copied",
            "resourceType": "FSx",
            "backupVaultName": "fsxn-lag-vault",
            "backupJobId": "job-11112222",
        },
    )


@pytest.fixture
def ram_revocation():
    return _envelope(
        "AWS API Call via CloudTrail",
        "aws.ram",
        {
            "eventSource": "ram.amazonaws.com",
            "eventName": "DisassociateResourceShare",
        },
    )


class TestFormatForDatadog:
    def test_backup_job_failed_classification(
        self, reset_backup_handler, backup_job_failed
    ):
        handler = reset_backup_handler
        from aws_backup_event import normalize_backup_event

        record = normalize_backup_event(backup_job_failed)
        dd_logs = handler._format_for_datadog([record])

        assert len(dd_logs) == 1
        dd_log = dd_logs[0]
        assert dd_log["ddsource"] == "fsxn-backup"
        assert dd_log["service"] == "fsxn-ontap"
        assert "source:fsxn-backup" in dd_log["ddtags"]
        assert "env:test" in dd_log["ddtags"]
        assert dd_log["attributes"]["classification"] == "failed"
        assert dd_log["attributes"]["event_name"] == "Backup Job State Change"
        assert dd_log["attributes"]["backup_vault_name"] == "fsxn-lag-vault"

    def test_completed_with_issues_classification(
        self, reset_backup_handler, backup_completed_with_issues
    ):
        handler = reset_backup_handler
        from aws_backup_event import normalize_backup_event

        record = normalize_backup_event(backup_completed_with_issues)
        dd_log = handler._format_for_datadog([record])[0]
        assert dd_log["attributes"]["classification"] == "completed_with_issues"
        assert "not copied" in dd_log["message"]

    def test_ram_revocation_classification(
        self, reset_backup_handler, ram_revocation
    ):
        handler = reset_backup_handler
        from aws_backup_event import normalize_backup_event

        record = normalize_backup_event(ram_revocation)
        dd_log = handler._format_for_datadog([record])[0]
        assert dd_log["attributes"]["classification"] == "revoked"
        assert dd_log["attributes"]["source"] == "aws.ram"


class TestLambdaHandler:
    def test_backup_job_failed_ships(
        self, reset_backup_handler, backup_job_failed
    ):
        handler = reset_backup_handler
        mock_response = MagicMock()
        mock_response.status = 202

        with patch.object(handler.secrets_client, "get_secret_value") as mock_secrets:
            mock_secrets.return_value = {
                "SecretString": json.dumps({"api_key": "test-key"})
            }
            with patch.object(handler.http, "request", return_value=mock_response):
                result = handler.lambda_handler(backup_job_failed, None)

        assert result["statusCode"] == 200
        assert result["classification"] == "failed"
        assert result["shipped"] == 1

    def test_malformed_event_returns_400(self, reset_backup_handler):
        handler = reset_backup_handler
        result = handler.lambda_handler({"source": "aws.backup"}, None)
        assert result["statusCode"] == 400
        assert "error" in result

    def test_api_key_retrieval_failure_returns_500(
        self, reset_backup_handler, backup_job_failed
    ):
        handler = reset_backup_handler
        with patch.object(handler.secrets_client, "get_secret_value") as mock_secrets:
            mock_secrets.side_effect = Exception("Access denied")
            result = handler.lambda_handler(backup_job_failed, None)
        assert result["statusCode"] == 500
        assert "API key" in result["error"]


class TestGetApiKey:
    def test_json_format_api_key(self, reset_backup_handler):
        handler = reset_backup_handler
        with patch.object(handler.secrets_client, "get_secret_value") as mock_get:
            mock_get.return_value = {
                "SecretString": json.dumps({"api_key": "dd-test-key-abc123"})
            }
            assert handler.get_api_key() == "dd-test-key-abc123"

    def test_plain_string_format(self, reset_backup_handler):
        handler = reset_backup_handler
        with patch.object(handler.secrets_client, "get_secret_value") as mock_get:
            mock_get.return_value = {"SecretString": "plain-api-key-12345"}
            assert handler.get_api_key() == "plain-api-key-12345"

    def test_api_key_caching(self, reset_backup_handler):
        handler = reset_backup_handler
        with patch.object(handler.secrets_client, "get_secret_value") as mock_get:
            mock_get.return_value = {
                "SecretString": json.dumps({"api_key": "cached-key"})
            }
            handler.get_api_key()
            handler.get_api_key()
            mock_get.assert_called_once()


class TestSendBatchRetry:
    def test_retry_on_429_rate_limit(self, reset_backup_handler):
        handler = reset_backup_handler
        mock_rate_limit = MagicMock()
        mock_rate_limit.status = 429
        mock_rate_limit.headers = {"Retry-After": "1"}

        mock_success = MagicMock()
        mock_success.status = 202

        with patch.object(
            handler.http, "request", side_effect=[mock_rate_limit, mock_success]
        ):
            with patch("backup_handler.time.sleep") as mock_sleep:
                result = handler._send_batch([{"message": "test"}], "key")

        assert result is True
        mock_sleep.assert_called_once_with(1)

    def test_retry_on_500_server_error(self, reset_backup_handler):
        handler = reset_backup_handler
        mock_error = MagicMock()
        mock_error.status = 500
        mock_error.data = b"Internal Server Error"

        mock_success = MagicMock()
        mock_success.status = 202

        with patch.object(
            handler.http, "request", side_effect=[mock_error, mock_success]
        ):
            with patch("backup_handler.time.sleep") as mock_sleep:
                result = handler._send_batch([{"message": "test"}], "key")

        assert result is True
        mock_sleep.assert_called_once_with(2)

    def test_no_retry_on_client_error(self, reset_backup_handler):
        handler = reset_backup_handler
        mock_error = MagicMock()
        mock_error.status = 403
        mock_error.data = b"Forbidden"

        with patch.object(
            handler.http, "request", return_value=mock_error
        ) as mock_request:
            result = handler._send_batch([{"message": "test"}], "key")

        assert result is False
        mock_request.assert_called_once()
