"""Unit tests for Grafana Loki log shipper."""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "lambda"))
sys.modules.pop("handler", None)
import handler


class TestFormatForLoki:
    def test_basic_structure(self, sample_logs):
        logs = [json.loads(l) for l in sample_logs.strip().split("\n")]
        result = handler._format_for_loki(logs, "audit/test.json")
        assert "streams" in result
        assert len(result["streams"]) == 1  # same SVM
        stream = result["streams"][0]
        assert stream["stream"]["job"] == "fsxn-audit"
        assert stream["stream"]["svm"] == "svm-prod-01"
        assert len(stream["values"]) == 2

    def test_multiple_svms(self):
        logs = [
            {"SVMName": "svm-a", "timestamp": "2026-01-15T12:00:01Z", "msg": "a"},
            {"SVMName": "svm-b", "timestamp": "2026-01-15T12:00:02Z", "msg": "b"},
        ]
        result = handler._format_for_loki(logs, "test.json")
        assert len(result["streams"]) == 2

    def test_values_sorted_by_timestamp(self):
        logs = [
            {"SVMName": "svm", "timestamp": "2026-01-15T12:00:05Z"},
            {"SVMName": "svm", "timestamp": "2026-01-15T12:00:01Z"},
        ]
        result = handler._format_for_loki(logs, "test.json")
        values = result["streams"][0]["values"]
        assert int(values[0][0]) < int(values[1][0])


class TestFormatForOtlpDirect:
    """Tests for the primary OTLP formatting path."""

    def test_basic_structure(self, sample_logs):
        logs = [json.loads(l) for l in sample_logs.strip().split("\n")]
        result = handler._format_for_otlp_direct(logs, "audit/test.json")
        assert "resourceLogs" in result
        assert len(result["resourceLogs"]) == 1  # same SVM

        rl = result["resourceLogs"][0]
        # Check resource attributes
        attrs = {a["key"]: a["value"]["stringValue"] for a in rl["resource"]["attributes"]}
        assert attrs["service.name"] == "fsxn-audit"
        assert attrs["source"] == "fsxn-ontap"
        assert attrs["svm"] == "svm-prod-01"
        assert attrs["s3_key"] == "audit/test.json"

        # Check log records
        log_records = rl["scopeLogs"][0]["logRecords"]
        assert len(log_records) == 2

    def test_multiple_svms(self):
        logs = [
            {"SVMName": "svm-a", "timestamp": "2026-01-15T12:00:01Z", "msg": "a"},
            {"SVMName": "svm-b", "timestamp": "2026-01-15T12:00:02Z", "msg": "b"},
        ]
        result = handler._format_for_otlp_direct(logs, "test.json")
        assert len(result["resourceLogs"]) == 2

    def test_log_record_has_body_and_attributes(self):
        logs = [
            {"SVMName": "svm", "timestamp": "2026-01-15T12:00:01Z",
             "Operation": "ReadData", "UserName": "admin"},
        ]
        result = handler._format_for_otlp_direct(logs, "test.json")
        record = result["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]

        # Body is JSON string of the full log
        body = json.loads(record["body"]["stringValue"])
        assert body["Operation"] == "ReadData"

        # Attributes include log fields (excluding timestamp)
        attr_keys = [a["key"] for a in record["attributes"]]
        assert "Operation" in attr_keys
        assert "UserName" in attr_keys
        assert "SVMName" in attr_keys
        assert "timestamp" not in attr_keys

    def test_timestamp_conversion(self):
        logs = [
            {"SVMName": "svm", "timestamp": "2026-01-15T12:00:01Z"},
        ]
        result = handler._format_for_otlp_direct(logs, "test.json")
        record = result["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]
        ts_ns = int(record["timeUnixNano"])
        # Should be a valid nanosecond timestamp (year 2026)
        assert ts_ns > 1_700_000_000_000_000_000


class TestShipLogs:
    """Tests for the unified _ship_logs entry point."""

    @patch("handler._send_otlp")
    @patch("handler._format_for_otlp_direct")
    def test_otlp_path_when_use_otlp(self, mock_format, mock_send):
        """When USE_OTLP is True, uses OTLP direct path."""
        mock_format.return_value = {"resourceLogs": []}
        mock_send.return_value = True

        with patch.object(handler, "USE_OTLP", True):
            result = handler._ship_logs(
                [{"SVMName": "svm", "msg": "test"}], "key.json", "Basic abc"
            )
        assert result == 1
        mock_format.assert_called_once()
        mock_send.assert_called_once()

    @patch("handler._send_loki_push")
    @patch("handler._format_for_loki")
    def test_loki_path_when_not_otlp(self, mock_format, mock_send):
        """When USE_OTLP is False, uses Loki Push fallback."""
        mock_format.return_value = {"streams": []}
        mock_send.return_value = True

        with patch.object(handler, "USE_OTLP", False):
            result = handler._ship_logs(
                [{"SVMName": "svm", "msg": "test"}], "key.json", "Basic abc"
            )
        assert result == 1
        mock_format.assert_called_once()
        mock_send.assert_called_once()

    def test_empty_logs_returns_zero(self):
        result = handler._ship_logs([], "key.json", "Basic abc")
        assert result == 0

    @patch("handler._send_otlp")
    @patch("handler._format_for_otlp_direct")
    def test_returns_zero_on_send_failure(self, mock_format, mock_send):
        mock_format.return_value = {"resourceLogs": []}
        mock_send.return_value = False

        with patch.object(handler, "USE_OTLP", True):
            result = handler._ship_logs(
                [{"SVMName": "svm", "msg": "test"}], "key.json", "Basic abc"
            )
        assert result == 0


class TestSendLokiPush:
    @patch("handler.http")
    def test_success_204(self, mock_http):
        mock_http.request.return_value = MagicMock(status=204)
        assert handler._send_loki_push({"streams": []}, "Basic abc") is True

    @patch("handler.http")
    def test_gzip_and_auth(self, mock_http):
        mock_http.request.return_value = MagicMock(status=204)
        handler._send_loki_push({"streams": []}, "Basic abc123")
        headers = mock_http.request.call_args[1]["headers"]
        assert headers["Content-Encoding"] == "gzip"
        assert headers["Authorization"] == "Basic abc123"

    @patch("handler.http")
    def test_retry_on_500(self, mock_http):
        mock_500 = MagicMock(status=500, data=b"error")
        mock_ok = MagicMock(status=204)
        mock_http.request.side_effect = [mock_500, mock_ok]
        with patch("handler.time.sleep"):
            assert handler._send_loki_push({"streams": []}, "Basic x") is True


class TestSchedulerMode:
    """Tests for EventBridge Scheduler polling mode."""

    @patch("handler.ssm_client")
    @patch("handler.s3_client")
    @patch("handler.secrets_client")
    @patch("handler.http")
    def test_scheduler_no_new_files(self, mock_http, mock_secrets, mock_s3, mock_ssm):
        """When no new files exist, return early with zero counts."""
        mock_secrets.get_secret_value.return_value = {
            "SecretString": json.dumps({"instance_id": "123", "api_key": "key"})
        }
        mock_ssm.get_parameter.return_value = {
            "Parameter": {"Value": "audit/svm-prod-01/2026/01/15/last.json"}
        }
        mock_s3.list_objects_v2.return_value = {"Contents": [], "IsTruncated": False}

        # Reset auth cache
        handler._auth_header_cache = None

        event = {"source": "scheduler", "prefix": "audit/svm-prod-01/"}
        result = handler.lambda_handler(event, None)

        assert result["statusCode"] == 200
        assert result["body"]["new_files"] == 0

    @patch("handler.ssm_client")
    @patch("handler.s3_client")
    @patch("handler.secrets_client")
    @patch("handler.http")
    def test_scheduler_processes_new_files(self, mock_http, mock_secrets, mock_s3, mock_ssm):
        """Scheduler mode processes new files and updates checkpoint."""
        mock_secrets.get_secret_value.return_value = {
            "SecretString": json.dumps({"instance_id": "123", "api_key": "key"})
        }
        mock_ssm.get_parameter.return_value = {
            "Parameter": {"Value": "__INIT__"}
        }
        mock_ssm.put_parameter.return_value = {}

        log_content = json.dumps(
            {"timestamp": "2026-01-15T12:00:01Z", "SVMName": "svm-prod-01", "msg": "test"}
        ).encode()

        mock_s3.list_objects_v2.return_value = {
            "Contents": [
                {"Key": "audit/svm-prod-01/2026/01/15/file1.json"},
                {"Key": "audit/svm-prod-01/2026/01/15/file2.json"},
            ],
            "IsTruncated": False,
        }

        mock_body = MagicMock()
        mock_body.read.return_value = log_content
        mock_s3.get_object.return_value = {"Body": mock_body}

        mock_http.request.return_value = MagicMock(status=204)

        handler._auth_header_cache = None

        event = {"source": "scheduler", "prefix": "audit/svm-prod-01/"}
        result = handler.lambda_handler(event, None)

        assert result["statusCode"] == 200
        assert result["body"]["new_files"] == 2
        assert result["body"]["processed_files"] == 2

        # Verify checkpoint was updated to last file
        mock_ssm.put_parameter.assert_called_once_with(
            Name="/fsxn-grafana/test-stack/last-processed-key",
            Value="audit/svm-prod-01/2026/01/15/file2.json",
            Type="String",
            Overwrite=True,
        )

    @patch("handler.ssm_client")
    @patch("handler.s3_client")
    @patch("handler.secrets_client")
    @patch("handler.http")
    def test_scheduler_stops_on_error(self, mock_http, mock_secrets, mock_s3, mock_ssm):
        """Scheduler mode stops processing on error to avoid checkpoint gaps."""
        mock_secrets.get_secret_value.return_value = {
            "SecretString": json.dumps({"instance_id": "123", "api_key": "key"})
        }
        mock_ssm.get_parameter.return_value = {"Parameter": {"Value": "__INIT__"}}
        mock_ssm.put_parameter.return_value = {}

        mock_s3.list_objects_v2.return_value = {
            "Contents": [
                {"Key": "audit/file1.json"},
                {"Key": "audit/file2.json"},
            ],
            "IsTruncated": False,
        }

        # First file succeeds, second file fails
        log_content = json.dumps({"timestamp": "2026-01-15T12:00:01Z", "SVMName": "svm", "msg": "ok"}).encode()
        mock_body_ok = MagicMock()
        mock_body_ok.read.return_value = log_content
        mock_s3.get_object.side_effect = [
            {"Body": mock_body_ok},
            Exception("S3 read error"),
        ]

        mock_http.request.return_value = MagicMock(status=204)

        handler._auth_header_cache = None

        event = {"source": "scheduler", "prefix": "audit/"}
        result = handler.lambda_handler(event, None)

        assert result["statusCode"] == 207
        assert len(result["body"]["errors"]) == 1
        # Checkpoint should be updated to file1 (last successful)
        mock_ssm.put_parameter.assert_called_once_with(
            Name="/fsxn-grafana/test-stack/last-processed-key",
            Value="audit/file1.json",
            Type="String",
            Overwrite=True,
        )


class TestS3EventMode:
    """Tests for backward-compatible S3 event mode."""

    @patch("handler.ssm_client")
    @patch("handler.s3_client")
    @patch("handler.secrets_client")
    @patch("handler.http")
    def test_s3_event_still_works(self, mock_http, mock_secrets, mock_s3, mock_ssm):
        """S3 event format still works for manual testing."""
        mock_secrets.get_secret_value.return_value = {
            "SecretString": json.dumps({"instance_id": "123", "api_key": "key"})
        }

        log_content = json.dumps(
            {"timestamp": "2026-01-15T12:00:01Z", "SVMName": "svm-prod-01", "msg": "test"}
        ).encode()
        mock_body = MagicMock()
        mock_body.read.return_value = log_content
        mock_s3.get_object.return_value = {"Body": mock_body}

        mock_http.request.return_value = MagicMock(status=204)

        handler._auth_header_cache = None

        event = {
            "Records": [{
                "s3": {
                    "bucket": {"name": "my-bucket"},
                    "object": {"key": "audit/svm-prod-01/2026/01/15/audit.json"},
                }
            }]
        }
        result = handler.lambda_handler(event, None)

        assert result["statusCode"] == 200
        assert result["body"]["total_logs"] == 1


class TestCheckpoint:
    """Tests for SSM Parameter Store checkpoint management."""

    @patch("handler.ssm_client")
    def test_get_checkpoint_init_value(self, mock_ssm):
        """__INIT__ value is treated as empty (no checkpoint)."""
        mock_ssm.get_parameter.return_value = {"Parameter": {"Value": "__INIT__"}}
        assert handler._get_checkpoint() == ""

    @patch("handler.ssm_client")
    def test_get_checkpoint_returns_key(self, mock_ssm):
        """Normal checkpoint value is returned as-is."""
        mock_ssm.get_parameter.return_value = {
            "Parameter": {"Value": "audit/svm-prod-01/2026/01/15/file.json"}
        }
        assert handler._get_checkpoint() == "audit/svm-prod-01/2026/01/15/file.json"

    @patch("handler.ssm_client")
    def test_get_checkpoint_not_found(self, mock_ssm):
        """ParameterNotFound returns empty string (caught by generic Exception)."""
        from botocore.exceptions import ClientError
        mock_ssm.get_parameter.side_effect = ClientError(
            {"Error": {"Code": "ParameterNotFound", "Message": "not found"}},
            "GetParameter",
        )
        # Falls into the generic Exception handler, returns ""
        assert handler._get_checkpoint() == ""


class TestListNewKeys:
    """Tests for S3 listing and filtering."""

    @patch("handler.s3_client")
    def test_list_skips_directory_markers(self, mock_s3):
        mock_s3.list_objects_v2.return_value = {
            "Contents": [
                {"Key": "audit/svm-prod-01/"},
                {"Key": "audit/svm-prod-01/file1.json"},
            ],
            "IsTruncated": False,
        }
        keys = handler._list_new_keys("arn:aws:s3:ap-northeast-1:123456789012:accesspoint/ap", "audit/", "")
        assert keys == ["audit/svm-prod-01/file1.json"]

    @patch("handler.s3_client")
    def test_list_uses_start_after(self, mock_s3):
        mock_s3.list_objects_v2.return_value = {
            "Contents": [{"Key": "audit/file3.json"}],
            "IsTruncated": False,
        }
        handler._list_new_keys("arn:aws:s3:ap-northeast-1:123456789012:accesspoint/ap", "audit/", "audit/file2.json")
        call_kwargs = mock_s3.list_objects_v2.call_args[1]
        assert call_kwargs["StartAfter"] == "audit/file2.json"

    @patch("handler.s3_client")
    def test_list_handles_pagination(self, mock_s3):
        mock_s3.list_objects_v2.side_effect = [
            {
                "Contents": [{"Key": "audit/file1.json"}],
                "IsTruncated": True,
                "NextContinuationToken": "token1",
            },
            {
                "Contents": [{"Key": "audit/file2.json"}],
                "IsTruncated": False,
            },
        ]
        keys = handler._list_new_keys("arn:aws:s3:ap-northeast-1:123456789012:accesspoint/ap", "audit/", "")
        assert keys == ["audit/file1.json", "audit/file2.json"]


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
