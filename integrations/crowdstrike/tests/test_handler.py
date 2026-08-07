"""Unit tests for CrowdStrike Falcon LogScale handler."""

import importlib
import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Add handler to path
HANDLER_DIR = Path(__file__).resolve().parent.parent / "lambda"
sys.path.insert(0, str(HANDLER_DIR))


@pytest.fixture
def reset_handler():
    """Import handler fresh for each test."""
    if "handler" in sys.modules:
        del sys.modules["handler"]
    import handler
    handler._token_cache = None
    return handler


class TestParseXml:
    """Tests for XML audit log parsing."""

    def test_parse_valid_xml(self, reset_handler, sample_xml_audit_log):
        handler = reset_handler
        events = handler._parse_xml(sample_xml_audit_log)
        assert len(events) == 1
        assert events[0]["event_type"] == "4663"
        assert events[0]["user"] == "CORP\\testuser"
        assert events[0]["path"] == "/share/test/document.xlsx"
        assert events[0]["svm"] == "TestSVM"
        assert events[0]["client_ip"] == "10.0.1.100"
        assert events[0]["result"] == "Audit Success"

    def test_parse_empty_xml(self, reset_handler):
        handler = reset_handler
        events = handler._parse_xml("<Events></Events>")
        assert len(events) == 0

    def test_parse_invalid_xml(self, reset_handler):
        handler = reset_handler
        events = handler._parse_xml("not xml at all")
        assert len(events) == 0


class TestParseJson:
    """Tests for JSON audit log parsing."""

    def test_parse_newline_delimited(self, reset_handler, sample_json_audit_logs):
        handler = reset_handler
        events = handler._parse_json(sample_json_audit_logs)
        assert len(events) == 2
        assert events[0]["event_type"] == "4663"
        assert events[1]["event_type"] == "4656"

    def test_parse_json_array(self, reset_handler):
        handler = reset_handler
        data = json.dumps([{"EventID": "4663", "UserName": "user1"}, {"EventID": "4656", "UserName": "user2"}])
        events = handler._parse_json(data)
        assert len(events) == 2

    def test_parse_empty(self, reset_handler):
        handler = reset_handler
        events = handler._parse_json("")
        assert len(events) == 0


class TestFormatForLogscale:
    """Tests for HEC format generation."""

    def test_basic_formatting(self, reset_handler):
        handler = reset_handler
        logs = [{"timestamp": "2026-06-01T10:00:00Z", "event_type": "4663",
                 "source": "fsxn-ontap", "svm": "TestSVM", "user": "testuser",
                 "client_ip": "10.0.1.100", "operation": "File",
                 "path": "/share/test.xlsx", "result": "Audit Success"}]
        result = handler._format_for_logscale(logs, "audit/test.xml")
        assert len(result) == 1
        assert result[0]["source"] == "fsxn-ontap"
        assert result[0]["sourcetype"] == "fsxn:audit"
        assert result[0]["index"] == "fsxn_audit"
        assert result[0]["event"]["user"] == "testuser"
        assert result[0]["event"]["s3_key"] == "audit/test.xml"
        assert "time" in result[0]  # epoch seconds

    def test_empty_logs(self, reset_handler):
        handler = reset_handler
        result = handler._format_for_logscale([], "test.xml")
        assert len(result) == 0


class TestShipToLogscale:
    """Tests for LogScale HEC delivery."""

    def test_successful_delivery(self, reset_handler):
        handler = reset_handler
        events = [{"event": {"test": "data"}, "source": "fsxn", "sourcetype": "fsxn:audit",
                   "index": "fsxn_audit", "time": "2026-06-01T10:00:00Z", "fields": {}}]

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.data = b'{"text":"Success"}'

        with patch.object(handler.http, "request", return_value=mock_resp) as mock_req:
            result = handler._ship_to_logscale(events, "test-token")
            assert result == 1
            mock_req.assert_called_once()
            call_kwargs = mock_req.call_args
            assert "Bearer test-token" in str(call_kwargs)

    def test_empty_events_returns_zero(self, reset_handler):
        handler = reset_handler
        result = handler._ship_to_logscale([], "test-token")
        assert result == 0

    def test_server_error_retries(self, reset_handler):
        handler = reset_handler
        events = [{"event": {"test": "data"}, "source": "fsxn", "sourcetype": "fsxn:audit",
                   "index": "fsxn_audit", "time": "", "fields": {}}]

        mock_resp_500 = MagicMock(status=500, data=b"Internal Server Error")
        mock_resp_200 = MagicMock(status=200, data=b'{"text":"Success"}')

        with patch.object(handler.http, "request", side_effect=[mock_resp_500, mock_resp_200]):
            with patch("time.sleep"):
                result = handler._ship_to_logscale(events, "test-token")
                assert result == 1


class TestGetIngestToken:
    """Tests for token retrieval from Secrets Manager."""

    def test_plain_string_token(self, reset_handler):
        handler = reset_handler
        with patch.object(handler.secrets_client, "get_secret_value") as mock_get:
            mock_get.return_value = {"SecretString": "plain-token-value"}
            handler._token_cache = None
            token = handler.get_ingest_token()
            assert token == "plain-token-value"

    def test_json_format_token(self, reset_handler):
        handler = reset_handler
        with patch.object(handler.secrets_client, "get_secret_value") as mock_get:
            mock_get.return_value = {"SecretString": json.dumps({"ingest_token": "json-token-123"})}
            handler._token_cache = None
            token = handler.get_ingest_token()
            assert token == "json-token-123"

    def test_token_cached(self, reset_handler):
        handler = reset_handler
        handler._token_cache = "cached-token"
        token = handler.get_ingest_token()
        assert token == "cached-token"


# ─── Scheduler polling regression (added after the no-op defect) ────────────


class TestSchedulerPolling:
    """Tests for the EventBridge Scheduler polling path.

    The template sends `{"source": "scheduler"}` every 5 minutes. The handler
    previously routed that through `_extract_s3_records`, which only understands
    `Records`/`detail` payloads, so it returned an empty list and reported
    success while shipping nothing — indefinitely.
    """

    SCHEDULER_EVENT = {
        "source": "scheduler",
        "action": "process_audit_logs",
        "prefix": "audit/",
    }

    @staticmethod
    def _audit_xml() -> bytes:
        return (
            '<?xml version="1.0" encoding="UTF-8"?><Events>'
            '<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event">'
            "<System><EventID>4663</EventID>"
            '<TimeCreated SystemTime="2026-08-07T04:00:00Z"/>'
            "<Computer>svm-prod-01</Computer></System>"
            '<EventData><Data Name="SubjectUserName">CORP\\jdoe</Data>'
            '<Data Name="ObjectName">/vol/data/a.txt</Data>'
            '<Data Name="ObjectType">ReadData</Data></EventData>'
            "</Event></Events>"
        ).encode("utf-8")

    def _s3_body(self):
        from io import BytesIO
        return {"Body": BytesIO(self._audit_xml())}

    def test_scheduler_ships_new_files_and_advances_checkpoint(self, reset_handler, monkeypatch):
        handler = reset_handler
        monkeypatch.setattr(handler, "CHECKPOINT_PARAM_NAME", "/fsxn-crowdstrike/test/key")

        with patch.object(handler, "get_ingest_token", return_value="tok"), \
             patch.object(handler.ssm_client, "get_parameter") as mock_get, \
             patch.object(handler.ssm_client, "put_parameter") as mock_put, \
             patch.object(handler.s3_client, "list_objects_v2") as mock_list, \
             patch.object(handler.s3_client, "get_object") as mock_obj, \
             patch.object(handler, "_ship_to_logscale", return_value=1) as mock_ship:

            mock_get.return_value = {"Parameter": {"Value": "audit/2026/08/07/a.json"}}
            mock_list.return_value = {
                "Contents": [
                    {"Key": "audit/2026/08/07/b.xml"},
                    {"Key": "audit/2026/08/07/c.xml"},
                ],
                "IsTruncated": False,
            }
            mock_obj.side_effect = lambda **kw: self._s3_body()

            result = handler.lambda_handler(self.SCHEDULER_EVENT, None)

        assert result["statusCode"] == 200
        assert result["body"]["new_files"] == 2
        assert mock_ship.call_count == 2
        # StartAfter must use the checkpoint so processed keys are skipped
        assert mock_list.call_args.kwargs["StartAfter"] == "audit/2026/08/07/a.json"
        assert mock_list.call_args.kwargs["Prefix"] == "audit/"
        mock_put.assert_called_once()
        assert mock_put.call_args.kwargs["Value"] == "audit/2026/08/07/c.xml"

    def test_scheduler_no_longer_returns_zero_for_every_run(self, reset_handler, monkeypatch):
        """Regression: the defect made every scheduled run report 0 events."""
        handler = reset_handler
        monkeypatch.setattr(handler, "CHECKPOINT_PARAM_NAME", "/fsxn-crowdstrike/test/key")

        with patch.object(handler, "get_ingest_token", return_value="tok"), \
             patch.object(handler.ssm_client, "get_parameter") as mock_get, \
             patch.object(handler.ssm_client, "put_parameter"), \
             patch.object(handler.s3_client, "list_objects_v2") as mock_list, \
             patch.object(handler.s3_client, "get_object") as mock_obj, \
             patch.object(handler, "_ship_to_logscale", return_value=1):

            mock_get.return_value = {"Parameter": {"Value": "__INIT__"}}
            mock_list.return_value = {
                "Contents": [{"Key": "audit/a.xml"}], "IsTruncated": False,
            }
            mock_obj.side_effect = lambda **kw: self._s3_body()

            result = handler.lambda_handler(self.SCHEDULER_EVENT, None)

        assert result["body"]["total_logs"] > 0, "scheduler run must parse events"
        assert result["body"]["total_shipped"] > 0, "scheduler run must ship events"

    def test_no_new_files_is_a_noop(self, reset_handler, monkeypatch):
        handler = reset_handler
        monkeypatch.setattr(handler, "CHECKPOINT_PARAM_NAME", "/fsxn-crowdstrike/test/key")

        with patch.object(handler, "get_ingest_token", return_value="tok"), \
             patch.object(handler.ssm_client, "get_parameter") as mock_get, \
             patch.object(handler.ssm_client, "put_parameter") as mock_put, \
             patch.object(handler.s3_client, "list_objects_v2") as mock_list:

            mock_get.return_value = {"Parameter": {"Value": "audit/z.xml"}}
            mock_list.return_value = {"Contents": [], "IsTruncated": False}

            result = handler.lambda_handler(self.SCHEDULER_EVENT, None)

        assert result["statusCode"] == 200
        assert result["body"]["new_files"] == 0
        mock_put.assert_not_called()

    def test_init_sentinel_is_not_sent_as_start_after(self, reset_handler, monkeypatch):
        handler = reset_handler
        monkeypatch.setattr(handler, "CHECKPOINT_PARAM_NAME", "/fsxn-crowdstrike/test/key")

        with patch.object(handler, "get_ingest_token", return_value="tok"), \
             patch.object(handler.ssm_client, "get_parameter") as mock_get, \
             patch.object(handler.s3_client, "list_objects_v2") as mock_list:

            mock_get.return_value = {"Parameter": {"Value": "__INIT__"}}
            mock_list.return_value = {"Contents": [], "IsTruncated": False}
            handler.lambda_handler(self.SCHEDULER_EVENT, None)

        assert "StartAfter" not in mock_list.call_args.kwargs

    def test_checkpoint_stops_at_first_failure(self, reset_handler, monkeypatch):
        """Advancing past a failed file would drop its audit events."""
        handler = reset_handler
        monkeypatch.setattr(handler, "CHECKPOINT_PARAM_NAME", "/fsxn-crowdstrike/test/key")

        with patch.object(handler, "get_ingest_token", return_value="tok"), \
             patch.object(handler.ssm_client, "get_parameter") as mock_get, \
             patch.object(handler.ssm_client, "put_parameter") as mock_put, \
             patch.object(handler.s3_client, "list_objects_v2") as mock_list, \
             patch.object(handler.s3_client, "get_object") as mock_obj, \
             patch.object(handler, "_ship_to_logscale") as mock_ship:

            mock_get.return_value = {"Parameter": {"Value": "__INIT__"}}
            mock_list.return_value = {
                "Contents": [
                    {"Key": "audit/a.xml"}, {"Key": "audit/b.xml"}, {"Key": "audit/c.xml"},
                ],
                "IsTruncated": False,
            }
            mock_obj.side_effect = lambda **kw: self._s3_body()
            mock_ship.side_effect = [1, RuntimeError("HEC 503"), 1]

            result = handler.lambda_handler(self.SCHEDULER_EVENT, None)

        assert result["statusCode"] == 207
        assert mock_ship.call_count == 2, "must not continue past the failing file"
        assert mock_put.call_args.kwargs["Value"] == "audit/a.xml"

    def test_zero_shipped_with_events_is_a_failure(self, reset_handler, monkeypatch):
        """Events parsed but none delivered must not advance the checkpoint."""
        handler = reset_handler
        monkeypatch.setattr(handler, "CHECKPOINT_PARAM_NAME", "/fsxn-crowdstrike/test/key")

        with patch.object(handler, "get_ingest_token", return_value="tok"), \
             patch.object(handler.ssm_client, "get_parameter") as mock_get, \
             patch.object(handler.ssm_client, "put_parameter") as mock_put, \
             patch.object(handler.s3_client, "list_objects_v2") as mock_list, \
             patch.object(handler.s3_client, "get_object") as mock_obj, \
             patch.object(handler, "_ship_to_logscale", return_value=0):

            mock_get.return_value = {"Parameter": {"Value": "__INIT__"}}
            mock_list.return_value = {"Contents": [{"Key": "audit/a.xml"}], "IsTruncated": False}
            mock_obj.side_effect = lambda **kw: self._s3_body()

            result = handler.lambda_handler(self.SCHEDULER_EVENT, None)

        assert result["statusCode"] == 207
        mock_put.assert_not_called()

    def test_backlog_is_capped_per_run(self, reset_handler, monkeypatch):
        handler = reset_handler
        monkeypatch.setattr(handler, "CHECKPOINT_PARAM_NAME", "/fsxn-crowdstrike/test/key")
        monkeypatch.setattr(handler, "MAX_KEYS_PER_RUN", 2)

        with patch.object(handler, "get_ingest_token", return_value="tok"), \
             patch.object(handler.ssm_client, "get_parameter") as mock_get, \
             patch.object(handler.ssm_client, "put_parameter"), \
             patch.object(handler.s3_client, "list_objects_v2") as mock_list, \
             patch.object(handler.s3_client, "get_object") as mock_obj, \
             patch.object(handler, "_ship_to_logscale", return_value=1) as mock_ship:

            mock_get.return_value = {"Parameter": {"Value": "__INIT__"}}
            mock_list.return_value = {
                "Contents": [{"Key": f"audit/{i}.xml"} for i in range(5)],
                "IsTruncated": False,
            }
            mock_obj.side_effect = lambda **kw: self._s3_body()

            handler.lambda_handler(self.SCHEDULER_EVENT, None)

        assert mock_ship.call_count == 2

    def test_directory_markers_are_skipped(self, reset_handler):
        handler = reset_handler
        with patch.object(handler.s3_client, "list_objects_v2") as mock_list:
            mock_list.return_value = {
                "Contents": [
                    {"Key": "audit/"}, {"Key": "audit/2026/"}, {"Key": "audit/2026/log.xml"},
                ],
                "IsTruncated": False,
            }
            keys = handler._list_new_keys("audit/", "")

        assert keys == ["audit/2026/log.xml"]

    def test_listing_paginates(self, reset_handler):
        handler = reset_handler
        with patch.object(handler.s3_client, "list_objects_v2") as mock_list:
            mock_list.side_effect = [
                {"Contents": [{"Key": "audit/a.xml"}], "IsTruncated": True,
                 "NextContinuationToken": "tok"},
                {"Contents": [{"Key": "audit/b.xml"}], "IsTruncated": False},
            ]
            keys = handler._list_new_keys("audit/", "")

        assert keys == ["audit/a.xml", "audit/b.xml"]
        assert mock_list.call_args.kwargs["ContinuationToken"] == "tok"

    def test_missing_checkpoint_parameter_starts_from_beginning(self, reset_handler, monkeypatch):
        handler = reset_handler
        from botocore.exceptions import ClientError

        monkeypatch.setattr(handler, "CHECKPOINT_PARAM_NAME", "/fsxn-crowdstrike/test/key")
        with patch.object(handler.ssm_client, "get_parameter") as mock_get:
            mock_get.side_effect = ClientError(
                {"Error": {"Code": "ParameterNotFound", "Message": "nope"}}, "GetParameter"
            )
            assert handler._get_checkpoint() == ""

    def test_checkpoint_write_failure_does_not_raise(self, reset_handler, monkeypatch):
        """Events were already delivered — failing here would re-ship them."""
        handler = reset_handler
        monkeypatch.setattr(handler, "CHECKPOINT_PARAM_NAME", "/fsxn-crowdstrike/test/key")
        with patch.object(handler.ssm_client, "put_parameter") as mock_put:
            mock_put.side_effect = Exception("AccessDenied")
            handler._set_checkpoint("audit/a.xml")  # must not raise

    def test_s3_event_path_still_works(self, reset_handler, monkeypatch):
        """Manual S3-event invocation must not be broken by the scheduler path."""
        handler = reset_handler
        event = {"Records": [{"s3": {"bucket": {"name": "b"}, "object": {"key": "audit/a.xml"}}}]}

        with patch.object(handler, "get_ingest_token", return_value="tok"), \
             patch.object(handler.s3_client, "get_object") as mock_obj, \
             patch.object(handler, "_ship_to_logscale", return_value=1):
            mock_obj.side_effect = lambda **kw: self._s3_body()
            result = handler.lambda_handler(event, None)

        assert result["statusCode"] == 200
        # The S3-event path does not checkpoint
        assert "checkpoint" not in result["body"]
