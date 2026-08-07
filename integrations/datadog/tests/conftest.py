"""Pytest configuration and shared fixtures for Datadog integration tests."""


import sys
from pathlib import Path

# Isolate this vendor's handler module: purge any previously cached handler
# so that `import handler` in test files resolves to THIS vendor's lambda/.
_handler_modules = [
    "handler",
    "ems_handler",
    "fpolicy_handler",
    "snapshot_remediation",
]
for _m in _handler_modules:
    sys.modules.pop(_m, None)
_lambda_dir = str(Path(__file__).parent.parent / "lambda")
if _lambda_dir not in sys.path:
    sys.path.insert(0, _lambda_dir)

import json
import os
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def env_vars(monkeypatch):
    """Set required environment variables for all tests."""
    monkeypatch.setenv("DATADOG_SITE", "datadoghq.com")
    monkeypatch.setenv("API_KEY_SECRET_ARN", "arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:dd-api-key")
    monkeypatch.setenv("S3_ACCESS_POINT_ARN", "arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("ENV", "test")
    # DD_SOURCE / DD_SERVICE are deliberately NOT set here. All three handlers
    # read them at import time with per-handler defaults (fsxn / fsxn-ems /
    # fsxn-fpolicy), and a single shared value would silently override the EMS
    # and FPolicy defaults that the log pipeline and facets filter on.
    # Tests that need a non-default value patch the module attribute directly.


@pytest.fixture
def sample_s3_event():
    """Sample S3 event notification."""
    return {
        "Records": [
            {
                "eventVersion": "2.1",
                "eventSource": "aws:s3",
                "awsRegion": "ap-northeast-1",
                "eventTime": "2026-01-15T12:00:00.000Z",
                "eventName": "ObjectCreated:Put",
                "s3": {
                    "bucket": {
                        "name": "fsxn-audit-logs-bucket",
                        "arn": "arn:aws:s3:::fsxn-audit-logs-bucket",
                    },
                    "object": {
                        "key": "audit/svm1/2026/01/15/audit_log_001.json",
                        "size": 1024,
                    },
                },
            }
        ]
    }


@pytest.fixture
def sample_eventbridge_event():
    """Sample EventBridge event for S3 object creation."""
    return {
        "version": "0",
        "id": "12345678-1234-1234-1234-123456789012",
        "detail-type": "Object Created",
        "source": "aws.s3",
        "account": "123456789012",
        "time": "2026-01-15T12:00:00Z",
        "region": "ap-northeast-1",
        "detail": {
            "bucket": {"name": "fsxn-audit-logs-bucket"},
            "object": {
                "key": "audit/svm1/2026/01/15/audit_log_001.json",
                "size": 1024,
            },
        },
    }


@pytest.fixture
def sample_json_audit_logs():
    """Sample FSx for ONTAP audit logs in JSON format."""
    logs = [
        {
            "timestamp": "2026-01-15T12:00:01Z",
            "EventID": "4663",
            "SVMName": "svm-prod-01",
            "UserName": "admin@corp.local",
            "ClientIP": "10.0.1.50",
            "Operation": "ReadData",
            "ObjectName": "/vol/data/reports/quarterly.xlsx",
            "Result": "Success",
        },
        {
            "timestamp": "2026-01-15T12:00:02Z",
            "EventID": "4663",
            "SVMName": "svm-prod-01",
            "UserName": "user1@corp.local",
            "ClientIP": "10.0.1.51",
            "Operation": "WriteData",
            "ObjectName": "/vol/data/shared/document.docx",
            "Result": "Success",
        },
        {
            "timestamp": "2026-01-15T12:00:03Z",
            "EventID": "4656",
            "SVMName": "svm-prod-01",
            "UserName": "unknown@external.com",
            "ClientIP": "192.168.1.100",
            "Operation": "Open",
            "ObjectName": "/vol/data/confidential/secret.pdf",
            "Result": "Failure",
        },
    ]
    return "\n".join(json.dumps(log) for log in logs)


@pytest.fixture
def mock_boto3_clients():
    """Mock boto3 clients for S3 and Secrets Manager."""
    with patch("handler.s3_client") as mock_s3, \
         patch("handler.secrets_client") as mock_secrets:
        mock_secrets.get_secret_value.return_value = {
            "SecretString": json.dumps({"api_key": "test-dd-api-key-12345"})
        }
        yield {"s3": mock_s3, "secrets": mock_secrets}


# Make the shared ONTAP audit parser importable, mirroring how deploy.sh bundles
# it next to the handler in the Lambda zip. Without this the handler falls back
# to JSON-only parsing and the audit-format tests fail.
# Imports are repeated locally so this block is self-contained regardless of
# where it sits relative to the rest of the file's imports.
import sys as _sys
from pathlib import Path as _Path

_shared_python_dir = str(_Path(__file__).resolve().parents[3] / "shared" / "python")
if _shared_python_dir not in _sys.path:
    _sys.path.insert(0, _shared_python_dir)
