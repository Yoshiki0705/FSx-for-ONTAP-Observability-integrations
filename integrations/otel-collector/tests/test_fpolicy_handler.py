"""Unit tests for FPolicy → OTel Collector OTLP handler."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "lambda"))

import fpolicy_handler


# ─── Sample FPolicy events ─────────────────────────────────────────────────

SAMPLE_FILE_CREATE_EVENT = {
    "version": "0",
    "id": "abc123",
    "detail-type": "FPolicy File Operation",
    "source": "fpolicy.fsxn",
    "account": "123456789012",
    "time": "2026-01-15T12:00:01Z",
    "region": "ap-northeast-1",
    "detail": {
        "operation": "create",
        "file_path": "/vol/data/shared/new_document.docx",
        "user": "admin@corp.local",
        "client_ip": "10.0.1.50",
        "volume": "vol_data",
        "svm": "svm-prod-01",
        "protocol": "cifs",
        "timestamp": "2026-01-15T12:00:01Z",
    },
}

SAMPLE_FILE_WRITE_EVENT = {
    "version": "0",
    "id": "def456",
    "detail-type": "FPolicy File Operation",
    "source": "fpolicy.fsxn",
    "detail": {
        "operation": "write",
        "file_path": "/vol/data/shared/existing_file.xlsx",
        "user": "user1@corp.local",
        "client_ip": "10.0.1.51",
        "volume": "vol_data",
        "svm": "svm-prod-01",
        "protocol": "cifs",
        "timestamp": "2026-01-15T12:00:02Z",
    },
}

SAMPLE_FILE_RENAME_EVENT = {
    "version": "0",
    "id": "ghi789",
    "detail-type": "FPolicy File Operation",
    "source": "fpolicy.fsxn",
    "detail": {
        "operation": "rename",
        "file_path": "/vol/data/shared/renamed_file.txt",
        "user": "user2@corp.local",
        "client_ip": "10.0.1.52",
        "volume": "vol_data",
        "svm": "svm-prod-01",
        "protocol": "cifs",
        "timestamp": "2026-01-15T12:00:03Z",
    },
}

SAMPLE_FILE_DELETE_EVENT = {
    "version": "0",
    "id": "jkl012",
    "detail-type": "FPolicy File Operation",
    "source": "fpolicy.fsxn",
    "detail": {
        "operation": "delete",
        "file_path": "/vol/data/shared/deleted_file.tmp",
        "user": "admin@corp.local",
        "client_ip": "10.0.1.50",
        "volume": "vol_data",
        "svm": "svm-prod-01",
        "protocol": "cifs",
        "timestamp": "2026-01-15T12:00:04Z",
    },
}


class TestBuildFpolicyOtlpPayload:
    """Tests for build_fpolicy_otlp_payload function."""

    def test_file_create_payload_structure(self):
        """File create event produces valid OTLP payload."""
        detail = SAMPLE_FILE_CREATE_EVENT["detail"]
        payload = fpolicy_handler.build_fpolicy_otlp_payload(detail)

        # Validate structure
        assert "resourceLogs" in payload
        resource_log = payload["resourceLogs"][0]

        # Resource attributes
        attr_map = {
            a["key"]: a["value"]["stringValue"]
            for a in resource_log["resource"]["attributes"]
        }
        assert attr_map["service.name"] == "fsxn-fpolicy"

        # Log record
        log_record = resource_log["scopeLogs"][0]["logRecords"][0]
        assert log_record["severityNumber"] == 9
        assert log_record["severityText"] == "INFO"
        assert log_record["timeUnixNano"] != ""

        # Attributes
        record_attrs = {a["key"]: a["value"]["stringValue"] for a in log_record["attributes"]}
        assert record_attrs["operation_type"] == "create"
        assert record_attrs["file_path"] == "/vol/data/shared/new_document.docx"
        assert record_attrs["user"] == "admin@corp.local"
        assert record_attrs["client_ip"] == "10.0.1.50"

    def test_field_mapping(self):
        """FPolicy fields are correctly mapped to OTLP attributes."""
        detail = {
            "operation": "write",
            "file_path": "/vol/data/test.txt",
            "user": "testuser",
            "client_ip": "192.168.1.1",
            "timestamp": "2026-01-15T12:00:00Z",
        }
        payload = fpolicy_handler.build_fpolicy_otlp_payload(detail)
        log_record = payload["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]

        attr_map = {a["key"]: a["value"]["stringValue"] for a in log_record["attributes"]}
        assert attr_map["operation_type"] == "write"
        assert attr_map["file_path"] == "/vol/data/test.txt"
        assert attr_map["user"] == "testuser"
        assert attr_map["client_ip"] == "192.168.1.1"

    def test_missing_fields_omitted(self):
        """Missing fields are not included in attributes."""
        detail = {"operation": "create", "timestamp": "2026-01-15T12:00:00Z"}
        payload = fpolicy_handler.build_fpolicy_otlp_payload(detail)
        log_record = payload["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]

        attr_keys = {a["key"] for a in log_record["attributes"]}
        assert "operation_type" in attr_keys
        assert "file_path" not in attr_keys
        assert "user" not in attr_keys
        assert "client_ip" not in attr_keys

    def test_additional_fields_included(self):
        """Additional fields (volume, svm, protocol) are included."""
        detail = {
            "operation": "create",
            "file_path": "/test",
            "volume": "vol1",
            "svm": "svm1",
            "protocol": "nfs",
            "timestamp": "2026-01-15T12:00:00Z",
        }
        payload = fpolicy_handler.build_fpolicy_otlp_payload(detail)
        log_record = payload["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]

        attr_map = {a["key"]: a["value"]["stringValue"] for a in log_record["attributes"]}
        assert attr_map["volume"] == "vol1"
        assert attr_map["svm"] == "svm1"
        assert attr_map["protocol"] == "nfs"


class TestSendOtlpPayload:
    """Tests for _send_otlp_payload function."""

    @patch("fpolicy_handler.http")
    def test_successful_send(self, mock_http):
        mock_response = MagicMock()
        mock_response.status = 200
        mock_http.request.return_value = mock_response

        result = fpolicy_handler._send_otlp_payload({"resourceLogs": []})
        assert result is True

    @patch("fpolicy_handler.http")
    def test_retry_on_5xx(self, mock_http):
        mock_error = MagicMock()
        mock_error.status = 500
        mock_error.data = b"Error"

        mock_success = MagicMock()
        mock_success.status = 200

        mock_http.request.side_effect = [mock_error, mock_success]

        with patch("fpolicy_handler.time.sleep"):
            result = fpolicy_handler._send_otlp_payload({"resourceLogs": []})

        assert result is True
        assert mock_http.request.call_count == 2

    @patch("fpolicy_handler.http")
    def test_retry_on_429(self, mock_http):
        mock_rate = MagicMock()
        mock_rate.status = 429
        mock_rate.headers = {"Retry-After": "2"}

        mock_success = MagicMock()
        mock_success.status = 200

        mock_http.request.side_effect = [mock_rate, mock_success]

        with patch("fpolicy_handler.time.sleep"):
            result = fpolicy_handler._send_otlp_payload({"resourceLogs": []})

        assert result is True

    @patch("fpolicy_handler.http")
    def test_no_retry_on_4xx(self, mock_http):
        mock_response = MagicMock()
        mock_response.status = 403
        mock_response.data = b"Forbidden"
        mock_http.request.return_value = mock_response

        result = fpolicy_handler._send_otlp_payload({"resourceLogs": []})
        assert result is False
        assert mock_http.request.call_count == 1

    @patch("fpolicy_handler.http")
    def test_max_retries_exhausted(self, mock_http):
        mock_error = MagicMock()
        mock_error.status = 500
        mock_error.data = b"Error"
        mock_http.request.return_value = mock_error

        with patch("fpolicy_handler.time.sleep"):
            result = fpolicy_handler._send_otlp_payload({"resourceLogs": []})

        assert result is False
        assert mock_http.request.call_count == 3


class TestAuthModeHeader:
    """Tests for AUTH_MODE="header" and EXTRA_HEADERS_JSON (generic
    custom-header auth support, needed for vendors like Mackerel with a
    non-Bearer/Basic auth header, e.g. "Mackerel-Api-Key")."""

    @patch("fpolicy_handler.http")
    @patch("fpolicy_handler.secrets_client")
    def test_header_auth_mode_uses_custom_header_name(self, mock_secrets, mock_http, monkeypatch):
        fpolicy_handler._api_key_cache = None
        monkeypatch.setattr(
            fpolicy_handler, "API_KEY_SECRET_ARN",
            "arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:test",
        )
        monkeypatch.setattr(fpolicy_handler, "AUTH_MODE", "header")
        monkeypatch.setattr(fpolicy_handler, "AUTH_HEADER_NAME", "Mackerel-Api-Key")

        mock_secrets.get_secret_value.return_value = {
            "SecretString": json.dumps({"api_key": "write-scoped-key"})
        }
        mock_response = MagicMock()
        mock_response.status = 200
        mock_http.request.return_value = mock_response

        fpolicy_handler.lambda_handler(SAMPLE_FILE_CREATE_EVENT, None)

        _, kwargs = mock_http.request.call_args
        sent_headers = kwargs["headers"]
        assert sent_headers["Mackerel-Api-Key"] == "write-scoped-key"
        assert "Authorization" not in sent_headers

    @patch("fpolicy_handler.http")
    @patch("fpolicy_handler.secrets_client")
    def test_extra_headers_json_merged(self, mock_secrets, mock_http, monkeypatch):
        fpolicy_handler._api_key_cache = None
        monkeypatch.setattr(
            fpolicy_handler, "API_KEY_SECRET_ARN",
            "arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:test",
        )
        monkeypatch.setattr(fpolicy_handler, "AUTH_MODE", "header")
        monkeypatch.setattr(fpolicy_handler, "AUTH_HEADER_NAME", "Mackerel-Api-Key")
        monkeypatch.setattr(fpolicy_handler, "EXTRA_HEADERS_JSON", '{"Accept": "*/*"}')

        mock_secrets.get_secret_value.return_value = {
            "SecretString": json.dumps({"api_key": "write-scoped-key"})
        }
        mock_response = MagicMock()
        mock_response.status = 200
        mock_http.request.return_value = mock_response

        fpolicy_handler.lambda_handler(SAMPLE_FILE_CREATE_EVENT, None)

        _, kwargs = mock_http.request.call_args
        sent_headers = kwargs["headers"]
        assert sent_headers["Mackerel-Api-Key"] == "write-scoped-key"
        assert sent_headers["Accept"] == "*/*"

    @patch("fpolicy_handler.http")
    def test_extra_headers_json_works_without_api_key_secret(self, mock_http, monkeypatch):
        monkeypatch.setattr(fpolicy_handler, "EXTRA_HEADERS_JSON", '{"Accept": "*/*"}')
        mock_response = MagicMock()
        mock_response.status = 200
        mock_http.request.return_value = mock_response

        fpolicy_handler.lambda_handler(SAMPLE_FILE_CREATE_EVENT, None)

        _, kwargs = mock_http.request.call_args
        assert kwargs["headers"]["Accept"] == "*/*"

    @patch("fpolicy_handler.http")
    def test_malformed_extra_headers_json_ignored_not_fatal(self, mock_http, monkeypatch):
        monkeypatch.setattr(fpolicy_handler, "EXTRA_HEADERS_JSON", "{not valid json")
        mock_response = MagicMock()
        mock_response.status = 200
        mock_http.request.return_value = mock_response

        result = fpolicy_handler.lambda_handler(SAMPLE_FILE_CREATE_EVENT, None)

        assert result["statusCode"] == 200
        _, kwargs = mock_http.request.call_args
        assert kwargs["headers"] == {"Content-Type": "application/json"}


class TestOtlpContentTypeProtobuf:
    """Tests for OTLP_CONTENT_TYPE="protobuf" (needed for vendors like
    Mackerel whose OTLP/HTTP log endpoint rejects OTLP/JSON bodies)."""

    @patch("fpolicy_handler.http")
    def test_protobuf_content_type_sets_correct_header_and_body_encoding(
        self, mock_http, monkeypatch,
    ):
        monkeypatch.setattr(fpolicy_handler, "OTLP_CONTENT_TYPE", "protobuf")
        mock_response = MagicMock()
        mock_response.status = 200
        mock_http.request.return_value = mock_response

        result = fpolicy_handler.lambda_handler(SAMPLE_FILE_CREATE_EVENT, None)

        assert result["statusCode"] == 200
        _, kwargs = mock_http.request.call_args
        assert kwargs["headers"]["Content-Type"] == "application/x-protobuf"
        body = kwargs["body"]
        assert isinstance(body, bytes)
        with pytest.raises((json.JSONDecodeError, UnicodeDecodeError)):
            json.loads(body)

    @patch("fpolicy_handler.http")
    def test_default_content_type_is_json_unaffected(self, mock_http):
        """Regression guard: default OTLP_CONTENT_TYPE must behave exactly
        as before (OTLP/JSON body)."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_http.request.return_value = mock_response

        fpolicy_handler.lambda_handler(SAMPLE_FILE_CREATE_EVENT, None)

        _, kwargs = mock_http.request.call_args
        assert kwargs["headers"]["Content-Type"] == "application/json"
        json.loads(kwargs["body"])

    @patch("fpolicy_handler.http")
    @patch("fpolicy_handler.secrets_client")
    def test_protobuf_content_type_combined_with_header_auth(
        self, mock_secrets, mock_http, monkeypatch,
    ):
        """The combination actually needed for Mackerel's direct-send path:
        AUTH_MODE=header + OTLP_CONTENT_TYPE=protobuf together."""
        fpolicy_handler._api_key_cache = None
        monkeypatch.setattr(
            fpolicy_handler, "API_KEY_SECRET_ARN",
            "arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:test",
        )
        monkeypatch.setattr(fpolicy_handler, "AUTH_MODE", "header")
        monkeypatch.setattr(fpolicy_handler, "AUTH_HEADER_NAME", "Mackerel-Api-Key")
        monkeypatch.setattr(fpolicy_handler, "EXTRA_HEADERS_JSON", '{"Accept": "*/*"}')
        monkeypatch.setattr(fpolicy_handler, "OTLP_CONTENT_TYPE", "protobuf")

        mock_secrets.get_secret_value.return_value = {
            "SecretString": json.dumps({"api_key": "write-scoped-key"})
        }
        mock_response = MagicMock()
        mock_response.status = 200
        mock_http.request.return_value = mock_response

        result = fpolicy_handler.lambda_handler(SAMPLE_FILE_CREATE_EVENT, None)

        assert result["statusCode"] == 200
        _, kwargs = mock_http.request.call_args
        assert kwargs["headers"]["Mackerel-Api-Key"] == "write-scoped-key"
        assert kwargs["headers"]["Accept"] == "*/*"
        assert kwargs["headers"]["Content-Type"] == "application/x-protobuf"
        assert isinstance(kwargs["body"], bytes)


class TestLambdaHandler:
    """Tests for the FPolicy Lambda handler entry point."""

    @patch("fpolicy_handler.http")
    def test_file_create_event(self, mock_http):
        mock_response = MagicMock()
        mock_response.status = 200
        mock_http.request.return_value = mock_response

        result = fpolicy_handler.lambda_handler(SAMPLE_FILE_CREATE_EVENT, None)

        assert result["statusCode"] == 200
        assert result["body"]["status"] == "ok"
        assert result["body"]["operation"] == "create"
        assert result["body"]["otlp_delivered"] is True

    @patch("fpolicy_handler.http")
    def test_file_write_event(self, mock_http):
        mock_response = MagicMock()
        mock_response.status = 200
        mock_http.request.return_value = mock_response

        result = fpolicy_handler.lambda_handler(SAMPLE_FILE_WRITE_EVENT, None)

        assert result["statusCode"] == 200
        assert result["body"]["operation"] == "write"

    @patch("fpolicy_handler.http")
    def test_file_rename_event(self, mock_http):
        mock_response = MagicMock()
        mock_response.status = 200
        mock_http.request.return_value = mock_response

        result = fpolicy_handler.lambda_handler(SAMPLE_FILE_RENAME_EVENT, None)

        assert result["statusCode"] == 200
        assert result["body"]["operation"] == "rename"

    @patch("fpolicy_handler.http")
    def test_file_delete_event(self, mock_http):
        mock_response = MagicMock()
        mock_response.status = 200
        mock_http.request.return_value = mock_response

        result = fpolicy_handler.lambda_handler(SAMPLE_FILE_DELETE_EVENT, None)

        assert result["statusCode"] == 200
        assert result["body"]["operation"] == "delete"

    @patch("fpolicy_handler.http")
    def test_otlp_delivery_failure(self, mock_http):
        mock_response = MagicMock()
        mock_response.status = 500
        mock_response.data = b"Error"
        mock_http.request.return_value = mock_response

        with patch("fpolicy_handler.time.sleep"):
            result = fpolicy_handler.lambda_handler(SAMPLE_FILE_CREATE_EVENT, None)

        assert result["statusCode"] == 502
        assert result["body"]["status"] == "error"

    @patch("fpolicy_handler.http")
    def test_direct_detail_event(self, mock_http):
        """Handler works with direct detail dict (no EventBridge wrapper)."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_http.request.return_value = mock_response

        direct_event = {
            "operation": "create",
            "file_path": "/vol/data/test.txt",
            "user": "admin",
            "client_ip": "10.0.1.1",
            "timestamp": "2026-01-15T12:00:00Z",
        }

        result = fpolicy_handler.lambda_handler(direct_event, None)
        assert result["statusCode"] == 200


# ─── SQS event source mapping (primary FPolicy path) ───────────────────────


def _sqs_record(message_id, detail):
    """Build one SQS event source mapping record carrying an FPolicy event."""
    return {
        "messageId": message_id,
        "eventSource": "aws:sqs",
        "body": json.dumps(detail),
    }


SAMPLE_SQS_DETAIL_A = {
    "operation_type": "create",
    "file_path": "/vol/data/a.txt",
    "user": "admin@corp.local",
    "client_ip": "198.51.100.10",
    "svm": "svm-prod-01",
    "protocol": "cifs",
    "timestamp": "2026-01-15T12:00:01Z",
}

SAMPLE_SQS_DETAIL_B = {
    "operation_type": "write",
    "file_path": "/vol/data/b.txt",
    "user": "admin@corp.local",
    "client_ip": "198.51.100.11",
    "svm": "svm-prod-01",
    "protocol": "nfs",
    "timestamp": "2026-01-15T12:00:02Z",
}


class TestSqsBatchHandling:
    """Tests for the SQS → Lambda → OTLP path.

    Before partial batch failure support, an SQS batch fell through to the
    EventBridge branch: ``event.get("detail", event)`` returned the whole SQS
    envelope, so the handler shipped one meaningless record with
    ``operation=unknown`` and silently dropped every real FPolicy event in the
    batch. These tests pin the corrected behaviour.
    """

    @patch("fpolicy_handler.http")
    def test_sqs_batch_returns_batch_item_failures_shape(self, mock_http):
        """An SQS batch returns the ReportBatchItemFailures response shape."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_http.request.return_value = mock_response

        event = {
            "Records": [
                _sqs_record("m1", SAMPLE_SQS_DETAIL_A),
                _sqs_record("m2", SAMPLE_SQS_DETAIL_B),
            ]
        }

        result = fpolicy_handler.lambda_handler(event, None)

        assert result == {"batchItemFailures": []}
        assert "statusCode" not in result

    @patch("fpolicy_handler.http")
    def test_every_sqs_message_is_shipped(self, mock_http):
        """Each SQS message produces its own OTLP request — none are dropped."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_http.request.return_value = mock_response

        event = {
            "Records": [
                _sqs_record("m1", SAMPLE_SQS_DETAIL_A),
                _sqs_record("m2", SAMPLE_SQS_DETAIL_B),
            ]
        }

        fpolicy_handler.lambda_handler(event, None)

        assert mock_http.request.call_count == 2

    @patch("fpolicy_handler.http")
    def test_sqs_message_fields_reach_otlp_attributes(self, mock_http):
        """The FPolicy fields from the SQS body land in OTLP attributes.

        Guards against regressing to the old behaviour where the SQS envelope
        was shipped and every field came out as "unknown".
        """
        mock_response = MagicMock()
        mock_response.status = 200
        mock_http.request.return_value = mock_response

        event = {"Records": [_sqs_record("m1", SAMPLE_SQS_DETAIL_A)]}

        fpolicy_handler.lambda_handler(event, None)

        sent = json.loads(mock_http.request.call_args[1]["body"])
        records = sent["resourceLogs"][0]["scopeLogs"][0]["logRecords"]
        attrs = {a["key"]: a["value"]["stringValue"] for a in records[0]["attributes"]}
        assert attrs["operation_type"] == "create"
        assert attrs["file_path"] == "/vol/data/a.txt"
        assert attrs["client_ip"] == "198.51.100.10"

    @patch("fpolicy_handler.http")
    def test_otlp_failure_reports_only_the_failing_message(self, mock_http):
        """A per-message delivery failure reports just that message."""
        ok = MagicMock()
        ok.status = 200
        bad = MagicMock()
        bad.status = 500
        bad.data = b"Error"
        # First message succeeds; the second exhausts its retries.
        mock_http.request.side_effect = [ok, bad, bad, bad]

        event = {
            "Records": [
                _sqs_record("m1", SAMPLE_SQS_DETAIL_A),
                _sqs_record("m2", SAMPLE_SQS_DETAIL_B),
            ]
        }

        with patch("fpolicy_handler.time.sleep"):
            result = fpolicy_handler.lambda_handler(event, None)

        assert result["batchItemFailures"] == [{"itemIdentifier": "m2"}]

    @patch("fpolicy_handler.http")
    def test_unparseable_message_reported_for_dlq_redrive(self, mock_http):
        """A malformed body is reported so the redrive policy sends it to the DLQ."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_http.request.return_value = mock_response

        event = {
            "Records": [
                {"messageId": "bad", "eventSource": "aws:sqs", "body": "not json {{"},
                _sqs_record("good", SAMPLE_SQS_DETAIL_A),
            ]
        }

        result = fpolicy_handler.lambda_handler(event, None)

        assert result["batchItemFailures"] == [{"itemIdentifier": "bad"}]
        # The well-formed message was still delivered.
        assert mock_http.request.call_count == 1

    @patch("fpolicy_handler.http")
    def test_non_object_body_reported_as_failure(self, mock_http):
        """A JSON body that is not an object cannot be mapped, so it is reported."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_http.request.return_value = mock_response

        event = {
            "Records": [
                {"messageId": "arr", "eventSource": "aws:sqs", "body": "[1, 2, 3]"}
            ]
        }

        result = fpolicy_handler.lambda_handler(event, None)

        assert result["batchItemFailures"] == [{"itemIdentifier": "arr"}]
        mock_http.request.assert_not_called()

    @patch("fpolicy_handler.http")
    def test_eventbridge_event_keeps_status_code_contract(self, mock_http):
        """Non-SQS payloads still return the statusCode response shape."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_http.request.return_value = mock_response

        result = fpolicy_handler.lambda_handler(SAMPLE_FILE_CREATE_EVENT, None)

        assert result["statusCode"] == 200
        assert "batchItemFailures" not in result

    @patch("fpolicy_handler.http")
    def test_empty_records_list_uses_eventbridge_path(self, mock_http):
        """An empty Records list is not an SQS batch, so it takes the other path."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_http.request.return_value = mock_response

        result = fpolicy_handler.lambda_handler({"Records": []}, None)

        assert "batchItemFailures" not in result
