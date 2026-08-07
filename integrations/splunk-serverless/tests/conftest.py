"""Pytest configuration and shared fixtures for Splunk Serverless integration tests.

Provides fixtures for:
- Environment variables (SPLUNK_HEC_ENDPOINT, API_KEY_SECRET_ARN, S3_ACCESS_POINT_ARN)
- Sample S3 events conforming to S3 event notification structure
- Sample FSx for ONTAP audit log entries
- Sample EMS event payloads
- Mocked boto3 clients (S3, Secrets Manager)
- Mocked urllib3 PoolManager
"""


import json
import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# Set environment variables BEFORE any handler imports.
# The handler module reads these at module level (os.environ["..."]).
os.environ.setdefault(
    "SPLUNK_HEC_ENDPOINT", "https://splunk.example.com:8088"
)
os.environ.setdefault(
    "API_KEY_SECRET_ARN",
    "arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:splunk/fsxn-hec-token-AbCdEf",
)
os.environ.setdefault(
    "S3_ACCESS_POINT_ARN",
    "arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit-ap",
)
os.environ.setdefault("SPLUNK_INDEX", "fsxn_audit")
os.environ.setdefault("SPLUNK_SOURCETYPE", "fsxn:ontap:audit")
os.environ.setdefault("SPLUNK_SOURCE", "fsxn-observability")
os.environ.setdefault("VERIFY_SSL", "true")
os.environ.setdefault("LOG_LEVEL", "DEBUG")

# Isolate this vendor's handler module: purge any previously cached handler
# so that `import handler` in test files resolves to THIS vendor's lambda/.
_handler_modules = ["handler", "ems_handler", "fpolicy_handler"]
for _m in _handler_modules:
    sys.modules.pop(_m, None)
LAMBDA_DIR = Path(__file__).parent.parent / "lambda"
if str(LAMBDA_DIR) not in sys.path:
    sys.path.insert(0, str(LAMBDA_DIR))


@pytest.fixture(autouse=True)
def env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set required environment variables for all tests.

    Configures the Splunk HEC endpoint, Secrets Manager ARN for the HEC token,
    S3 Access Point ARN, and other Lambda configuration values.
    """
    monkeypatch.setenv(
        "SPLUNK_HEC_ENDPOINT", "https://splunk.example.com:8088"
    )
    monkeypatch.setenv(
        "API_KEY_SECRET_ARN",
        "arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:splunk/fsxn-hec-token-AbCdEf",
    )
    monkeypatch.setenv(
        "S3_ACCESS_POINT_ARN",
        "arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit-ap",
    )
    monkeypatch.setenv("SPLUNK_INDEX", "fsxn_audit")
    monkeypatch.setenv("SPLUNK_SOURCETYPE", "fsxn:ontap:audit")
    monkeypatch.setenv("SPLUNK_SOURCE", "fsxn-observability")
    monkeypatch.setenv("VERIFY_SSL", "true")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")


@pytest.fixture(autouse=True)
def reset_hec_token_cache() -> None:
    """Reset the HEC token cache between tests."""
    import handler
    handler._hec_token_cache = None


@pytest.fixture
def sample_s3_event() -> dict[str, Any]:
    """Sample S3 event notification with Records array.

    Conforms to the S3 event notification structure containing
    s3.bucket.name and s3.object.key fields.
    """
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
                        "key": "audit/svm-prod-01/2026/01/15/audit_log_001.json",
                        "size": 2048,
                    },
                },
            }
        ]
    }


@pytest.fixture
def sample_multi_record_s3_event() -> dict[str, Any]:
    """Sample S3 event with multiple records for batch processing tests."""
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
                        "key": "audit/svm-prod-01/2026/01/15/audit_log_001.json",
                        "size": 2048,
                    },
                },
            },
            {
                "eventVersion": "2.1",
                "eventSource": "aws:s3",
                "awsRegion": "ap-northeast-1",
                "eventTime": "2026-01-15T12:01:00.000Z",
                "eventName": "ObjectCreated:Put",
                "s3": {
                    "bucket": {
                        "name": "fsxn-audit-logs-bucket",
                        "arn": "arn:aws:s3:::fsxn-audit-logs-bucket",
                    },
                    "object": {
                        "key": "audit/svm-prod-01/2026/01/15/audit_log_002.json",
                        "size": 1024,
                    },
                },
            },
        ]
    }


@pytest.fixture
def sample_audit_logs() -> str:
    """Sample FSx for ONTAP audit logs as newline-delimited JSON.

    Contains representative audit log entries with EventID, SVMName,
    UserName, Operation, ObjectName, Result, and timestamp fields.
    """
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
def sample_audit_logs_bytes(sample_audit_logs: str) -> bytes:
    """Sample audit logs as bytes (as read from S3)."""
    return sample_audit_logs.encode("utf-8")


@pytest.fixture
def sample_ems_event() -> dict[str, Any]:
    """Sample ONTAP EMS event payload for ARP ransomware detection.

    Contains the required fields: message-name, message-severity,
    message-timestamp, and parameters.
    """
    return {
        "message-name": "arw.volume.state",
        "message-severity": "alert",
        "message-timestamp": "2026-01-15T12:05:00+09:00",
        "parameters": {
            "volume-name": "vol_data_01",
            "vserver-name": "svm-prod-01",
            "state": "attack-detected",
            "attack-probability": "high",
            "suspect-files-count": "42",
        },
    }


@pytest.fixture
def sample_ems_api_gateway_event(sample_ems_event: dict[str, Any]) -> dict[str, Any]:
    """Sample API Gateway HTTP API event wrapping an EMS payload.

    Includes x-api-key header for authentication.
    """
    return {
        "version": "2.0",
        "routeKey": "POST /ems",
        "rawPath": "/ems",
        "headers": {
            "content-type": "application/json",
            "x-api-key": "test-valid-api-key-12345",
        },
        "body": json.dumps(sample_ems_event),
        "isBase64Encoded": False,
        "requestContext": {
            "http": {
                "method": "POST",
                "path": "/ems",
            },
            "requestId": "req-12345",
        },
    }


@pytest.fixture
def valid_hec_token() -> str:
    """Valid Splunk HEC token in UUID format (8-4-4-4-12 hex)."""
    return "a1b2c3d4-e5f6-7890-abcd-ef1234567890"


@pytest.fixture
def mock_boto3_clients(valid_hec_token: str) -> dict[str, MagicMock]:
    """Mock boto3 clients for S3 and Secrets Manager.

    Provides pre-configured mocks that return valid responses:
    - Secrets Manager returns a valid HEC token
    - S3 returns sample audit log data
    """
    with (
        patch("handler.s3_client") as mock_s3,
        patch("handler.secrets_client") as mock_secrets,
    ):
        # Configure Secrets Manager mock to return valid HEC token
        mock_secrets.get_secret_value.return_value = {
            "SecretString": valid_hec_token,
        }

        # Configure S3 mock with a readable body
        mock_body = MagicMock()
        mock_body.read.return_value = json.dumps({
            "timestamp": "2026-01-15T12:00:01Z",
            "EventID": "4663",
            "SVMName": "svm-prod-01",
            "UserName": "admin@corp.local",
            "Operation": "ReadData",
            "ObjectName": "/vol/data/file.txt",
            "Result": "Success",
        }).encode("utf-8")
        mock_s3.get_object.return_value = {"Body": mock_body}

        yield {"s3": mock_s3, "secrets": mock_secrets}


@pytest.fixture
def mock_urllib3() -> MagicMock:
    """Mock urllib3 PoolManager for HTTP calls to Splunk HEC.

    Returns a mock that simulates successful HEC responses (HTTP 200).
    """
    with patch("handler.http") as mock_http:
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.data = json.dumps(
            {"text": "Success", "code": 0}
        ).encode("utf-8")
        mock_http.request.return_value = mock_response
        yield mock_http


@pytest.fixture
def mock_urllib3_failure() -> MagicMock:
    """Mock urllib3 PoolManager that simulates HEC failures (HTTP 503)."""
    with patch("handler.http") as mock_http:
        mock_response = MagicMock()
        mock_response.status = 503
        mock_response.data = json.dumps(
            {"text": "Server Error", "code": 9}
        ).encode("utf-8")
        mock_http.request.return_value = mock_response
        yield mock_http


@pytest.fixture
def mock_urllib3_rate_limited() -> MagicMock:
    """Mock urllib3 PoolManager that simulates HEC rate limiting (HTTP 429)."""
    with patch("handler.http") as mock_http:
        mock_response = MagicMock()
        mock_response.status = 429
        mock_response.data = json.dumps(
            {"text": "Rate limit exceeded", "code": 9}
        ).encode("utf-8")
        mock_http.request.return_value = mock_response
        yield mock_http


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
