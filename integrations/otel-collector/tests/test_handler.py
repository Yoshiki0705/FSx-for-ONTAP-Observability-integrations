"""Unit tests for OTel Collector OTLP shipper Lambda handler."""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add lambda directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "lambda"))

import handler


class TestExtractS3Records:
    """Tests for _extract_s3_records function."""

    def test_s3_event_notification(self, sample_s3_event):
        records = handler._extract_s3_records(sample_s3_event)
        assert len(records) == 1
        assert records[0]["bucket"] == "fsxn-audit-logs-bucket"
        assert records[0]["key"] == "audit/svm1/2026/01/15/audit_log_001.json"

    def test_eventbridge_event(self, sample_eventbridge_event):
        records = handler._extract_s3_records(sample_eventbridge_event)
        assert len(records) == 1
        assert records[0]["bucket"] == "fsxn-audit-logs-bucket"
        assert records[0]["key"] == "audit/svm1/2026/01/15/audit_log_001.json"

    def test_empty_event(self):
        records = handler._extract_s3_records({})
        assert len(records) == 0

    def test_multiple_records(self):
        event = {
            "Records": [
                {"s3": {"bucket": {"name": "b1"}, "object": {"key": "k1.json"}}},
                {"s3": {"bucket": {"name": "b2"}, "object": {"key": "k2.json"}}},
            ]
        }
        records = handler._extract_s3_records(event)
        assert len(records) == 2

    def test_missing_bucket_filtered(self):
        event = {
            "Records": [
                {"s3": {"bucket": {"name": ""}, "object": {"key": "k1.json"}}},
            ]
        }
        records = handler._extract_s3_records(event)
        assert len(records) == 0


class TestDetermineSeverity:
    """Tests for determine_severity function."""

    def test_success_returns_info(self):
        assert handler.determine_severity("Success") == (9, "INFO")

    def test_failure_returns_warn(self):
        assert handler.determine_severity("Failure") == (13, "WARN")

    def test_access_denied_returns_warn(self):
        assert handler.determine_severity("Access Denied") == (13, "WARN")

    def test_error_occurred_returns_warn(self):
        assert handler.determine_severity("Error occurred") == (13, "WARN")

    def test_case_insensitive_failure(self):
        assert handler.determine_severity("FAILURE") == (13, "WARN")
        assert handler.determine_severity("failure") == (13, "WARN")
        assert handler.determine_severity("FaIlUrE") == (13, "WARN")

    def test_none_returns_info(self):
        assert handler.determine_severity(None) == (9, "INFO")

    def test_empty_returns_info(self):
        assert handler.determine_severity("") == (9, "INFO")

    def test_partial_match(self):
        assert handler.determine_severity("failed to connect") == (13, "WARN")
        assert handler.determine_severity("permission denied by policy") == (13, "WARN")


class TestTimestampToUnixNano:
    """Tests for timestamp_to_unix_nano function."""

    def test_valid_iso8601_utc(self):
        result = handler.timestamp_to_unix_nano("2026-01-15T12:00:01Z")
        # 2026-01-15T12:00:01Z in nanoseconds
        expected_seconds = datetime(2026, 1, 15, 12, 0, 1, tzinfo=timezone.utc).timestamp()
        expected_nano = str(int(expected_seconds * 1_000_000_000))
        assert result == expected_nano

    def test_valid_iso8601_with_offset(self):
        result = handler.timestamp_to_unix_nano("2026-01-15T21:00:01+09:00")
        # Same as 2026-01-15T12:00:01Z
        expected_seconds = datetime(2026, 1, 15, 12, 0, 1, tzinfo=timezone.utc).timestamp()
        expected_nano = str(int(expected_seconds * 1_000_000_000))
        assert result == expected_nano

    def test_none_returns_current_time(self):
        before = int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)
        result = int(handler.timestamp_to_unix_nano(None))
        after = int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)
        assert before <= result <= after

    def test_empty_returns_current_time(self):
        before = int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)
        result = int(handler.timestamp_to_unix_nano(""))
        after = int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)
        assert before <= result <= after

    def test_invalid_returns_current_time(self):
        before = int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)
        result = int(handler.timestamp_to_unix_nano("not-a-timestamp"))
        after = int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)
        assert before <= result <= after


class TestMapLogRecord:
    """Tests for map_log_record function."""

    def test_all_fields_present(self):
        log = {
            "Timestamp": "2026-01-15T12:00:01Z",
            "EventID": "4663",
            "SVMName": "svm-prod-01",
            "UserName": "admin@corp.local",
            "ClientIP": "10.0.1.50",
            "Operation": "ReadData",
            "ObjectName": "/vol/data/file.txt",
            "Result": "Success",
        }
        record = handler.map_log_record(log)

        attr_map = {a["key"]: a["value"]["stringValue"] for a in record["attributes"]}
        assert attr_map["event.type"] == "4663"
        assert attr_map["user.name"] == "admin@corp.local"
        assert attr_map["client.address"] == "10.0.1.50"
        assert attr_map["fsxn.operation"] == "ReadData"
        assert attr_map["fsxn.path"] == "/vol/data/file.txt"
        assert attr_map["fsxn.result"] == "Success"
        assert attr_map["fsxn.svm"] == "svm-prod-01"

    def test_absent_fields_omitted(self):
        log = {"Timestamp": "2026-01-15T12:00:01Z", "Result": "Success"}
        record = handler.map_log_record(log)

        attr_keys = {a["key"] for a in record["attributes"]}
        assert "event.type" not in attr_keys
        assert "user.name" not in attr_keys
        assert "client.address" not in attr_keys
        assert "fsxn.operation" not in attr_keys
        assert "fsxn.path" not in attr_keys
        # Result is present
        assert "fsxn.result" in attr_keys

    def test_empty_fields_omitted(self):
        log = {
            "EventID": "",
            "UserName": "",
            "ClientIP": "",
            "Operation": "",
            "ObjectName": "",
            "Result": "",
            "SVMName": "",
        }
        record = handler.map_log_record(log)
        assert record["attributes"] == []

    def test_severity_in_record(self):
        log = {"Result": "Failure"}
        record = handler.map_log_record(log)
        assert record["severityNumber"] == 13
        assert record["severityText"] == "WARN"

    def test_body_contains_json(self):
        log = {"EventID": "4663", "Operation": "ReadData"}
        record = handler.map_log_record(log)
        body = json.loads(record["body"]["stringValue"])
        assert body["EventID"] == "4663"


class TestBuildOtlpPayload:
    """Tests for build_otlp_payload function."""

    def test_resource_attributes_present(self):
        logs = [{"EventID": "4663", "Result": "Success"}]
        payload = handler.build_otlp_payload(logs, "fsxn-audit", "test.json")

        resource_attrs = payload["resourceLogs"][0]["resource"]["attributes"]
        attr_map = {a["key"]: a["value"]["stringValue"] for a in resource_attrs}
        assert attr_map["service.name"] == "fsxn-audit"
        assert attr_map["cloud.provider"] == "aws"
        assert attr_map["cloud.platform"] == "aws_fsx"

    def test_scope_metadata(self):
        logs = [{"EventID": "4663"}]
        payload = handler.build_otlp_payload(logs, "fsxn-audit", "test.json")

        scope = payload["resourceLogs"][0]["scopeLogs"][0]["scope"]
        assert scope["name"] == "fsxn-otel-shipper"
        assert scope["version"] == "1.0.0"

    def test_empty_input(self):
        payload = handler.build_otlp_payload([], "fsxn-audit", "test.json")
        log_records = payload["resourceLogs"][0]["scopeLogs"][0]["logRecords"]
        assert log_records == []

    def test_multiple_logs(self):
        logs = [{"EventID": str(i)} for i in range(5)]
        payload = handler.build_otlp_payload(logs, "fsxn-audit", "test.json")
        log_records = payload["resourceLogs"][0]["scopeLogs"][0]["logRecords"]
        assert len(log_records) == 5


class TestSendOtlpPayload:
    """Tests for _send_otlp_payload function."""

    @patch("handler.http")
    def test_successful_send(self, mock_http):
        mock_response = MagicMock()
        mock_response.status = 200
        mock_http.request.return_value = mock_response

        payload = {"resourceLogs": []}
        result = handler._send_otlp_payload(payload, "http://localhost:4318")

        assert result is True
        mock_http.request.assert_called_once()

    @patch("handler.http")
    def test_retry_on_server_error(self, mock_http):
        mock_error = MagicMock()
        mock_error.status = 500
        mock_error.data = b"Internal Server Error"

        mock_success = MagicMock()
        mock_success.status = 200

        mock_http.request.side_effect = [mock_error, mock_success]

        with patch("handler.time.sleep"):
            result = handler._send_otlp_payload(
                {"resourceLogs": []}, "http://localhost:4318"
            )

        assert result is True
        assert mock_http.request.call_count == 2

    @patch("handler.http")
    def test_no_retry_on_client_error(self, mock_http):
        mock_response = MagicMock()
        mock_response.status = 403
        mock_response.data = b"Forbidden"
        mock_http.request.return_value = mock_response

        result = handler._send_otlp_payload(
            {"resourceLogs": []}, "http://localhost:4318"
        )

        assert result is False
        assert mock_http.request.call_count == 1

    @patch("handler.http")
    def test_retry_on_rate_limit(self, mock_http):
        mock_rate_limit = MagicMock()
        mock_rate_limit.status = 429
        mock_rate_limit.headers = {"Retry-After": "1"}

        mock_success = MagicMock()
        mock_success.status = 200

        mock_http.request.side_effect = [mock_rate_limit, mock_success]

        with patch("handler.time.sleep"):
            result = handler._send_otlp_payload(
                {"resourceLogs": []}, "http://localhost:4318"
            )

        assert result is True

    @patch("handler.http")
    def test_max_retries_exhausted(self, mock_http):
        mock_error = MagicMock()
        mock_error.status = 500
        mock_error.data = b"Error"
        mock_http.request.return_value = mock_error

        with patch("handler.time.sleep"):
            result = handler._send_otlp_payload(
                {"resourceLogs": []}, "http://localhost:4318"
            )

        assert result is False
        assert mock_http.request.call_count == 3


class TestAuthModeHeader:
    """Tests for AUTH_MODE="header" and EXTRA_HEADERS_JSON (generic custom-header
    auth support, needed for vendors like Mackerel with a non-Bearer/Basic
    auth header, e.g. "Mackerel-Api-Key")."""

    @patch("handler.http")
    @patch("handler.s3_client")
    @patch("handler.secrets_client")
    def test_header_auth_mode_uses_custom_header_name(
        self, mock_secrets, mock_s3, mock_http, monkeypatch,
        sample_s3_event, sample_json_audit_logs,
    ):
        handler._api_key_cache = None
        # API_KEY_SECRET_ARN is a module-level constant read once at import
        # time from the env var; conftest's monkeypatch.setenv doesn't affect
        # an already-imported module, so it must be patched directly here.
        monkeypatch.setattr(
            handler, "API_KEY_SECRET_ARN",
            "arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:test",
        )
        monkeypatch.setattr(handler, "AUTH_MODE", "header")
        monkeypatch.setattr(handler, "AUTH_HEADER_NAME", "Mackerel-Api-Key")

        mock_secrets.get_secret_value.return_value = {
            "SecretString": json.dumps({"api_key": "write-scoped-key"})
        }
        mock_body = MagicMock()
        mock_body.read.return_value = sample_json_audit_logs.encode("utf-8")
        mock_s3.get_object.return_value = {"Body": mock_body}
        mock_response = MagicMock()
        mock_response.status = 200
        mock_http.request.return_value = mock_response

        handler.lambda_handler(sample_s3_event, None)

        _, kwargs = mock_http.request.call_args
        sent_headers = kwargs["headers"]
        assert sent_headers["Mackerel-Api-Key"] == "write-scoped-key"
        # Must NOT wrap the token in "Bearer "/"Basic " like the other modes
        assert "Authorization" not in sent_headers

    @patch("handler.http")
    @patch("handler.s3_client")
    @patch("handler.secrets_client")
    def test_extra_headers_json_merged_with_auth_headers(
        self, mock_secrets, mock_s3, mock_http, monkeypatch,
        sample_s3_event, sample_json_audit_logs,
    ):
        handler._api_key_cache = None
        monkeypatch.setattr(
            handler, "API_KEY_SECRET_ARN",
            "arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:test",
        )
        monkeypatch.setattr(handler, "AUTH_MODE", "header")
        monkeypatch.setattr(handler, "AUTH_HEADER_NAME", "Mackerel-Api-Key")
        monkeypatch.setattr(handler, "EXTRA_HEADERS_JSON", '{"Accept": "*/*"}')

        mock_secrets.get_secret_value.return_value = {
            "SecretString": json.dumps({"api_key": "write-scoped-key"})
        }
        mock_body = MagicMock()
        mock_body.read.return_value = sample_json_audit_logs.encode("utf-8")
        mock_s3.get_object.return_value = {"Body": mock_body}
        mock_response = MagicMock()
        mock_response.status = 200
        mock_http.request.return_value = mock_response

        handler.lambda_handler(sample_s3_event, None)

        _, kwargs = mock_http.request.call_args
        sent_headers = kwargs["headers"]
        assert sent_headers["Mackerel-Api-Key"] == "write-scoped-key"
        assert sent_headers["Accept"] == "*/*"

    @patch("handler.http")
    @patch("handler.s3_client")
    @patch("handler.secrets_client")
    def test_extra_headers_json_works_without_api_key_secret(
        self, mock_secrets, mock_s3, mock_http, monkeypatch,
        sample_s3_event, sample_json_audit_logs,
    ):
        """EXTRA_HEADERS_JSON must apply even when API_KEY_SECRET_ARN is unset
        (e.g. a Collector endpoint that needs Accept: */* but no per-request
        secret)."""
        handler._api_key_cache = None
        monkeypatch.setattr(handler, "API_KEY_SECRET_ARN", "")
        monkeypatch.setattr(handler, "EXTRA_HEADERS_JSON", '{"Accept": "*/*"}')

        mock_body = MagicMock()
        mock_body.read.return_value = sample_json_audit_logs.encode("utf-8")
        mock_s3.get_object.return_value = {"Body": mock_body}
        mock_response = MagicMock()
        mock_response.status = 200
        mock_http.request.return_value = mock_response

        handler.lambda_handler(sample_s3_event, None)

        mock_secrets.get_secret_value.assert_not_called()
        _, kwargs = mock_http.request.call_args
        assert kwargs["headers"]["Accept"] == "*/*"

    @patch("handler.http")
    @patch("handler.s3_client")
    @patch("handler.secrets_client")
    def test_malformed_extra_headers_json_ignored_not_fatal(
        self, mock_secrets, mock_s3, mock_http, monkeypatch,
        sample_s3_event, sample_json_audit_logs,
    ):
        """Malformed EXTRA_HEADERS_JSON must not crash the handler — it should
        log a warning and proceed without the extra headers."""
        handler._api_key_cache = None
        monkeypatch.setattr(handler, "API_KEY_SECRET_ARN", "")
        monkeypatch.setattr(handler, "EXTRA_HEADERS_JSON", "{not valid json")

        mock_body = MagicMock()
        mock_body.read.return_value = sample_json_audit_logs.encode("utf-8")
        mock_s3.get_object.return_value = {"Body": mock_body}
        mock_response = MagicMock()
        mock_response.status = 200
        mock_http.request.return_value = mock_response

        result = handler.lambda_handler(sample_s3_event, None)

        assert result["statusCode"] == 200
        _, kwargs = mock_http.request.call_args
        assert kwargs["headers"] == {"Content-Type": "application/json"}

    @patch("handler.http")
    @patch("handler.s3_client")
    @patch("handler.secrets_client")
    def test_bearer_mode_unaffected_by_new_options(
        self, mock_secrets, mock_s3, mock_http, monkeypatch,
        sample_s3_event, sample_json_audit_logs,
    ):
        """Regression guard: default bearer mode must behave exactly as before
        when AUTH_HEADER_NAME/EXTRA_HEADERS_JSON are left at their defaults."""
        handler._api_key_cache = None
        monkeypatch.setattr(
            handler, "API_KEY_SECRET_ARN",
            "arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:test",
        )
        monkeypatch.setattr(handler, "AUTH_MODE", "bearer")

        mock_secrets.get_secret_value.return_value = {
            "SecretString": json.dumps({"api_key": "test-token"})
        }
        mock_body = MagicMock()
        mock_body.read.return_value = sample_json_audit_logs.encode("utf-8")
        mock_s3.get_object.return_value = {"Body": mock_body}
        mock_response = MagicMock()
        mock_response.status = 200
        mock_http.request.return_value = mock_response

        handler.lambda_handler(sample_s3_event, None)

        _, kwargs = mock_http.request.call_args
        assert kwargs["headers"]["Authorization"] == "Bearer test-token"


class TestOtlpContentTypeProtobuf:
    """Tests for OTLP_CONTENT_TYPE="protobuf" (needed for vendors like
    Mackerel whose OTLP/HTTP log endpoint rejects OTLP/JSON bodies with
    HTTP 400 and only accepts Protobuf-encoded requests)."""

    @patch("handler.http")
    @patch("handler.s3_client")
    @patch("handler.secrets_client")
    def test_protobuf_content_type_sets_correct_header_and_body_encoding(
        self, mock_secrets, mock_s3, mock_http, monkeypatch,
        sample_s3_event, sample_json_audit_logs,
    ):
        handler._api_key_cache = None
        monkeypatch.setattr(handler, "API_KEY_SECRET_ARN", "")
        monkeypatch.setattr(handler, "OTLP_CONTENT_TYPE", "protobuf")

        mock_body = MagicMock()
        mock_body.read.return_value = sample_json_audit_logs.encode("utf-8")
        mock_s3.get_object.return_value = {"Body": mock_body}
        mock_response = MagicMock()
        mock_response.status = 200
        mock_http.request.return_value = mock_response

        result = handler.lambda_handler(sample_s3_event, None)

        assert result["statusCode"] == 200
        _, kwargs = mock_http.request.call_args
        assert kwargs["headers"]["Content-Type"] == "application/x-protobuf"
        # Body must be raw bytes, not a json.dumps() string, and must not be
        # valid JSON (it's a binary Protobuf wire-format message).
        body = kwargs["body"]
        assert isinstance(body, bytes)
        # A Protobuf-encoded body must not be parseable as JSON (either it
        # raises JSONDecodeError, or the raw bytes aren't even valid UTF-8
        # and raise UnicodeDecodeError first — both prove it isn't OTLP/JSON).
        with pytest.raises((json.JSONDecodeError, UnicodeDecodeError)):
            json.loads(body)

    @patch("handler.http")
    @patch("handler.s3_client")
    @patch("handler.secrets_client")
    def test_default_content_type_is_json_unaffected(
        self, mock_secrets, mock_s3, mock_http, monkeypatch,
        sample_s3_event, sample_json_audit_logs,
    ):
        """Regression guard: leaving OTLP_CONTENT_TYPE at its default must
        behave exactly as before (OTLP/JSON body, Content-Type: application/json)."""
        handler._api_key_cache = None
        monkeypatch.setattr(handler, "API_KEY_SECRET_ARN", "")

        mock_body = MagicMock()
        mock_body.read.return_value = sample_json_audit_logs.encode("utf-8")
        mock_s3.get_object.return_value = {"Body": mock_body}
        mock_response = MagicMock()
        mock_response.status = 200
        mock_http.request.return_value = mock_response

        handler.lambda_handler(sample_s3_event, None)

        _, kwargs = mock_http.request.call_args
        assert kwargs["headers"]["Content-Type"] == "application/json"
        # Body must still be valid JSON, unchanged from before this option existed.
        json.loads(kwargs["body"])

    @patch("handler.http")
    @patch("handler.s3_client")
    @patch("handler.secrets_client")
    def test_protobuf_content_type_combined_with_header_auth(
        self, mock_secrets, mock_s3, mock_http, monkeypatch,
        sample_s3_event, sample_json_audit_logs,
    ):
        """The combination actually needed for Mackerel's direct-send path:
        AUTH_MODE=header + OTLP_CONTENT_TYPE=protobuf together."""
        handler._api_key_cache = None
        monkeypatch.setattr(
            handler, "API_KEY_SECRET_ARN",
            "arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:test",
        )
        monkeypatch.setattr(handler, "AUTH_MODE", "header")
        monkeypatch.setattr(handler, "AUTH_HEADER_NAME", "Mackerel-Api-Key")
        monkeypatch.setattr(handler, "EXTRA_HEADERS_JSON", '{"Accept": "*/*"}')
        monkeypatch.setattr(handler, "OTLP_CONTENT_TYPE", "protobuf")

        mock_secrets.get_secret_value.return_value = {
            "SecretString": json.dumps({"api_key": "write-scoped-key"})
        }
        mock_body = MagicMock()
        mock_body.read.return_value = sample_json_audit_logs.encode("utf-8")
        mock_s3.get_object.return_value = {"Body": mock_body}
        mock_response = MagicMock()
        mock_response.status = 200
        mock_http.request.return_value = mock_response

        result = handler.lambda_handler(sample_s3_event, None)

        assert result["statusCode"] == 200
        _, kwargs = mock_http.request.call_args
        assert kwargs["headers"]["Mackerel-Api-Key"] == "write-scoped-key"
        assert kwargs["headers"]["Accept"] == "*/*"
        assert kwargs["headers"]["Content-Type"] == "application/x-protobuf"
        assert isinstance(kwargs["body"], bytes)


class TestSendOtlpPayloadContentTypeUnit:
    """Direct unit tests of _send_otlp_payload()'s content_type parameter,
    independent of the full lambda_handler flow."""

    @patch("handler.http")
    def test_protobuf_encodes_via_otlp_protobuf_module(self, mock_http):
        import otlp_protobuf

        mock_response = MagicMock()
        mock_response.status = 200
        mock_http.request.return_value = mock_response

        payload = {
            "resourceLogs": [
                {
                    "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "x"}}]},
                    "scopeLogs": [
                        {"scope": {"name": "s"}, "logRecords": [
                            {"timeUnixNano": "1", "severityNumber": 9, "severityText": "INFO",
                             "body": {"stringValue": "hi"}, "attributes": []}
                        ]}
                    ],
                }
            ]
        }
        result = handler._send_otlp_payload(payload, "https://example.com", content_type="protobuf")

        assert result is True
        _, kwargs = mock_http.request.call_args
        assert kwargs["body"] == otlp_protobuf.encode_logs_data(payload)
        assert kwargs["headers"]["Content-Type"] == "application/x-protobuf"

    @patch("handler.http")
    def test_json_is_default_when_content_type_omitted(self, mock_http):
        mock_response = MagicMock()
        mock_response.status = 200
        mock_http.request.return_value = mock_response

        payload = {"resourceLogs": []}
        handler._send_otlp_payload(payload, "https://example.com")

        _, kwargs = mock_http.request.call_args
        assert kwargs["headers"]["Content-Type"] == "application/json"
        assert kwargs["body"] == json.dumps(payload).encode("utf-8")


class TestParseJsonLogs:
    """Tests for _parse_json_logs function."""

    def test_newline_delimited_json(self):
        data = '{"EventID":"4663"}\n{"EventID":"4656"}'
        events = handler._parse_json_logs(data)
        assert len(events) == 2

    def test_json_array(self):
        data = json.dumps([{"event": "test1"}, {"event": "test2"}])
        events = handler._parse_json_logs(data)
        assert len(events) == 2

    def test_empty_input(self):
        events = handler._parse_json_logs("")
        assert len(events) == 0

    def test_invalid_json_lines_skipped(self):
        data = '{"valid": true}\nnot json\n{"also_valid": true}'
        events = handler._parse_json_logs(data)
        assert len(events) == 2


class TestLambdaHandler:
    """Integration tests for the full Lambda handler."""

    @patch("handler.http")
    @patch("handler.s3_client")
    @patch("handler.secrets_client")
    def test_full_flow(self, mock_secrets, mock_s3, mock_http, sample_s3_event, sample_json_audit_logs):
        handler._api_key_cache = None

        mock_secrets.get_secret_value.return_value = {
            "SecretString": json.dumps({"api_key": "test-token"})
        }

        mock_body = MagicMock()
        mock_body.read.return_value = sample_json_audit_logs.encode("utf-8")
        mock_s3.get_object.return_value = {"Body": mock_body}

        mock_response = MagicMock()
        mock_response.status = 200
        mock_http.request.return_value = mock_response

        result = handler.lambda_handler(sample_s3_event, None)

        assert result["statusCode"] == 200
        assert result["body"]["total_logs"] == 3
        assert result["body"]["total_shipped"] == 3
        assert result["body"]["errors"] == []

    @patch("handler.http")
    @patch("handler.s3_client")
    @patch("handler.secrets_client")
    def test_s3_read_error(self, mock_secrets, mock_s3, mock_http, sample_s3_event):
        handler._api_key_cache = None

        mock_secrets.get_secret_value.return_value = {
            "SecretString": "test-token"
        }

        from botocore.exceptions import ClientError
        mock_s3.get_object.side_effect = ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": "Not found"}},
            "GetObject",
        )

        result = handler.lambda_handler(sample_s3_event, None)

        assert result["statusCode"] == 502
        assert len(result["body"]["errors"]) == 1

    @patch("handler.http")
    @patch("handler.s3_client")
    @patch("handler.secrets_client")
    def test_otlp_delivery_failure(self, mock_secrets, mock_s3, mock_http, sample_s3_event, sample_json_audit_logs):
        handler._api_key_cache = None

        mock_secrets.get_secret_value.return_value = {
            "SecretString": "test-token"
        }

        mock_body = MagicMock()
        mock_body.read.return_value = sample_json_audit_logs.encode("utf-8")
        mock_s3.get_object.return_value = {"Body": mock_body}

        mock_response = MagicMock()
        mock_response.status = 500
        mock_response.data = b"Server Error"
        mock_http.request.return_value = mock_response

        with patch("handler.time.sleep"):
            result = handler.lambda_handler(sample_s3_event, None)

        assert result["statusCode"] == 502
        assert result["body"]["total_shipped"] == 0


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
        events = handler._parse_audit_logs(NAMESPACED_ONTAP_XML.encode("utf-8"), "audit/2026/08/07/log.xml")

        assert len(events) == 2, "each <Event> must become its own record"
        assert {str(e.get("event_type")) for e in events} == {"4663", "4660"}

    def test_fields_are_extracted(self):
        events = handler._parse_audit_logs(NAMESPACED_ONTAP_XML.encode("utf-8"), "audit/log.xml")
        first = next(e for e in events if str(e.get("event_type")) == "4663")

        assert first["user"] == "CORP\\jdoe"
        assert first["path"] == "/vol/data/first.txt"
        assert first["operation"] == "ReadData"
        assert first["client_ip"] == "198.51.100.10"
        assert first["result"] == "Audit Success"
        assert first["svm"] == "svm-prod-01"

    def test_no_raw_xml_blob_records(self):
        """The old behaviour emitted the raw XML text as the message field."""
        events = handler._parse_audit_logs(NAMESPACED_ONTAP_XML.encode("utf-8"), "audit/log.xml")

        assert not any(str(e.get("message", "")).lstrip().startswith("<") for e in events)

    def test_xml_without_suffix_is_detected_by_content(self):
        """ONTAP rotations do not always carry a .xml suffix."""
        events = handler._parse_audit_logs(NAMESPACED_ONTAP_XML.encode("utf-8"), "audit/rotated_0001")

        assert len(events) == 2

    def test_evtx_is_reported_not_silently_dropped(self):
        """EVTX needs -format xml for field extraction; say so rather than drop it."""
        events = handler._parse_audit_logs(b"ElfFile\x00" + b"\x00" * 64, "audit/log.evtx")

        assert len(events) == 1
        assert "-format xml" in events[0].get("message", "")

    def test_json_still_parses(self):
        """The JSON path must keep working for synthetic and test payloads."""
        payload = b'{"EventID":"4663","UserName":"u1","ObjectName":"/a.txt"}'
        events = handler._parse_audit_logs(payload, "audit/log.json")

        assert len(events) == 1
        assert events[0]["user"] == "u1"

    def test_empty_file_yields_no_events(self):
        assert handler._parse_audit_logs(b"", "audit/empty.xml") == []
