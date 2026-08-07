"""Unit tests for Splunk Serverless log shipper."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import urllib3
from botocore.exceptions import ClientError

sys.path.insert(0, str(Path(__file__).parent.parent / "lambda"))
sys.modules.pop("handler", None)
import handler


class TestGetHecToken:
    """Tests for get_hec_token() with UUID validation and error handling."""

    def test_valid_uuid_token_plain_string(self):
        """Valid UUID token as plain string is accepted and cached."""
        handler._hec_token_cache = None
        valid_token = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        with patch("handler.secrets_client") as mock_secrets:
            mock_secrets.get_secret_value.return_value = {
                "SecretString": valid_token,
            }
            result = handler.get_hec_token()
            assert result == valid_token
            assert handler._hec_token_cache == valid_token

    def test_valid_uuid_token_json_format(self):
        """Valid UUID token in JSON format (hec_token key) is accepted."""
        handler._hec_token_cache = None
        valid_token = "abcdef01-2345-6789-abcd-ef0123456789"
        with patch("handler.secrets_client") as mock_secrets:
            mock_secrets.get_secret_value.return_value = {
                "SecretString": json.dumps({"hec_token": valid_token}),
            }
            result = handler.get_hec_token()
            assert result == valid_token

    def test_valid_uuid_token_json_splunk_key(self):
        """Valid UUID token in JSON format (SPLUNK_HEC_TOKEN key) is accepted."""
        handler._hec_token_cache = None
        valid_token = "12345678-abcd-ef01-2345-6789abcdef01"
        with patch("handler.secrets_client") as mock_secrets:
            mock_secrets.get_secret_value.return_value = {
                "SecretString": json.dumps({"SPLUNK_HEC_TOKEN": valid_token}),
            }
            result = handler.get_hec_token()
            assert result == valid_token

    def test_cached_token_returned_without_api_call(self):
        """Cached token is returned without calling Secrets Manager."""
        handler._hec_token_cache = "cached-tok-abcd-1234-567890abcdef"
        # This is already cached, so it should be returned directly
        # Note: this token doesn't match UUID but it's already cached
        result = handler.get_hec_token()
        assert result == "cached-tok-abcd-1234-567890abcdef"

    def test_empty_token_raises_value_error(self):
        """Empty token raises ValueError."""
        handler._hec_token_cache = None
        with patch("handler.secrets_client") as mock_secrets:
            mock_secrets.get_secret_value.return_value = {
                "SecretString": "",
            }
            with pytest.raises(ValueError, match="HEC token is empty"):
                handler.get_hec_token()

    def test_whitespace_only_token_raises_value_error(self):
        """Whitespace-only token raises ValueError."""
        handler._hec_token_cache = None
        with patch("handler.secrets_client") as mock_secrets:
            mock_secrets.get_secret_value.return_value = {
                "SecretString": "   \t\n  ",
            }
            with pytest.raises(ValueError, match="HEC token is empty"):
                handler.get_hec_token()

    def test_invalid_format_raises_value_error(self):
        """Token not matching UUID format raises ValueError."""
        handler._hec_token_cache = None
        with patch("handler.secrets_client") as mock_secrets:
            mock_secrets.get_secret_value.return_value = {
                "SecretString": "not-a-valid-uuid-token",
            }
            with pytest.raises(ValueError, match="Invalid HEC token format"):
                handler.get_hec_token()

    def test_partial_uuid_raises_value_error(self):
        """Partial UUID (missing segment) raises ValueError."""
        handler._hec_token_cache = None
        with patch("handler.secrets_client") as mock_secrets:
            mock_secrets.get_secret_value.return_value = {
                "SecretString": "a1b2c3d4-e5f6-7890-abcd",
            }
            with pytest.raises(ValueError, match="Invalid HEC token format"):
                handler.get_hec_token()

    def test_resource_not_found_exception(self):
        """ResourceNotFoundException is logged with ARN and re-raised."""
        handler._hec_token_cache = None
        error_response = {
            "Error": {"Code": "ResourceNotFoundException", "Message": "Secret not found"}
        }
        with patch("handler.secrets_client") as mock_secrets:
            mock_secrets.get_secret_value.side_effect = ClientError(
                error_response, "GetSecretValue"
            )
            with pytest.raises(ClientError):
                handler.get_hec_token()

    def test_access_denied_exception(self):
        """AccessDeniedException is logged with ARN and re-raised."""
        handler._hec_token_cache = None
        error_response = {
            "Error": {"Code": "AccessDeniedException", "Message": "Access denied"}
        }
        with patch("handler.secrets_client") as mock_secrets:
            mock_secrets.get_secret_value.side_effect = ClientError(
                error_response, "GetSecretValue"
            )
            with pytest.raises(ClientError):
                handler.get_hec_token()

    def test_token_with_leading_trailing_whitespace_is_stripped(self):
        """Token with whitespace is stripped before validation."""
        handler._hec_token_cache = None
        valid_token = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        with patch("handler.secrets_client") as mock_secrets:
            mock_secrets.get_secret_value.return_value = {
                "SecretString": f"  {valid_token}  ",
            }
            result = handler.get_hec_token()
            assert result == valid_token


class TestFormatForSplunk:
    def test_basic_formatting(self):
        logs = [{"EventID": "4663", "SVMName": "svm-01", "UserName": "admin",
                 "Operation": "ReadData", "timestamp": "2026-01-15T12:00:00Z"}]
        result = handler._format_for_splunk(logs, "audit/test.json")
        assert len(result) == 1
        assert result[0]["sourcetype"] == "fsxn:ontap:audit"
        assert result[0]["index"] == "fsxn_audit"
        assert result[0]["host"] == "svm-01"
        assert result[0]["event"]["event_type"] == "4663"
        assert result[0]["time"] is not None

    def test_missing_timestamp(self):
        logs = [{"EventID": "4663", "SVMName": "svm-01"}]
        result = handler._format_for_splunk(logs, "test.json")
        assert "time" not in result[0]

    def test_all_event_fields_present(self):
        """Verify all required event sub-object fields are present."""
        logs = [{
            "EventID": "4663",
            "SVMName": "svm-prod-01",
            "UserName": "admin@corp.local",
            "ClientIP": "10.0.1.50",
            "Operation": "ReadData",
            "ObjectName": "/vol/data/file.txt",
            "Result": "Success",
            "timestamp": "2026-01-15T12:00:00Z",
        }]
        result = handler._format_for_splunk(logs, "audit/key.json")
        event = result[0]["event"]
        assert event["event_type"] == "4663"
        assert event["user"] == "admin@corp.local"
        assert event["client_ip"] == "10.0.1.50"
        assert event["operation"] == "ReadData"
        assert event["path"] == "/vol/data/file.txt"
        assert event["result"] == "Success"
        assert event["svm"] == "svm-prod-01"

    def test_hec_json_top_level_fields(self):
        """Verify all required HEC top-level fields are present."""
        logs = [{"SVMName": "svm-01", "timestamp": "2026-01-15T12:00:00Z"}]
        result = handler._format_for_splunk(logs, "key.json")
        hec = result[0]
        assert "source" in hec
        assert "sourcetype" in hec
        assert "index" in hec
        assert "host" in hec
        assert "event" in hec
        assert hec["source"] == "fsxn-observability"

    def test_multiple_logs(self):
        """Verify multiple logs produce multiple HEC events."""
        logs = [
            {"EventID": "4663", "SVMName": "svm-01", "timestamp": "2026-01-15T12:00:00Z"},
            {"EventID": "4656", "SVMName": "svm-02", "timestamp": "2026-01-15T12:01:00Z"},
        ]
        result = handler._format_for_splunk(logs, "key.json")
        assert len(result) == 2
        assert result[0]["host"] == "svm-01"
        assert result[1]["host"] == "svm-02"

    def test_empty_logs_returns_empty_list(self):
        """Verify empty input produces empty output."""
        result = handler._format_for_splunk([], "key.json")
        assert result == []

    def test_fallback_field_names(self):
        """Verify fallback field names (lowercase) are used when primary fields missing."""
        logs = [{
            "event_type": "custom_event",
            "svm": "svm-fallback",
            "user": "fallback_user",
            "client_ip": "192.168.1.1",
            "operation": "Write",
            "path": "/fallback/path",
            "result": "Failure",
            "timestamp": "2026-01-15T12:00:00Z",
        }]
        result = handler._format_for_splunk(logs, "key.json")
        event = result[0]["event"]
        assert event["event_type"] == "custom_event"
        assert event["user"] == "fallback_user"
        assert event["svm"] == "svm-fallback"
        assert result[0]["host"] == "svm-fallback"

    def test_timestamp_converted_to_epoch(self):
        """Verify timestamp is converted to epoch seconds (float)."""
        logs = [{"timestamp": "2026-01-15T12:00:00Z", "SVMName": "svm-01"}]
        result = handler._format_for_splunk(logs, "key.json")
        assert isinstance(result[0]["time"], float)
        assert result[0]["time"] > 0


class TestToEpoch:
    def test_iso_with_z(self):
        result = handler._to_epoch("2026-01-15T12:00:00Z")
        assert result is not None
        assert result > 0

    def test_invalid(self):
        assert handler._to_epoch("not-a-date") is None


class TestSendToHec:
    """Tests for _send_to_hec() exponential backoff retry logic."""

    @patch("handler.http")
    def test_success_first_attempt(self, mock_http):
        """Successful HEC response on first attempt returns True."""
        mock_resp = MagicMock(status=200, data=b'{"text":"Success","code":0}')
        mock_http.request.return_value = mock_resp
        payload = json.dumps({"event": {"msg": "test"}})
        assert handler._send_to_hec(payload, "hec-token") is True

        # Verify Authorization header
        call_args = mock_http.request.call_args
        assert "Splunk hec-token" in call_args[1]["headers"]["Authorization"]

    @patch("handler.http")
    def test_retry_on_429_with_backoff(self, mock_http):
        """HTTP 429 triggers retry with exponential backoff."""
        mock_429 = MagicMock(status=429, data=b'{"text":"Rate limited","code":9}')
        mock_ok = MagicMock(status=200, data=b'{"text":"Success","code":0}')
        mock_http.request.side_effect = [mock_429, mock_ok]
        with patch("handler.time.sleep") as mock_sleep:
            assert handler._send_to_hec('{"event":{}}', "token") is True
            # First retry delay: 2 * 2^0 = 2 seconds
            mock_sleep.assert_called_once_with(2)

    @patch("handler.http")
    def test_retry_on_500_with_backoff(self, mock_http):
        """HTTP 500 triggers retry with exponential backoff."""
        mock_500 = MagicMock(status=500, data=b'{"text":"Internal error","code":9}')
        mock_ok = MagicMock(status=200, data=b'{"text":"Success","code":0}')
        mock_http.request.side_effect = [mock_500, mock_ok]
        with patch("handler.time.sleep") as mock_sleep:
            assert handler._send_to_hec('{"event":{}}', "token") is True
            mock_sleep.assert_called_once_with(2)

    @patch("handler.http")
    def test_retry_on_503_with_backoff(self, mock_http):
        """HTTP 503 triggers retry with exponential backoff."""
        mock_503 = MagicMock(status=503, data=b'{"text":"Server busy","code":9}')
        mock_ok = MagicMock(status=200, data=b'{"text":"Success","code":0}')
        mock_http.request.side_effect = [mock_503, mock_ok]
        with patch("handler.time.sleep") as mock_sleep:
            assert handler._send_to_hec('{"event":{}}', "token") is True
            mock_sleep.assert_called_once_with(2)

    @patch("handler.http")
    def test_exponential_backoff_delays(self, mock_http):
        """Verify backoff delays double: 2s, 4s for 3 total attempts."""
        mock_5xx = MagicMock(status=503, data=b'{"text":"Server busy","code":9}')
        mock_ok = MagicMock(status=200, data=b'{"text":"Success","code":0}')
        # Fail twice, succeed on third attempt
        mock_http.request.side_effect = [mock_5xx, mock_5xx, mock_ok]
        with patch("handler.time.sleep") as mock_sleep:
            assert handler._send_to_hec('{"event":{}}', "token") is True
            # Two sleeps: 2s after attempt 1, 4s after attempt 2
            assert mock_sleep.call_count == 2
            mock_sleep.assert_any_call(2)
            mock_sleep.assert_any_call(4)

    @patch("handler.http")
    def test_max_3_attempts_then_failure(self, mock_http):
        """After 3 failed attempts, returns False without extra sleep."""
        mock_5xx = MagicMock(status=503, data=b'{"text":"Server busy","code":9}')
        mock_http.request.return_value = mock_5xx
        with patch("handler.time.sleep") as mock_sleep:
            assert handler._send_to_hec('{"event":{}}', "token") is False
            # Exactly 3 HTTP requests made
            assert mock_http.request.call_count == 3
            # Only 2 sleeps (between attempts 1→2 and 2→3, not after final)
            assert mock_sleep.call_count == 2

    @patch("handler.http")
    def test_no_retry_on_400(self, mock_http):
        """HTTP 400 (client error) does not trigger retry."""
        mock_400 = MagicMock(status=400, data=b'{"text":"Bad request","code":6}')
        mock_http.request.return_value = mock_400
        with patch("handler.time.sleep") as mock_sleep:
            assert handler._send_to_hec('{"event":{}}', "token") is False
            # Only 1 attempt, no retry
            assert mock_http.request.call_count == 1
            mock_sleep.assert_not_called()

    @patch("handler.http")
    def test_no_retry_on_401(self, mock_http):
        """HTTP 401 (unauthorized) does not trigger retry."""
        mock_401 = MagicMock(status=401, data=b'{"text":"Unauthorized","code":4}')
        mock_http.request.return_value = mock_401
        with patch("handler.time.sleep") as mock_sleep:
            assert handler._send_to_hec('{"event":{}}', "token") is False
            assert mock_http.request.call_count == 1
            mock_sleep.assert_not_called()

    @patch("handler.http")
    def test_no_retry_on_403(self, mock_http):
        """HTTP 403 (forbidden) does not trigger retry."""
        mock_403 = MagicMock(status=403, data=b'{"text":"Forbidden","code":4}')
        mock_http.request.return_value = mock_403
        with patch("handler.time.sleep") as mock_sleep:
            assert handler._send_to_hec('{"event":{}}', "token") is False
            assert mock_http.request.call_count == 1
            mock_sleep.assert_not_called()

    @patch("handler.http")
    def test_http_error_retries_with_backoff(self, mock_http):
        """urllib3 HTTPError triggers retry with exponential backoff."""
        mock_http.request.side_effect = [
            urllib3.exceptions.HTTPError("Connection refused"),
            MagicMock(status=200, data=b'{"text":"Success","code":0}'),
        ]
        with patch("handler.time.sleep") as mock_sleep:
            assert handler._send_to_hec('{"event":{}}', "token") is True
            mock_sleep.assert_called_once_with(2)

    @patch("handler.http")
    def test_http_error_all_attempts_exhausted(self, mock_http):
        """urllib3 HTTPError on all attempts returns False."""
        mock_http.request.side_effect = urllib3.exceptions.HTTPError("Timeout")
        with patch("handler.time.sleep") as mock_sleep:
            assert handler._send_to_hec('{"event":{}}', "token") is False
            assert mock_http.request.call_count == 3
            assert mock_sleep.call_count == 2

    @patch("handler.http")
    def test_content_type_header(self, mock_http):
        """Verify Content-Type header is application/json."""
        mock_http.request.return_value = MagicMock(
            status=200, data=b'{"code":0}'
        )
        handler._send_to_hec('{"event":{}}', "token")
        call_args = mock_http.request.call_args
        assert call_args[1]["headers"]["Content-Type"] == "application/json"

    @patch("handler.http")
    def test_posts_to_correct_endpoint(self, mock_http):
        """Verify POST to {HEC_ENDPOINT}/services/collector/event."""
        mock_http.request.return_value = MagicMock(
            status=200, data=b'{"code":0}'
        )
        handler._send_to_hec('{"event":{}}', "token")
        call_args = mock_http.request.call_args
        assert call_args[0][0] == "POST"
        assert call_args[0][1].endswith("/services/collector/event")


class TestSendBatch:
    @patch("handler.http")
    def test_success(self, mock_http):
        mock_resp = MagicMock(status=200, data=b'{"text":"Success","code":0}')
        mock_http.request.return_value = mock_resp
        batch = [{"event": {"msg": "test"}, "sourcetype": "test"}]
        assert handler._send_batch(batch, "hec-token") is True

        # Verify Authorization header
        call_args = mock_http.request.call_args
        assert "Splunk hec-token" in call_args[1]["headers"]["Authorization"]

    @patch("handler.http")
    def test_retry_on_503(self, mock_http):
        mock_503 = MagicMock(status=503, data=b'{"text":"Server busy","code":9}')
        mock_ok = MagicMock(status=200, data=b'{"text":"Success","code":0}')
        mock_http.request.side_effect = [mock_503, mock_ok]
        with patch("handler.time.sleep"):
            assert handler._send_batch([{"event": {}}], "token") is True

    @patch("handler.http")
    def test_hec_newline_delimited(self, mock_http):
        """Verify HEC payload is newline-delimited JSON, not an array."""
        mock_http.request.return_value = MagicMock(status=200, data=b'{"code":0}')
        batch = [{"event": {"a": 1}}, {"event": {"b": 2}}]
        handler._send_batch(batch, "token")

        body = mock_http.request.call_args[1]["body"].decode()
        lines = body.strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["event"]["a"] == 1


class TestGracefulRecordSkipping:
    """Tests for graceful record skipping when S3 objects are missing or empty."""

    @patch("handler.time.sleep")
    @patch("handler.http")
    @patch("handler.s3_client")
    @patch("handler.secrets_client")
    def test_missing_s3_object_skipped(
        self, mock_secrets, mock_s3, mock_http, mock_sleep
    ):
        """Record with NoSuchKey error is skipped, error logged in response."""
        handler._hec_token_cache = None
        valid_token = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        mock_secrets.get_secret_value.return_value = {
            "SecretString": valid_token,
        }

        # S3 raises NoSuchKey
        error_response = {
            "Error": {"Code": "NoSuchKey", "Message": "The specified key does not exist."}
        }
        mock_s3.get_object.side_effect = ClientError(error_response, "GetObject")

        event = {
            "Records": [
                {
                    "s3": {
                        "bucket": {"name": "test-bucket"},
                        "object": {"key": "audit/missing-file.json"},
                    }
                }
            ]
        }

        result = handler.lambda_handler(event, None)

        assert result["statusCode"] == 207
        assert result["body"]["total_logs"] == 0
        assert result["body"]["total_shipped"] == 0
        assert len(result["body"]["errors"]) == 1
        assert result["body"]["errors"][0]["key"] == "audit/missing-file.json"
        assert "NoSuchKey" in result["body"]["errors"][0]["error"]

    @patch("handler.time.sleep")
    @patch("handler.http")
    @patch("handler.s3_client")
    @patch("handler.secrets_client")
    def test_empty_s3_object_skipped(
        self, mock_secrets, mock_s3, mock_http, mock_sleep
    ):
        """Record with empty S3 object body is skipped, error logged in response."""
        handler._hec_token_cache = None
        valid_token = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        mock_secrets.get_secret_value.return_value = {
            "SecretString": valid_token,
        }

        # S3 returns empty body
        mock_body = MagicMock()
        mock_body.read.return_value = b""
        mock_s3.get_object.return_value = {"Body": mock_body}

        event = {
            "Records": [
                {
                    "s3": {
                        "bucket": {"name": "test-bucket"},
                        "object": {"key": "audit/empty-file.json"},
                    }
                }
            ]
        }

        result = handler.lambda_handler(event, None)

        assert result["statusCode"] == 207
        assert result["body"]["total_logs"] == 0
        assert result["body"]["total_shipped"] == 0
        assert len(result["body"]["errors"]) == 1
        assert result["body"]["errors"][0]["key"] == "audit/empty-file.json"
        assert "empty" in result["body"]["errors"][0]["error"].lower()

    @patch("handler.time.sleep")
    @patch("handler.http")
    @patch("handler.s3_client")
    @patch("handler.secrets_client")
    def test_multi_record_with_one_failure_continues(
        self, mock_secrets, mock_s3, mock_http, mock_sleep
    ):
        """Multi-record event with one failing record continues processing others."""
        handler._hec_token_cache = None
        valid_token = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        mock_secrets.get_secret_value.return_value = {
            "SecretString": valid_token,
        }

        # First record: NoSuchKey error
        # Second record: valid data
        valid_log = json.dumps({
            "timestamp": "2026-01-15T12:00:01Z",
            "EventID": "4663",
            "SVMName": "svm-prod-01",
            "UserName": "admin@corp.local",
            "Operation": "ReadData",
            "ObjectName": "/vol/data/file.txt",
            "Result": "Success",
        }).encode("utf-8")

        error_response = {
            "Error": {"Code": "NoSuchKey", "Message": "The specified key does not exist."}
        }

        mock_body_valid = MagicMock()
        mock_body_valid.read.return_value = valid_log

        # First call raises NoSuchKey, second call returns valid data
        mock_s3.get_object.side_effect = [
            ClientError(error_response, "GetObject"),
            {"Body": mock_body_valid},
        ]

        # HEC returns success
        mock_http.request.return_value = MagicMock(
            status=200, data=b'{"text":"Success","code":0}'
        )

        event = {
            "Records": [
                {
                    "s3": {
                        "bucket": {"name": "test-bucket"},
                        "object": {"key": "audit/missing-file.json"},
                    }
                },
                {
                    "s3": {
                        "bucket": {"name": "test-bucket"},
                        "object": {"key": "audit/valid-file.json"},
                    }
                },
            ]
        }

        result = handler.lambda_handler(event, None)

        # Should be 207 because one record failed
        assert result["statusCode"] == 207
        # Valid record had 1 log entry
        assert result["body"]["total_logs"] == 1
        assert result["body"]["total_shipped"] == 1
        # Only one error (the missing file)
        assert len(result["body"]["errors"]) == 1
        assert result["body"]["errors"][0]["key"] == "audit/missing-file.json"
        assert "NoSuchKey" in result["body"]["errors"][0]["error"]

    @patch("handler.time.sleep")
    @patch("handler.http")
    @patch("handler.s3_client")
    @patch("handler.secrets_client")
    def test_no_such_bucket_skipped(
        self, mock_secrets, mock_s3, mock_http, mock_sleep
    ):
        """Record with NoSuchBucket error is skipped gracefully."""
        handler._hec_token_cache = None
        valid_token = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        mock_secrets.get_secret_value.return_value = {
            "SecretString": valid_token,
        }

        error_response = {
            "Error": {"Code": "NoSuchBucket", "Message": "The specified bucket does not exist."}
        }
        mock_s3.get_object.side_effect = ClientError(error_response, "GetObject")

        event = {
            "Records": [
                {
                    "s3": {
                        "bucket": {"name": "nonexistent-bucket"},
                        "object": {"key": "audit/some-file.json"},
                    }
                }
            ]
        }

        result = handler.lambda_handler(event, None)

        assert result["statusCode"] == 207
        assert result["body"]["total_logs"] == 0
        assert result["body"]["total_shipped"] == 0
        assert len(result["body"]["errors"]) == 1
        assert result["body"]["errors"][0]["key"] == "audit/some-file.json"
        assert "NoSuchBucket" in result["body"]["errors"][0]["error"]

    @patch("handler.time.sleep")
    @patch("handler.http")
    @patch("handler.s3_client")
    @patch("handler.secrets_client")
    def test_all_records_success_returns_200(
        self, mock_secrets, mock_s3, mock_http, mock_sleep
    ):
        """All records processed successfully returns statusCode 200."""
        handler._hec_token_cache = None
        valid_token = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        mock_secrets.get_secret_value.return_value = {
            "SecretString": valid_token,
        }

        valid_log = json.dumps({
            "timestamp": "2026-01-15T12:00:01Z",
            "EventID": "4663",
            "SVMName": "svm-prod-01",
            "UserName": "admin@corp.local",
            "Operation": "ReadData",
            "ObjectName": "/vol/data/file.txt",
            "Result": "Success",
        }).encode("utf-8")

        mock_body = MagicMock()
        mock_body.read.return_value = valid_log
        mock_s3.get_object.return_value = {"Body": mock_body}

        mock_http.request.return_value = MagicMock(
            status=200, data=b'{"text":"Success","code":0}'
        )

        event = {
            "Records": [
                {
                    "s3": {
                        "bucket": {"name": "test-bucket"},
                        "object": {"key": "audit/file1.json"},
                    }
                }
            ]
        }

        result = handler.lambda_handler(event, None)

        assert result["statusCode"] == 200
        assert result["body"]["total_logs"] == 1
        assert result["body"]["total_shipped"] == 1
        assert result["body"]["errors"] == []


# ─── ONTAP audit format regression (added after the JSON-only defect) ───────

NAMESPACED_ONTAP_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Events>
<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event">
  <System>
    <EventID>4663</EventID>
    <TimeCreated SystemTime="2026-08-07T04:00:00Z"/>
    <Computer>svm-prod-01</Computer>
  </System>
  <EventData>
    <Data Name="SubjectUserName">CORP\\jdoe</Data>
    <Data Name="ObjectName">/vol/data/first.txt</Data>
    <Data Name="ObjectType">ReadData</Data>
    <Data Name="IpAddress">198.51.100.10</Data>
    <Data Name="Keywords">Audit Success</Data>
  </EventData>
</Event>
<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event">
  <System>
    <EventID>4660</EventID>
    <TimeCreated SystemTime="2026-08-07T04:00:01Z"/>
    <Computer>svm-prod-01</Computer>
  </System>
  <EventData>
    <Data Name="SubjectUserName">CORP\\jdoe</Data>
    <Data Name="ObjectName">/vol/data/second.docx</Data>
    <Data Name="ObjectType">Delete</Data>
    <Data Name="IpAddress">198.51.100.10</Data>
    <Data Name="Keywords">Audit Success</Data>
  </EventData>
</Event>
</Events>"""


class TestOntapAuditFormats:
    """ONTAP writes EVTX or XML, never JSON.

    This handler previously parsed JSON lines only, so a real audit log produced
    one useless {"message": "<xml line>"} record per line (or zero events). These
    tests pin the parsed result so that cannot regress.
    """

    def test_namespaced_xml_yields_one_record_per_event(self):
        events = handler._parse_logs(NAMESPACED_ONTAP_XML.encode("utf-8"), "audit/2026/08/07/log.xml")

        assert len(events) == 2, "each <Event> must become its own record"
        assert {str(e.get("event_type")) for e in events} == {"4663", "4660"}

    def test_fields_are_extracted(self):
        events = handler._parse_logs(NAMESPACED_ONTAP_XML.encode("utf-8"), "audit/log.xml")
        first = next(e for e in events if str(e.get("event_type")) == "4663")

        assert first["user"] == "CORP\\jdoe"
        assert first["path"] == "/vol/data/first.txt"
        assert first["operation"] == "ReadData"
        assert first["client_ip"] == "198.51.100.10"
        assert first["result"] == "Audit Success"
        assert first["svm"] == "svm-prod-01"

    def test_no_raw_xml_blob_records(self):
        """The old behaviour emitted the raw XML text as the message field."""
        events = handler._parse_logs(NAMESPACED_ONTAP_XML.encode("utf-8"), "audit/log.xml")

        assert not any(str(e.get("message", "")).lstrip().startswith("<") for e in events)

    def test_xml_without_suffix_is_detected_by_content(self):
        """ONTAP rotations do not always carry a .xml suffix."""
        events = handler._parse_logs(NAMESPACED_ONTAP_XML.encode("utf-8"), "audit/rotated_0001")

        assert len(events) == 2

    def test_evtx_is_reported_not_silently_dropped(self):
        """EVTX needs -format xml for field extraction; say so rather than drop it."""
        events = handler._parse_logs(b"ElfFile\x00" + b"\x00" * 64, "audit/log.evtx")

        assert len(events) == 1
        assert "-format xml" in events[0].get("message", "")

    def test_json_still_parses(self):
        """The JSON path must keep working for synthetic and test payloads."""
        payload = b'{"EventID":"4663","UserName":"u1","ObjectName":"/a.txt"}'
        events = handler._parse_logs(payload, "audit/log.json")

        assert len(events) == 1
        assert events[0]["user"] == "u1"

    def test_empty_file_yields_no_events(self):
        assert handler._parse_logs(b"", "audit/empty.xml") == []
