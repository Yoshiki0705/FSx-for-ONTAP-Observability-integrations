"""Unit tests for Datadog log shipper Lambda handler."""

import gzip
import json
import sys
from io import BytesIO
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
                {
                    "s3": {
                        "bucket": {"name": "bucket1"},
                        "object": {"key": "key1.json"},
                    }
                },
                {
                    "s3": {
                        "bucket": {"name": "bucket2"},
                        "object": {"key": "key2.json"},
                    }
                },
            ]
        }
        records = handler._extract_s3_records(event)
        assert len(records) == 2


class TestParseJsonLogs:
    """Tests for _parse_json_logs function."""

    def test_newline_delimited_json(self, sample_json_audit_logs):
        events = handler._parse_json_logs(sample_json_audit_logs)
        assert len(events) == 3
        assert events[0]["EventID"] == "4663"
        assert events[0]["SVMName"] == "svm-prod-01"

    def test_json_array(self):
        logs = json.dumps([{"event": "test1"}, {"event": "test2"}])
        events = handler._parse_json_logs(logs)
        assert len(events) == 2

    def test_single_json_object(self):
        log = json.dumps({"event": "single"})
        events = handler._parse_json_logs(log)
        assert len(events) == 1

    def test_empty_input(self):
        events = handler._parse_json_logs("")
        assert len(events) == 0

    def test_invalid_json_lines_skipped(self):
        data = '{"valid": true}\nnot json\n{"also_valid": true}'
        events = handler._parse_json_logs(data)
        assert len(events) == 2


class TestFormatForDatadog:
    """Tests for _format_for_datadog function."""

    def test_basic_formatting(self):
        logs = [
            {
                "timestamp": "2026-01-15T12:00:01Z",
                "EventID": "4663",
                "SVMName": "svm-prod-01",
                "UserName": "admin@corp.local",
                "ClientIP": "10.0.1.50",
                "Operation": "ReadData",
                "ObjectName": "/vol/data/file.txt",
                "Result": "Success",
            }
        ]
        result = handler._format_for_datadog(logs, "audit/test.json")

        assert len(result) == 1
        dd_log = result[0]
        assert dd_log["ddsource"] == "fsxn"
        assert dd_log["service"] == "ontap-audit"
        assert dd_log["hostname"] == "svm-prod-01"
        assert dd_log["date"] == "2026-01-15T12:00:01Z"
        assert "source:fsxn" in dd_log["ddtags"]
        assert dd_log["attributes"]["event_type"] == "4663"
        assert dd_log["attributes"]["user"] == "admin@corp.local"
        assert dd_log["attributes"]["operation"] == "ReadData"

    def test_missing_fields(self):
        logs = [{"message": "raw log line"}]
        result = handler._format_for_datadog(logs, "test.json")

        assert len(result) == 1
        assert result[0]["message"] == "raw log line"
        assert result[0]["hostname"] == "fsxn-ontap"

    def test_empty_logs(self):
        result = handler._format_for_datadog([], "test.json")
        assert len(result) == 0


class TestCreateBatches:
    """Tests for _create_batches function."""

    def test_single_batch(self):
        logs = [{"message": f"log {i}"} for i in range(10)]
        batches = handler._create_batches(logs)
        assert len(batches) == 1
        assert len(batches[0]) == 10

    def test_max_items_split(self):
        logs = [{"message": f"log {i}"} for i in range(1500)]
        batches = handler._create_batches(logs)
        assert len(batches) == 2
        assert len(batches[0]) == 1000
        assert len(batches[1]) == 500

    def test_size_limit_split(self):
        # Create logs that exceed 5MB total
        large_message = "x" * 10000
        logs = [{"message": large_message} for _ in range(600)]
        batches = handler._create_batches(logs)
        assert len(batches) > 1

    def test_empty_input(self):
        batches = handler._create_batches([])
        assert len(batches) == 0


class TestSendBatch:
    """Tests for _send_batch function."""

    @patch("handler.http")
    def test_successful_send(self, mock_http):
        mock_response = MagicMock()
        mock_response.status = 202
        mock_http.request.return_value = mock_response

        logs = [{"message": "test log"}]
        result = handler._send_batch(logs, "test-api-key")

        assert result is True
        mock_http.request.assert_called_once()

        # Verify headers
        call_kwargs = mock_http.request.call_args
        headers = call_kwargs[1]["headers"] if "headers" in call_kwargs[1] else call_kwargs[0][3]
        assert headers["DD-API-KEY"] == "test-api-key"
        assert headers["Content-Type"] == "application/json"

    @patch("handler.http")
    def test_retry_on_server_error(self, mock_http):
        mock_error = MagicMock()
        mock_error.status = 500
        mock_error.data = b"Internal Server Error"

        mock_success = MagicMock()
        mock_success.status = 202

        mock_http.request.side_effect = [mock_error, mock_success]

        with patch("handler.time.sleep"):
            result = handler._send_batch([{"message": "test"}], "key")

        assert result is True
        assert mock_http.request.call_count == 2

    @patch("handler.http")
    def test_no_retry_on_client_error(self, mock_http):
        mock_response = MagicMock()
        mock_response.status = 403
        mock_response.data = b"Forbidden"
        mock_http.request.return_value = mock_response

        result = handler._send_batch([{"message": "test"}], "bad-key")

        assert result is False
        assert mock_http.request.call_count == 1

    @patch("handler.http")
    def test_retry_on_rate_limit(self, mock_http):
        mock_rate_limit = MagicMock()
        mock_rate_limit.status = 429
        mock_rate_limit.headers = {"Retry-After": "1"}

        mock_success = MagicMock()
        mock_success.status = 202

        mock_http.request.side_effect = [mock_rate_limit, mock_success]

        with patch("handler.time.sleep"):
            result = handler._send_batch([{"message": "test"}], "key")

        assert result is True

    @patch("handler.http")
    def test_max_retries_exhausted(self, mock_http):
        mock_error = MagicMock()
        mock_error.status = 500
        mock_error.data = b"Error"
        mock_http.request.return_value = mock_error

        with patch("handler.time.sleep"):
            result = handler._send_batch([{"message": "test"}], "key")

        assert result is False
        assert mock_http.request.call_count == 3


class TestGetApiKey:
    """Tests for get_api_key function."""

    def test_json_format(self, mock_boto3_clients):
        # Reset cache
        handler._api_key_cache = None
        key = handler.get_api_key()
        assert key == "test-dd-api-key-12345"

    def test_plain_string_format(self, mock_boto3_clients):
        handler._api_key_cache = None
        mock_boto3_clients["secrets"].get_secret_value.return_value = {
            "SecretString": "plain-api-key-67890"
        }
        key = handler.get_api_key()
        assert key == "plain-api-key-67890"

    def test_caching(self, mock_boto3_clients):
        handler._api_key_cache = None
        handler.get_api_key()
        handler.get_api_key()
        # Should only call Secrets Manager once due to caching
        mock_boto3_clients["secrets"].get_secret_value.assert_called_once()


class TestLambdaHandler:
    """Integration tests for the full Lambda handler."""

    @patch("handler.http")
    @patch("handler.s3_client")
    @patch("handler.secrets_client")
    def test_full_flow(self, mock_secrets, mock_s3, mock_http, sample_s3_event, sample_json_audit_logs):
        # Reset cache
        handler._api_key_cache = None

        # Mock Secrets Manager
        mock_secrets.get_secret_value.return_value = {
            "SecretString": json.dumps({"api_key": "test-key"})
        }

        # Mock S3
        mock_body = MagicMock()
        mock_body.read.return_value = sample_json_audit_logs.encode("utf-8")
        mock_s3.get_object.return_value = {"Body": mock_body}

        # Mock HTTP (Datadog API)
        mock_response = MagicMock()
        mock_response.status = 202
        mock_http.request.return_value = mock_response

        # Execute
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
            "SecretString": "test-key"
        }

        # S3 raises an error
        from botocore.exceptions import ClientError
        mock_s3.get_object.side_effect = ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": "Not found"}},
            "GetObject",
        )

        result = handler.lambda_handler(sample_s3_event, None)

        assert result["statusCode"] == 207
        assert len(result["body"]["errors"]) == 1


class TestSchedulerPolling:
    """Tests for the EventBridge Scheduler polling path.

    This is the production trigger: FSx for ONTAP S3 Access Points do not emit
    S3 Event Notifications, so the shipper discovers new files with
    ListObjectsV2 and tracks progress in an SSM Parameter Store checkpoint.
    """

    SCHEDULER_EVENT = {
        "source": "scheduler",
        "action": "process_audit_logs",
        "prefix": "audit/",
    }

    @staticmethod
    def _audit_body(count: int = 1) -> bytes:
        lines = [
            json.dumps(
                {
                    "timestamp": "2026-08-07T09:00:0%d" % i,
                    "EventID": "4663",
                    "SVMName": "svm-prod-01",
                    "UserName": "jdoe@corp.local",
                    "Operation": "ReadData",
                    "ObjectName": "/vol/data/file%d.txt" % i,
                    "Result": "Success",
                }
            )
            for i in range(count)
        ]
        return "\n".join(lines).encode("utf-8")

    def test_scheduler_ships_new_files_and_advances_checkpoint(self, monkeypatch):
        """New files are listed, shipped, and the checkpoint moves to the last key."""
        monkeypatch.setattr(handler, "CHECKPOINT_PARAM_NAME", "/fsxn-datadog/test/last-key")

        with patch.object(handler, "get_api_key", return_value="k"), \
             patch.object(handler.ssm_client, "get_parameter") as mock_get, \
             patch.object(handler.ssm_client, "put_parameter") as mock_put, \
             patch.object(handler.s3_client, "list_objects_v2") as mock_list, \
             patch.object(handler.s3_client, "get_object") as mock_get_obj, \
             patch.object(handler, "_ship_to_datadog", return_value=1) as mock_ship:

            mock_get.return_value = {"Parameter": {"Value": "audit/2026/08/07/a.json"}}
            mock_list.return_value = {
                "Contents": [
                    {"Key": "audit/2026/08/07/b.json"},
                    {"Key": "audit/2026/08/07/c.json"},
                ],
                "IsTruncated": False,
            }
            # Fresh stream per call — a single BytesIO would be consumed by file 1
            mock_get_obj.side_effect = lambda **kw: {"Body": BytesIO(self._audit_body())}

            result = handler.lambda_handler(self.SCHEDULER_EVENT, None)

        assert result["statusCode"] == 200
        assert result["body"]["new_files"] == 2
        assert mock_ship.call_count == 2
        # StartAfter must use the checkpoint so processed keys are skipped server-side
        assert mock_list.call_args.kwargs["StartAfter"] == "audit/2026/08/07/a.json"
        assert mock_list.call_args.kwargs["Prefix"] == "audit/"
        # Checkpoint advances to the last successfully shipped key
        mock_put.assert_called_once()
        assert mock_put.call_args.kwargs["Value"] == "audit/2026/08/07/c.json"

    def test_scheduler_no_new_files_is_a_noop(self, monkeypatch):
        """An empty listing returns 200 without touching the checkpoint."""
        monkeypatch.setattr(handler, "CHECKPOINT_PARAM_NAME", "/fsxn-datadog/test/last-key")

        with patch.object(handler, "get_api_key", return_value="k"), \
             patch.object(handler.ssm_client, "get_parameter") as mock_get, \
             patch.object(handler.ssm_client, "put_parameter") as mock_put, \
             patch.object(handler.s3_client, "list_objects_v2") as mock_list:

            mock_get.return_value = {"Parameter": {"Value": "audit/z.json"}}
            mock_list.return_value = {"Contents": [], "IsTruncated": False}

            result = handler.lambda_handler(self.SCHEDULER_EVENT, None)

        assert result["statusCode"] == 200
        assert result["body"]["new_files"] == 0
        mock_put.assert_not_called()

    def test_checkpoint_stops_at_first_failure(self, monkeypatch):
        """Processing halts on the first failing file so no file is skipped.

        Advancing past a failed file would permanently drop its audit records.
        """
        monkeypatch.setattr(handler, "CHECKPOINT_PARAM_NAME", "/fsxn-datadog/test/last-key")

        with patch.object(handler, "get_api_key", return_value="k"), \
             patch.object(handler.ssm_client, "get_parameter") as mock_get, \
             patch.object(handler.ssm_client, "put_parameter") as mock_put, \
             patch.object(handler.s3_client, "list_objects_v2") as mock_list, \
             patch.object(handler.s3_client, "get_object") as mock_get_obj, \
             patch.object(handler, "_ship_to_datadog") as mock_ship:

            mock_get.return_value = {"Parameter": {"Value": "__INIT__"}}
            mock_list.return_value = {
                "Contents": [
                    {"Key": "audit/a.json"},
                    {"Key": "audit/b.json"},
                    {"Key": "audit/c.json"},
                ],
                "IsTruncated": False,
            }
            # Fresh stream per call — a single BytesIO would be consumed by file 1
            mock_get_obj.side_effect = lambda **kw: {"Body": BytesIO(self._audit_body())}
            # First file ships, second fails, third must not be attempted
            mock_ship.side_effect = [1, RuntimeError("intake 503"), 1]

            result = handler.lambda_handler(self.SCHEDULER_EVENT, None)

        assert result["statusCode"] == 207
        assert mock_ship.call_count == 2
        assert result["body"]["errors"][0]["key"] == "audit/b.json"
        # Checkpoint stops at the last *successful* key, so b.json is retried
        assert mock_put.call_args.kwargs["Value"] == "audit/a.json"

    def test_init_sentinel_means_start_from_beginning(self, monkeypatch):
        """The '__INIT__' sentinel must not be passed to S3 as StartAfter."""
        monkeypatch.setattr(handler, "CHECKPOINT_PARAM_NAME", "/fsxn-datadog/test/last-key")

        with patch.object(handler, "get_api_key", return_value="k"), \
             patch.object(handler.ssm_client, "get_parameter") as mock_get, \
             patch.object(handler.s3_client, "list_objects_v2") as mock_list:

            mock_get.return_value = {"Parameter": {"Value": "__INIT__"}}
            mock_list.return_value = {"Contents": [], "IsTruncated": False}

            handler.lambda_handler(self.SCHEDULER_EVENT, None)

        assert "StartAfter" not in mock_list.call_args.kwargs

    def test_missing_checkpoint_parameter_starts_from_beginning(self, monkeypatch):
        """A ParameterNotFound error is treated as 'no checkpoint yet'."""
        from botocore.exceptions import ClientError

        monkeypatch.setattr(handler, "CHECKPOINT_PARAM_NAME", "/fsxn-datadog/test/last-key")

        with patch.object(handler.ssm_client, "get_parameter") as mock_get:
            mock_get.side_effect = ClientError(
                {"Error": {"Code": "ParameterNotFound", "Message": "not found"}},
                "GetParameter",
            )
            assert handler._get_checkpoint() == ""

    def test_directory_markers_are_skipped(self):
        """Zero-byte directory markers are not audit log files."""
        with patch.object(handler.s3_client, "list_objects_v2") as mock_list:
            mock_list.return_value = {
                "Contents": [
                    {"Key": "audit/"},
                    {"Key": "audit/2026/"},
                    {"Key": "audit/2026/log.json"},
                ],
                "IsTruncated": False,
            }
            keys = handler._list_new_keys("arn:aws:s3:::ap", "audit/", "")

        assert keys == ["audit/2026/log.json"]

    def test_listing_paginates(self):
        """Truncated listings are followed via ContinuationToken."""
        with patch.object(handler.s3_client, "list_objects_v2") as mock_list:
            mock_list.side_effect = [
                {
                    "Contents": [{"Key": "audit/a.json"}],
                    "IsTruncated": True,
                    "NextContinuationToken": "tok",
                },
                {"Contents": [{"Key": "audit/b.json"}], "IsTruncated": False},
            ]
            keys = handler._list_new_keys("arn:aws:s3:::ap", "audit/", "")

        assert keys == ["audit/a.json", "audit/b.json"]
        assert mock_list.call_args.kwargs["ContinuationToken"] == "tok"

    def test_backlog_is_capped_per_run(self, monkeypatch):
        """MAX_KEYS_PER_RUN bounds the work so a backlog drains over several runs."""
        monkeypatch.setattr(handler, "CHECKPOINT_PARAM_NAME", "/fsxn-datadog/test/last-key")
        monkeypatch.setattr(handler, "MAX_KEYS_PER_RUN", 2)

        with patch.object(handler, "get_api_key", return_value="k"), \
             patch.object(handler.ssm_client, "get_parameter") as mock_get, \
             patch.object(handler.ssm_client, "put_parameter"), \
             patch.object(handler.s3_client, "list_objects_v2") as mock_list, \
             patch.object(handler.s3_client, "get_object") as mock_get_obj, \
             patch.object(handler, "_ship_to_datadog", return_value=1) as mock_ship:

            mock_get.return_value = {"Parameter": {"Value": "__INIT__"}}
            mock_list.return_value = {
                "Contents": [{"Key": "audit/%d.json" % i} for i in range(5)],
                "IsTruncated": False,
            }
            # Fresh stream per call — a single BytesIO would be consumed by file 1
            mock_get_obj.side_effect = lambda **kw: {"Body": BytesIO(self._audit_body())}

            result = handler.lambda_handler(self.SCHEDULER_EVENT, None)

        assert result["body"]["new_files"] == 2
        assert mock_ship.call_count == 2

    def test_stops_before_lambda_timeout(self, monkeypatch):
        """Processing stops early so the checkpoint is written before the timeout."""
        monkeypatch.setattr(handler, "CHECKPOINT_PARAM_NAME", "/fsxn-datadog/test/last-key")
        monkeypatch.setattr(handler, "SAFETY_THRESHOLD_MS", 30000)

        context = MagicMock()
        # Plenty of time for the first file, then below the safety threshold
        context.get_remaining_time_in_millis.side_effect = [60000, 5000]

        with patch.object(handler, "get_api_key", return_value="k"), \
             patch.object(handler.ssm_client, "get_parameter") as mock_get, \
             patch.object(handler.ssm_client, "put_parameter") as mock_put, \
             patch.object(handler.s3_client, "list_objects_v2") as mock_list, \
             patch.object(handler.s3_client, "get_object") as mock_get_obj, \
             patch.object(handler, "_ship_to_datadog", return_value=1) as mock_ship:

            mock_get.return_value = {"Parameter": {"Value": "__INIT__"}}
            mock_list.return_value = {
                "Contents": [{"Key": "audit/a.json"}, {"Key": "audit/b.json"}],
                "IsTruncated": False,
            }
            mock_get_obj.side_effect = lambda **kw: {"Body": BytesIO(self._audit_body())}

            result = handler.lambda_handler(self.SCHEDULER_EVENT, context)

        assert mock_ship.call_count == 1
        assert result["statusCode"] == 200
        assert mock_put.call_args.kwargs["Value"] == "audit/a.json"

    def test_checkpoint_write_failure_does_not_fail_invocation(self, monkeypatch):
        """Logs were already delivered — failing here would re-ship duplicates."""
        monkeypatch.setattr(handler, "CHECKPOINT_PARAM_NAME", "/fsxn-datadog/test/last-key")

        with patch.object(handler.ssm_client, "put_parameter") as mock_put:
            mock_put.side_effect = Exception("AccessDenied")
            handler._set_checkpoint("audit/a.json")  # must not raise

    def test_checkpoint_disabled_warns_and_reprocesses(self, monkeypatch):
        """An unset checkpoint parameter name degrades to full re-processing."""
        monkeypatch.setattr(handler, "CHECKPOINT_PARAM_NAME", "")
        assert handler._get_checkpoint() == ""
        handler._set_checkpoint("audit/a.json")  # no-op, must not raise


class TestXmlAuditLogParsing:
    """Tests for ONTAP XML audit log parsing.

    ONTAP writes audit logs in the Windows Event Log XML schema
    (`vserver audit create -format xml`), which namespaces every element with
    xmlns="http://schemas.microsoft.com/win/2004/08/events/event". These tests
    pin that behaviour: a namespaced document must yield one record per
    <Event>, not one merged record per file.
    """

    NAMESPACED = """<?xml version="1.0" encoding="UTF-8"?>
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

    def test_namespaced_events_are_not_merged(self):
        """Regression: namespaced <Event> elements must not collapse into one.

        A plain iter("Event") does not match "{uri}Event", which previously sent
        every event through the flat-record fallback. That merged the whole file
        into a single dict and silently dropped all but the last event.
        """
        events = handler._parse_xml_logs(self.NAMESPACED)

        assert len(events) == 2, "each <Event> must produce its own record"
        assert events[0]["event_type"] == "4663"
        assert events[1]["event_type"] == "4660"
        # The first event must survive — it is the one the old code discarded
        assert events[0]["path"] == "/vol/data/first.txt"
        assert events[0]["operation"] == "ReadData"
        assert events[1]["path"] == "/vol/data/second.docx"
        assert events[1]["operation"] == "Delete"

    def test_namespaced_fields_are_extracted(self):
        """Namespace stripping must not break Data Name= field extraction."""
        events = handler._parse_xml_logs(self.NAMESPACED)

        first = events[0]
        assert first["svm"] == "svm-prod-01"
        assert first["user"] == "CORP\\jdoe"
        assert first["client_ip"] == "198.51.100.10"
        assert first["result"] == "Audit Success"
        assert first["timestamp"] == "2026-08-07T04:00:00Z"

    def test_non_namespaced_events_still_parse(self):
        """The pre-existing non-namespaced form must keep working."""
        xml = """<?xml version="1.0"?>
<Events>
<Event><System><EventID>4663</EventID><Computer>svm1</Computer></System>
<EventData><Data Name="SubjectUserName">u1</Data><Data Name="ObjectName">/a.txt</Data></EventData></Event>
<Event><System><EventID>4660</EventID><Computer>svm1</Computer></System>
<EventData><Data Name="SubjectUserName">u2</Data><Data Name="ObjectName">/b.txt</Data></EventData></Event>
</Events>"""
        events = handler._parse_xml_logs(xml)

        assert len(events) == 2
        assert [e["user"] for e in events] == ["u1", "u2"]

    def test_bare_event_fragments_parse(self):
        """Concatenated <Event> fragments with no declaration or root."""
        xml = (
            "<Event><EventID>4663</EventID><UserName>x</UserName></Event>"
            "<Event><EventID>4660</EventID><UserName>y</UserName></Event>"
        )
        events = handler._parse_xml_logs(xml)

        assert len(events) == 2
        assert [e["user"] for e in events] == ["x", "y"]

    def test_wrapper_without_event_tags_is_not_merged(self):
        """A wrapper element around non-<Event> records must still yield N records.

        Without descending through the wrapper, every record below it would be
        flattened into a single dict.
        """
        xml = """<?xml version="1.0"?>
<AuditRecords>
  <Record><EventID>1</EventID><UserName>a</UserName></Record>
  <Record><EventID>2</EventID><UserName>b</UserName></Record>
  <Record><EventID>3</EventID><UserName>c</UserName></Record>
</AuditRecords>"""
        events = handler._parse_xml_logs(xml)

        assert len(events) == 3
        assert [e["user"] for e in events] == ["a", "b", "c"]

    def test_detection_by_content_routes_xml_to_the_xml_parser(self):
        """A key without a .xml suffix must still be parsed as XML by content."""
        events = handler._parse_audit_logs(
            self.NAMESPACED.encode("utf-8"), "audit/2026/08/07/rotated_log"
        )
        assert len(events) == 2

    def test_end_to_end_format_for_datadog(self):
        """Parsed namespaced events must produce two distinct Datadog entries."""
        events = handler._parse_xml_logs(self.NAMESPACED)
        dd_logs = handler._format_for_datadog(events, "audit/2026/08/07/a.xml")

        assert len(dd_logs) == 2
        paths = [d["attributes"]["path"] for d in dd_logs]
        assert paths == ["/vol/data/first.txt", "/vol/data/second.docx"]
        assert all(d["hostname"] == "svm-prod-01" for d in dd_logs)
        assert all(d["ddsource"] == "fsxn" for d in dd_logs)
