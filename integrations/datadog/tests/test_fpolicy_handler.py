"""Unit tests for FPolicy Lambda handler (fpolicy_handler.py).

Tests cover:
- Valid EventBridge event with file create operation → parsed, formatted, shipped
- Valid EventBridge event with file delete operation → correct attributes
- Missing event detail → returns 400 error
- Multiple operations (create, write, rename, delete) → correct formatting
- Datadog format includes correct tags (source:fsxn-fpolicy, service:fsxn-ontap)
- Structured attributes contain operation, file_path, user, client_ip
- API key retrieval from Secrets Manager (mock)
- Datadog API returns 429 → retry with backoff
- Successful shipping → returns 200
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add lambda directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "lambda"))


@pytest.fixture(autouse=True)
def fpolicy_env_vars(monkeypatch):
    """Set required environment variables for FPolicy handler tests."""
    monkeypatch.setenv("DATADOG_SITE", "datadoghq.com")
    monkeypatch.setenv("API_KEY_SECRET_ARN", "arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:dd-api-key")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("DD_ENV", "test")
    monkeypatch.setenv("ENABLE_GZIP", "false")


@pytest.fixture
def reset_fpolicy_handler():
    """Reset module-level cache and reimport fpolicy_handler for test isolation."""
    if "fpolicy_handler" in sys.modules:
        del sys.modules["fpolicy_handler"]
    import fpolicy_handler
    fpolicy_handler._api_key_cache = None
    return fpolicy_handler


@pytest.fixture
def sample_fpolicy_create_event():
    """Sample EventBridge event with file create operation."""
    return {
        "source": "fpolicy.fsxn",
        "detail-type": "FPolicy File Operation",
        "detail": {
            "operation": "create",
            "file_path": "/vol/data/test.txt",
            "user": "admin@corp.local",
            "client_ip": "10.0.1.50",
            "vserver": "FPolicySMB",
            "timestamp": "2026-05-16T12:00:00Z",
            "protocol": "cifs",
        },
    }


@pytest.fixture
def sample_fpolicy_delete_event():
    """Sample EventBridge event with file delete operation."""
    return {
        "source": "fpolicy.fsxn",
        "detail-type": "FPolicy File Operation",
        "detail": {
            "operation": "delete",
            "file_path": "/vol/data/old-report.xlsx",
            "user": "user1@corp.local",
            "client_ip": "10.0.1.51",
            "vserver": "FPolicySMB",
            "timestamp": "2026-05-16T12:05:00Z",
            "protocol": "cifs",
        },
    }


@pytest.fixture
def sample_fpolicy_write_event():
    """Sample EventBridge event with file write operation."""
    return {
        "source": "fpolicy.fsxn",
        "detail-type": "FPolicy File Operation",
        "detail": {
            "operation": "write",
            "file_path": "/vol/data/shared/document.docx",
            "user": "editor@corp.local",
            "client_ip": "10.0.1.52",
            "vserver": "FPolicySMB",
            "timestamp": "2026-05-16T12:10:00Z",
            "protocol": "cifs",
        },
    }


@pytest.fixture
def sample_fpolicy_rename_event():
    """Sample EventBridge event with file rename operation."""
    return {
        "source": "fpolicy.fsxn",
        "detail-type": "FPolicy File Operation",
        "detail": {
            "operation": "rename",
            "file_path": "/vol/data/renamed-file.txt",
            "user": "admin@corp.local",
            "client_ip": "10.0.1.50",
            "vserver": "FPolicySMB",
            "timestamp": "2026-05-16T12:15:00Z",
            "protocol": "cifs",
        },
    }


class TestExtractFpolicyEvents:
    """Tests for _extract_fpolicy_events function."""

    def test_valid_event_extracts_detail(self, reset_fpolicy_handler, sample_fpolicy_create_event):
        handler = reset_fpolicy_handler
        result = handler._extract_fpolicy_events(sample_fpolicy_create_event)
        assert len(result) == 1
        assert result[0]["operation"] == "create"
        assert result[0]["file_path"] == "/vol/data/test.txt"

    def test_delete_event_extracts_detail(self, reset_fpolicy_handler, sample_fpolicy_delete_event):
        handler = reset_fpolicy_handler
        result = handler._extract_fpolicy_events(sample_fpolicy_delete_event)
        assert len(result) == 1
        assert result[0]["operation"] == "delete"
        assert result[0]["file_path"] == "/vol/data/old-report.xlsx"

    def test_missing_detail_raises_value_error(self, reset_fpolicy_handler):
        handler = reset_fpolicy_handler
        event = {"source": "fpolicy.fsxn", "detail-type": "FPolicy File Operation"}
        with pytest.raises(ValueError, match="detail is missing"):
            handler._extract_fpolicy_events(event)

    def test_none_detail_raises_value_error(self, reset_fpolicy_handler):
        handler = reset_fpolicy_handler
        event = {"source": "fpolicy.fsxn", "detail": None}
        with pytest.raises(ValueError, match="detail is missing"):
            handler._extract_fpolicy_events(event)

    def test_non_dict_detail_raises_value_error(self, reset_fpolicy_handler):
        handler = reset_fpolicy_handler
        event = {"source": "fpolicy.fsxn", "detail": "not a dict"}
        with pytest.raises(ValueError, match="Unexpected detail type"):
            handler._extract_fpolicy_events(event)


class TestFormatForDatadog:
    """Tests for _format_for_datadog function."""

    def test_create_event_formatting(self, reset_fpolicy_handler, sample_fpolicy_create_event):
        handler = reset_fpolicy_handler
        events = [sample_fpolicy_create_event["detail"]]
        result = handler._format_for_datadog(events)

        assert len(result) == 1
        dd_log = result[0]
        assert dd_log["ddsource"] == "fsxn-fpolicy"
        assert dd_log["service"] == "fsxn-ontap"
        assert dd_log["hostname"] == "FPolicySMB"
        assert dd_log["date"] == "2026-05-16T12:00:00Z"
        assert "FPolicy: create /vol/data/test.txt" in dd_log["message"]

    def test_delete_event_attributes(self, reset_fpolicy_handler, sample_fpolicy_delete_event):
        """Delete event should have correct structured attributes."""
        handler = reset_fpolicy_handler
        events = [sample_fpolicy_delete_event["detail"]]
        result = handler._format_for_datadog(events)

        assert len(result) == 1
        dd_log = result[0]
        attrs = dd_log["attributes"]
        assert attrs["operation_type"] == "delete"
        assert attrs["file_path"] == "/vol/data/old-report.xlsx"
        assert attrs["user"] == "user1@corp.local"
        assert attrs["client_ip"] == "10.0.1.51"
        assert attrs["svm"] == "FPolicySMB"
        assert attrs["protocol"] == "cifs"

    def test_correct_tags(self, reset_fpolicy_handler, sample_fpolicy_create_event):
        """Datadog format includes correct tags (source:fsxn-fpolicy, service:fsxn-ontap)."""
        handler = reset_fpolicy_handler
        events = [sample_fpolicy_create_event["detail"]]
        result = handler._format_for_datadog(events)

        dd_log = result[0]
        assert "source:fsxn-fpolicy" in dd_log["ddtags"]
        assert "service:fsxn-ontap" in dd_log["ddtags"]
        assert "env:test" in dd_log["ddtags"]

    def test_structured_attributes_present(self, reset_fpolicy_handler, sample_fpolicy_create_event):
        """Structured attributes contain operation, file_path, user, client_ip."""
        handler = reset_fpolicy_handler
        events = [sample_fpolicy_create_event["detail"]]
        result = handler._format_for_datadog(events)

        attrs = result[0]["attributes"]
        assert "operation_type" in attrs
        assert "file_path" in attrs
        assert "user" in attrs
        assert "client_ip" in attrs
        assert attrs["operation_type"] == "create"
        assert attrs["file_path"] == "/vol/data/test.txt"
        assert attrs["user"] == "admin@corp.local"
        assert attrs["client_ip"] == "10.0.1.50"

    def test_multiple_operations_formatting(
        self,
        reset_fpolicy_handler,
        sample_fpolicy_create_event,
        sample_fpolicy_write_event,
        sample_fpolicy_rename_event,
        sample_fpolicy_delete_event,
    ):
        """Multiple operations (create, write, rename, delete) → correct formatting."""
        handler = reset_fpolicy_handler
        events = [
            sample_fpolicy_create_event["detail"],
            sample_fpolicy_write_event["detail"],
            sample_fpolicy_rename_event["detail"],
            sample_fpolicy_delete_event["detail"],
        ]
        result = handler._format_for_datadog(events)

        assert len(result) == 4
        assert result[0]["attributes"]["operation_type"] == "create"
        assert result[1]["attributes"]["operation_type"] == "write"
        assert result[2]["attributes"]["operation_type"] == "rename"
        assert result[3]["attributes"]["operation_type"] == "delete"

        # All should have correct source and service
        for dd_log in result:
            assert dd_log["ddsource"] == "fsxn-fpolicy"
            assert dd_log["service"] == "fsxn-ontap"
            assert "source:fsxn-fpolicy" in dd_log["ddtags"]
            assert "service:fsxn-ontap" in dd_log["ddtags"]

    def test_empty_events_list(self, reset_fpolicy_handler):
        handler = reset_fpolicy_handler
        result = handler._format_for_datadog([])
        assert len(result) == 0

    def test_event_without_file_path_uses_json_dump(self, reset_fpolicy_handler):
        """When file_path or user is empty, message should use JSON dump."""
        handler = reset_fpolicy_handler
        event = {"operation": "create", "vserver": "svm1"}
        result = handler._format_for_datadog([event])
        dd_log = result[0]
        # Should fall back to JSON dump since file_path and user are empty
        assert dd_log["message"]  # Should not be empty


class TestGetApiKey:
    """Tests for API key retrieval from Secrets Manager."""

    def test_json_format_api_key(self, reset_fpolicy_handler):
        handler = reset_fpolicy_handler
        with patch.object(handler.secrets_client, "get_secret_value") as mock_get:
            mock_get.return_value = {
                "SecretString": json.dumps({"api_key": "dd-test-key-abc123"})
            }
            key = handler.get_api_key()
            assert key == "dd-test-key-abc123"

    def test_json_format_dd_api_key(self, reset_fpolicy_handler):
        handler = reset_fpolicy_handler
        with patch.object(handler.secrets_client, "get_secret_value") as mock_get:
            mock_get.return_value = {
                "SecretString": json.dumps({"DD_API_KEY": "dd-test-key-xyz789"})
            }
            key = handler.get_api_key()
            assert key == "dd-test-key-xyz789"

    def test_plain_string_format(self, reset_fpolicy_handler):
        handler = reset_fpolicy_handler
        with patch.object(handler.secrets_client, "get_secret_value") as mock_get:
            mock_get.return_value = {
                "SecretString": "plain-api-key-12345"
            }
            key = handler.get_api_key()
            assert key == "plain-api-key-12345"

    def test_api_key_caching(self, reset_fpolicy_handler):
        handler = reset_fpolicy_handler
        with patch.object(handler.secrets_client, "get_secret_value") as mock_get:
            mock_get.return_value = {
                "SecretString": json.dumps({"api_key": "cached-key"})
            }
            handler.get_api_key()
            handler.get_api_key()
            # Should only call Secrets Manager once due to caching
            mock_get.assert_called_once()


class TestSendBatchRetry:
    """Tests for _send_batch retry logic."""

    def test_retry_on_429_rate_limit(self, reset_fpolicy_handler):
        handler = reset_fpolicy_handler
        mock_rate_limit = MagicMock()
        mock_rate_limit.status = 429
        mock_rate_limit.headers = {"Retry-After": "1"}

        mock_success = MagicMock()
        mock_success.status = 202

        with patch.object(handler.http, "request", side_effect=[mock_rate_limit, mock_success]):
            with patch("fpolicy_handler.time.sleep") as mock_sleep:
                result = handler._send_batch([{"message": "test"}], "key")

        assert result is True
        mock_sleep.assert_called_once_with(1)

    def test_retry_on_500_server_error(self, reset_fpolicy_handler):
        handler = reset_fpolicy_handler
        mock_error = MagicMock()
        mock_error.status = 500
        mock_error.data = b"Internal Server Error"

        mock_success = MagicMock()
        mock_success.status = 202

        with patch.object(handler.http, "request", side_effect=[mock_error, mock_success]):
            with patch("fpolicy_handler.time.sleep") as mock_sleep:
                result = handler._send_batch([{"message": "test"}], "key")

        assert result is True
        # Exponential backoff: 2^(0+1) = 2 seconds
        mock_sleep.assert_called_once_with(2)

    def test_max_retries_exhausted(self, reset_fpolicy_handler):
        handler = reset_fpolicy_handler
        mock_error = MagicMock()
        mock_error.status = 500
        mock_error.data = b"Error"

        with patch.object(handler.http, "request", return_value=mock_error):
            with patch("fpolicy_handler.time.sleep"):
                result = handler._send_batch([{"message": "test"}], "key")

        assert result is False

    def test_no_retry_on_client_error(self, reset_fpolicy_handler):
        handler = reset_fpolicy_handler
        mock_error = MagicMock()
        mock_error.status = 403
        mock_error.data = b"Forbidden"

        with patch.object(handler.http, "request", return_value=mock_error) as mock_request:
            result = handler._send_batch([{"message": "test"}], "key")

        assert result is False
        mock_request.assert_called_once()

    def test_successful_send(self, reset_fpolicy_handler):
        handler = reset_fpolicy_handler
        mock_response = MagicMock()
        mock_response.status = 202

        with patch.object(handler.http, "request", return_value=mock_response):
            result = handler._send_batch([{"message": "test"}], "key")

        assert result is True


class TestLambdaHandler:
    """Integration tests for the full FPolicy Lambda handler."""

    def test_valid_create_event_returns_200(self, reset_fpolicy_handler, sample_fpolicy_create_event):
        """Valid EventBridge event with file create → parsed, formatted, shipped → 200."""
        handler = reset_fpolicy_handler
        mock_response = MagicMock()
        mock_response.status = 202

        with patch.object(handler.secrets_client, "get_secret_value") as mock_secrets:
            mock_secrets.return_value = {"SecretString": json.dumps({"api_key": "test-key"})}
            with patch.object(handler.http, "request", return_value=mock_response):
                result = handler.lambda_handler(sample_fpolicy_create_event, None)

        assert result["statusCode"] == 200
        body = result["body"]
        assert body["total_events"] == 1
        assert body["shipped"] == 1

    def test_valid_delete_event_returns_200(self, reset_fpolicy_handler, sample_fpolicy_delete_event):
        """Valid EventBridge event with file delete → correct attributes → 200."""
        handler = reset_fpolicy_handler
        mock_response = MagicMock()
        mock_response.status = 202

        with patch.object(handler.secrets_client, "get_secret_value") as mock_secrets:
            mock_secrets.return_value = {"SecretString": json.dumps({"api_key": "test-key"})}
            with patch.object(handler.http, "request", return_value=mock_response):
                result = handler.lambda_handler(sample_fpolicy_delete_event, None)

        assert result["statusCode"] == 200
        body = result["body"]
        assert body["total_events"] == 1
        assert body["shipped"] == 1

    def test_missing_detail_returns_400(self, reset_fpolicy_handler):
        """Missing event detail → returns 400 error."""
        handler = reset_fpolicy_handler
        event = {"source": "fpolicy.fsxn", "detail-type": "FPolicy File Operation"}
        result = handler.lambda_handler(event, None)

        assert result["statusCode"] == 400
        body = result["body"]
        assert "error" in body

    def test_none_detail_returns_400(self, reset_fpolicy_handler):
        handler = reset_fpolicy_handler
        event = {"source": "fpolicy.fsxn", "detail": None}
        result = handler.lambda_handler(event, None)

        assert result["statusCode"] == 400
        body = result["body"]
        assert "error" in body

    def test_api_key_retrieval_failure_propagates(self, reset_fpolicy_handler, sample_fpolicy_create_event):
        """A Secrets Manager failure must fail the invocation, not be swallowed.

        Returning an error dict would mark the invocation successful, so
        EventBridge would not retry and the FPolicy event would be lost.
        """
        handler = reset_fpolicy_handler
        with patch.object(handler.secrets_client, "get_secret_value") as mock_secrets:
            mock_secrets.side_effect = Exception("Access denied")
            with pytest.raises(Exception, match="Access denied"):
                handler.lambda_handler(sample_fpolicy_create_event, None)

    def test_shipping_failure_returns_207(self, reset_fpolicy_handler, sample_fpolicy_create_event):
        """When shipping fails, handler returns 207."""
        handler = reset_fpolicy_handler

        with patch.object(handler.secrets_client, "get_secret_value") as mock_secrets:
            mock_secrets.return_value = {"SecretString": json.dumps({"api_key": "test-key"})}
            with patch.object(handler, "_ship_to_datadog", return_value=0):
                result = handler.lambda_handler(sample_fpolicy_create_event, None)

        assert result["statusCode"] == 207


class TestSqsEventExtraction:
    """Tests for SQS event format support (added after E2E verification)."""

    def test_sqs_event_extracts_body(self, reset_fpolicy_handler):
        """SQS event with FPolicy JSON in body → extracted correctly."""
        handler = reset_fpolicy_handler
        sqs_event = {
            "Records": [
                {
                    "eventSource": "aws:sqs",
                    "body": json.dumps({
                        "operation_type": "create",
                        "file_path": "e2e_test.txt",
                        "client_ip": "10.0.11.107",
                        "volume_name": "vol1",
                        "timestamp": "2026-05-17T14:35:52Z",
                    }),
                }
            ]
        }
        result = handler._extract_fpolicy_events(sqs_event)
        assert len(result) == 1
        assert result[0]["file_path"] == "e2e_test.txt"
        assert result[0]["operation_type"] == "create"

    def test_sqs_event_multiple_records(self, reset_fpolicy_handler):
        """SQS batch with multiple records → all extracted."""
        handler = reset_fpolicy_handler
        sqs_event = {
            "Records": [
                {
                    "eventSource": "aws:sqs",
                    "body": json.dumps({"file_path": "file1.txt", "operation_type": "create"}),
                },
                {
                    "eventSource": "aws:sqs",
                    "body": json.dumps({"file_path": "file2.txt", "operation_type": "delete"}),
                },
            ]
        }
        result = handler._extract_fpolicy_events(sqs_event)
        assert len(result) == 2
        assert result[0]["file_path"] == "file1.txt"
        assert result[1]["file_path"] == "file2.txt"

    def test_sqs_event_invalid_json_body_skipped(self, reset_fpolicy_handler):
        """SQS record with invalid JSON body → skipped, raises if no valid records."""
        handler = reset_fpolicy_handler
        sqs_event = {
            "Records": [
                {
                    "eventSource": "aws:sqs",
                    "body": "not valid json",
                },
            ]
        }
        with pytest.raises(ValueError, match="No valid FPolicy events"):
            handler._extract_fpolicy_events(sqs_event)

    def test_sqs_event_mixed_valid_invalid(self, reset_fpolicy_handler):
        """SQS batch with mix of valid and invalid → valid ones extracted."""
        handler = reset_fpolicy_handler
        sqs_event = {
            "Records": [
                {
                    "eventSource": "aws:sqs",
                    "body": "invalid",
                },
                {
                    "eventSource": "aws:sqs",
                    "body": json.dumps({"file_path": "valid.txt", "operation_type": "create"}),
                },
            ]
        }
        result = handler._extract_fpolicy_events(sqs_event)
        assert len(result) == 1
        assert result[0]["file_path"] == "valid.txt"

    def test_sqs_full_handler_flow(self, reset_fpolicy_handler):
        """Full handler flow with SQS event → empty batchItemFailures."""
        handler = reset_fpolicy_handler
        sqs_event = {
            "Records": [
                {
                    "eventSource": "aws:sqs",
                    "body": json.dumps({
                        "operation_type": "create",
                        "file_path": "e2e_test.txt",
                        "client_ip": "10.0.11.107",
                        "vserver": "FPolicySMB",
                        "timestamp": "2026-05-17T14:35:52Z",
                        "protocol": "cifs",
                        "user": "admin",
                    }),
                }
            ]
        }
        mock_response = MagicMock()
        mock_response.status = 202

        with patch.object(handler.secrets_client, "get_secret_value") as mock_secrets:
            mock_secrets.return_value = {"SecretString": json.dumps({"api_key": "test-key"})}
            with patch.object(handler.http, "request", return_value=mock_response):
                result = handler.lambda_handler(sqs_event, None)

        # ReportBatchItemFailures contract: empty list == whole batch succeeded
        assert result == {"batchItemFailures": []}


def _sqs_record(message_id: str, body: str) -> dict:
    """Build a minimal SQS event source mapping record."""
    return {"eventSource": "aws:sqs", "messageId": message_id, "body": body}


class TestSqsPartialBatchFailure:
    """SQS batches must report per-message failures, never drop events silently.

    Without ReportBatchItemFailures semantics, returning a success response for a
    batch tells SQS to delete every message in it — including ones that were
    never shipped to Datadog. These tests pin the partial-failure contract.
    """

    @staticmethod
    def _valid_body(file_path: str = "report.xlsx") -> str:
        return json.dumps(
            {
                "operation_type": "create",
                "file_path": file_path,
                "vserver": "svm-prod",
                "timestamp": "2026-08-07T09:00:00Z",
                "protocol": "cifs",
                "user": "jdoe",
            }
        )

    def test_unparseable_message_reported_for_dlq_redrive(self, reset_fpolicy_handler):
        """A malformed message is reported as failed so redrive moves it to the DLQ."""
        handler = reset_fpolicy_handler
        sqs_event = {
            "Records": [
                _sqs_record("msg-good", self._valid_body()),
                _sqs_record("msg-bad", "{not valid json"),
            ]
        }
        mock_response = MagicMock()
        mock_response.status = 202

        with patch.object(handler.secrets_client, "get_secret_value") as mock_secrets:
            mock_secrets.return_value = {"SecretString": json.dumps({"api_key": "k"})}
            with patch.object(handler.http, "request", return_value=mock_response):
                result = handler.lambda_handler(sqs_event, None)

        # Only the malformed message is retried; the good one is deleted.
        assert result == {"batchItemFailures": [{"itemIdentifier": "msg-bad"}]}

    def test_non_object_body_reported_as_failure(self, reset_fpolicy_handler):
        """A JSON scalar body is valid JSON but not an FPolicy event."""
        handler = reset_fpolicy_handler
        sqs_event = {"Records": [_sqs_record("msg-scalar", json.dumps("just-a-string"))]}

        with patch.object(handler.secrets_client, "get_secret_value") as mock_secrets:
            mock_secrets.return_value = {"SecretString": json.dumps({"api_key": "k"})}
            result = handler.lambda_handler(sqs_event, None)

        assert result == {"batchItemFailures": [{"itemIdentifier": "msg-scalar"}]}

    def test_delivery_failure_reports_all_parsed_messages(self, reset_fpolicy_handler):
        """If Datadog delivery fails, every parsed message must be retried."""
        handler = reset_fpolicy_handler
        sqs_event = {
            "Records": [
                _sqs_record("msg-1", self._valid_body("a.txt")),
                _sqs_record("msg-2", self._valid_body("b.txt")),
            ]
        }

        with patch.object(handler.secrets_client, "get_secret_value") as mock_secrets:
            mock_secrets.return_value = {"SecretString": json.dumps({"api_key": "k"})}
            with patch.object(
                handler, "_ship_to_datadog", side_effect=RuntimeError("intake 503")
            ):
                result = handler.lambda_handler(sqs_event, None)

        assert result["batchItemFailures"] == [
            {"itemIdentifier": "msg-1"},
            {"itemIdentifier": "msg-2"},
        ]

    def test_secrets_failure_raises_so_whole_batch_is_retried(self, reset_fpolicy_handler):
        """Secrets Manager failure is batch-wide and transient — fail the invocation."""
        handler = reset_fpolicy_handler
        sqs_event = {"Records": [_sqs_record("msg-1", self._valid_body())]}

        with patch.object(handler.secrets_client, "get_secret_value") as mock_secrets:
            mock_secrets.side_effect = Exception("throttled")
            with pytest.raises(Exception, match="throttled"):
                handler.lambda_handler(sqs_event, None)

    def test_all_messages_succeed_returns_empty_failures(self, reset_fpolicy_handler):
        """A fully successful batch reports no failures so all messages are deleted."""
        handler = reset_fpolicy_handler
        sqs_event = {
            "Records": [
                _sqs_record("msg-1", self._valid_body("a.txt")),
                _sqs_record("msg-2", self._valid_body("b.txt")),
            ]
        }
        mock_response = MagicMock()
        mock_response.status = 202

        with patch.object(handler.secrets_client, "get_secret_value") as mock_secrets:
            mock_secrets.return_value = {"SecretString": json.dumps({"api_key": "k"})}
            with patch.object(handler.http, "request", return_value=mock_response):
                result = handler.lambda_handler(sqs_event, None)

        assert result == {"batchItemFailures": []}

    def test_eventbridge_event_still_returns_status_code(
        self, reset_fpolicy_handler, sample_fpolicy_create_event
    ):
        """The EventBridge path keeps its statusCode contract (no SQS Records)."""
        handler = reset_fpolicy_handler
        mock_response = MagicMock()
        mock_response.status = 202

        with patch.object(handler.secrets_client, "get_secret_value") as mock_secrets:
            mock_secrets.return_value = {"SecretString": json.dumps({"api_key": "k"})}
            with patch.object(handler.http, "request", return_value=mock_response):
                result = handler.lambda_handler(sample_fpolicy_create_event, None)

        assert result["statusCode"] == 200
        assert "batchItemFailures" not in result
