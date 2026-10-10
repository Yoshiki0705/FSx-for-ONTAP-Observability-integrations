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

    The template sends `{"source": "scheduler"}` every 5 minutes. Progress is
    tracked with a record-timestamp watermark, not a last-processed key: ONTAP's
    active audit file has a fixed key that sorts AFTER every rotated file, so a
    key high-water mark passed to ListObjectsV2 StartAfter stalls listing of
    newly rotated files permanently (ROADMAP L80-L86).
    """

    SCHEDULER_EVENT = {
        "source": "scheduler",
        "action": "process_audit_logs",
        "prefix": "audit/",
    }

    # The active audit file ONTAP appends to has a fixed key that sorts AFTER
    # every rotated file's key, because "l" (last) > "D" (the rotated prefix).
    ACTIVE_KEY = "audit/svm_last.xml"
    ROTATED_KEY = "audit/svm_D2026-08-07-T04-00-00_0.xml"

    @staticmethod
    def _ns(handler, ts: str) -> int:
        return handler._audit_timestamp_ns({"timestamp": ts})

    @staticmethod
    def _audit_xml(second: int = 0) -> bytes:
        template = (
            '<?xml version="1.0" encoding="UTF-8"?><Events>'
            '<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event">'
            "<System><EventID>4663</EventID>"
            '<TimeCreated SystemTime="2026-08-07T04:00:%02dZ"/>'
            "<Computer>svm-prod-01</Computer></System>"
            '<EventData><Data Name="SubjectUserName">CORP\\jdoe</Data>'
            '<Data Name="ObjectName">/vol/data/a.txt</Data>'
            '<Data Name="ObjectType">ReadData</Data></EventData>'
            "</Event></Events>"
        ) % second
        return template.encode("utf-8")

    def _s3_body(self, second: int = 0):
        from io import BytesIO
        return {"Body": BytesIO(self._audit_xml(second))}

    @staticmethod
    def _lm(minute):
        from datetime import datetime, timezone
        return datetime(2026, 8, 7, 4, minute, tzinfo=timezone.utc)

    def test_scheduler_ships_fresh_records_and_advances_watermark(self, reset_handler, monkeypatch):
        handler = reset_handler
        monkeypatch.setattr(handler, "CHECKPOINT_PARAM_NAME", "/fsxn-crowdstrike/test/wm")

        with patch.object(handler, "get_ingest_token", return_value="tok"), \
             patch.object(handler.ssm_client, "get_parameter") as mock_get, \
             patch.object(handler.ssm_client, "put_parameter") as mock_put, \
             patch.object(handler.s3_client, "list_objects_v2") as mock_list, \
             patch.object(handler.s3_client, "get_object") as mock_obj, \
             patch.object(handler, "_ship_to_logscale", return_value=1) as mock_ship:

            mock_get.return_value = {"Parameter": {"Value": "__INIT__"}}
            mock_list.return_value = {
                "Contents": [
                    {"Key": self.ROTATED_KEY, "LastModified": self._lm(1)},
                    {"Key": self.ACTIVE_KEY, "LastModified": self._lm(2)},
                ],
                "IsTruncated": False,
            }
            mock_obj.side_effect = lambda **kw: self._s3_body(5)

            result = handler.lambda_handler(self.SCHEDULER_EVENT, None)

        assert result["statusCode"] == 200
        assert result["body"]["new_files"] == 2
        assert mock_ship.call_count == 2
        # A key high-water mark must never be passed to S3 — that is the stall.
        assert "StartAfter" not in mock_list.call_args.kwargs
        assert mock_list.call_args.kwargs["Prefix"] == "audit/"
        mock_put.assert_called_once()
        assert mock_put.call_args.kwargs["Value"] == str(self._ns(handler, "2026-08-07T04:00:05Z"))

    def test_active_file_fixed_key_does_not_stop_later_rotated_files(self, reset_handler, monkeypatch):
        """Regression for ROADMAP L82 — the exact failure mode.

        Under the old key scheme, once the checkpoint reached the active file's
        fixed key, StartAfter never returned a later-rotated file again (the
        rotated key sorts BEFORE the active key). With the timestamp watermark,
        a file rotated after the active one is still listed and shipped.
        """
        handler = reset_handler
        monkeypatch.setattr(handler, "CHECKPOINT_PARAM_NAME", "/fsxn-crowdstrike/test/wm")

        # The ordering fact the old scheme broke on, asserted directly.
        assert self.ROTATED_KEY < self.ACTIVE_KEY

        watermark = self._ns(handler, "2026-08-07T04:00:03Z")
        later_rotated = "audit/svm_D2026-08-07-T04-05-00_1.xml"

        with patch.object(handler, "get_ingest_token", return_value="tok"), \
             patch.object(handler.ssm_client, "get_parameter") as mock_get, \
             patch.object(handler.ssm_client, "put_parameter"), \
             patch.object(handler.s3_client, "list_objects_v2") as mock_list, \
             patch.object(handler.s3_client, "get_object") as mock_obj, \
             patch.object(handler, "_ship_to_logscale", return_value=1):

            mock_get.return_value = {"Parameter": {"Value": str(watermark)}}
            mock_list.return_value = {
                "Contents": [
                    {"Key": self.ACTIVE_KEY, "LastModified": self._lm(1)},
                    {"Key": later_rotated, "LastModified": self._lm(6)},
                ],
                "IsTruncated": False,
            }
            # Each file holds a record at 04:00:08, newer than the 04:00:03 watermark.
            mock_obj.side_effect = lambda **kw: self._s3_body(8)

            result = handler.lambda_handler(self.SCHEDULER_EVENT, None)

        assert "StartAfter" not in mock_list.call_args.kwargs
        assert mock_obj.call_count == 2
        read_keys = {c.kwargs["Key"] for c in mock_obj.call_args_list}
        assert read_keys == {self.ACTIVE_KEY, later_rotated}
        assert result["body"]["total_shipped"] == 2

    def test_scheduler_no_longer_returns_zero_for_every_run(self, reset_handler, monkeypatch):
        """Regression: a defect once made every scheduled run report 0 events."""
        handler = reset_handler
        monkeypatch.setattr(handler, "CHECKPOINT_PARAM_NAME", "/fsxn-crowdstrike/test/wm")

        with patch.object(handler, "get_ingest_token", return_value="tok"), \
             patch.object(handler.ssm_client, "get_parameter") as mock_get, \
             patch.object(handler.ssm_client, "put_parameter"), \
             patch.object(handler.s3_client, "list_objects_v2") as mock_list, \
             patch.object(handler.s3_client, "get_object") as mock_obj, \
             patch.object(handler, "_ship_to_logscale", return_value=1):

            mock_get.return_value = {"Parameter": {"Value": "__INIT__"}}
            mock_list.return_value = {
                "Contents": [{"Key": "audit/a.xml", "LastModified": self._lm(1)}],
                "IsTruncated": False,
            }
            mock_obj.side_effect = lambda **kw: self._s3_body(5)

            result = handler.lambda_handler(self.SCHEDULER_EVENT, None)

        assert result["body"]["total_logs"] > 0, "scheduler run must parse events"
        assert result["body"]["total_shipped"] > 0, "scheduler run must ship events"

    def test_no_candidate_files_is_a_noop(self, reset_handler, monkeypatch):
        handler = reset_handler
        monkeypatch.setattr(handler, "CHECKPOINT_PARAM_NAME", "/fsxn-crowdstrike/test/wm")

        with patch.object(handler, "get_ingest_token", return_value="tok"), \
             patch.object(handler.ssm_client, "get_parameter") as mock_get, \
             patch.object(handler.ssm_client, "put_parameter") as mock_put, \
             patch.object(handler.s3_client, "list_objects_v2") as mock_list:

            mock_get.return_value = {"Parameter": {"Value": "12345"}}
            mock_list.return_value = {"Contents": [], "IsTruncated": False}

            result = handler.lambda_handler(self.SCHEDULER_EVENT, None)

        assert result["statusCode"] == 200
        assert result["body"]["new_files"] == 0
        mock_put.assert_not_called()

    def test_legacy_key_checkpoint_is_a_cold_start_not_a_crash(self, reset_handler, monkeypatch):
        handler = reset_handler
        monkeypatch.setattr(handler, "CHECKPOINT_PARAM_NAME", "/fsxn-crowdstrike/test/wm")
        with patch.object(handler.ssm_client, "get_parameter") as mock_get:
            mock_get.return_value = {"Parameter": {"Value": "audit/2026/08/07/a.xml"}}
            assert handler._get_checkpoint() == 0

    def test_init_sentinel_is_a_cold_start(self, reset_handler, monkeypatch):
        handler = reset_handler
        monkeypatch.setattr(handler, "CHECKPOINT_PARAM_NAME", "/fsxn-crowdstrike/test/wm")
        with patch.object(handler.ssm_client, "get_parameter") as mock_get:
            mock_get.return_value = {"Parameter": {"Value": "__INIT__"}}
            assert handler._get_checkpoint() == 0

    def test_checkpoint_stops_at_first_failure(self, reset_handler, monkeypatch):
        """Advancing past a failed file would drop its audit events."""
        handler = reset_handler
        monkeypatch.setattr(handler, "CHECKPOINT_PARAM_NAME", "/fsxn-crowdstrike/test/wm")

        with patch.object(handler, "get_ingest_token", return_value="tok"), \
             patch.object(handler.ssm_client, "get_parameter") as mock_get, \
             patch.object(handler.ssm_client, "put_parameter") as mock_put, \
             patch.object(handler.s3_client, "list_objects_v2") as mock_list, \
             patch.object(handler.s3_client, "get_object") as mock_obj, \
             patch.object(handler, "_ship_to_logscale") as mock_ship:

            mock_get.return_value = {"Parameter": {"Value": "__INIT__"}}
            mock_list.return_value = {
                "Contents": [
                    {"Key": "audit/a.xml", "LastModified": self._lm(0)},
                    {"Key": "audit/b.xml", "LastModified": self._lm(1)},
                    {"Key": "audit/c.xml", "LastModified": self._lm(2)},
                ],
                "IsTruncated": False,
            }
            bodies = {
                "audit/a.xml": self._s3_body(0),
                "audit/b.xml": self._s3_body(10),
                "audit/c.xml": self._s3_body(20),
            }
            mock_obj.side_effect = lambda **kw: {"Body": bodies[kw["Key"]]["Body"]}
            mock_ship.side_effect = [1, RuntimeError("HEC 503"), 1]

            result = handler.lambda_handler(self.SCHEDULER_EVENT, None)

        assert result["statusCode"] == 207
        assert mock_ship.call_count == 2, "must not continue past the failing file"
        assert mock_put.call_args.kwargs["Value"] == str(self._ns(handler, "2026-08-07T04:00:00Z"))

    def test_zero_shipped_with_events_is_a_failure(self, reset_handler, monkeypatch):
        """Events parsed but none delivered must not advance the watermark."""
        handler = reset_handler
        monkeypatch.setattr(handler, "CHECKPOINT_PARAM_NAME", "/fsxn-crowdstrike/test/wm")

        with patch.object(handler, "get_ingest_token", return_value="tok"), \
             patch.object(handler.ssm_client, "get_parameter") as mock_get, \
             patch.object(handler.ssm_client, "put_parameter") as mock_put, \
             patch.object(handler.s3_client, "list_objects_v2") as mock_list, \
             patch.object(handler.s3_client, "get_object") as mock_obj, \
             patch.object(handler, "_ship_to_logscale", return_value=0):

            mock_get.return_value = {"Parameter": {"Value": "__INIT__"}}
            mock_list.return_value = {"Contents": [{"Key": "audit/a.xml", "LastModified": self._lm(1)}], "IsTruncated": False}
            mock_obj.side_effect = lambda **kw: self._s3_body(5)

            result = handler.lambda_handler(self.SCHEDULER_EVENT, None)

        assert result["statusCode"] == 207
        mock_put.assert_not_called()

    def test_backlog_is_capped_per_run(self, reset_handler, monkeypatch):
        handler = reset_handler
        monkeypatch.setattr(handler, "CHECKPOINT_PARAM_NAME", "/fsxn-crowdstrike/test/wm")
        monkeypatch.setattr(handler, "MAX_KEYS_PER_RUN", 2)

        with patch.object(handler, "get_ingest_token", return_value="tok"), \
             patch.object(handler.ssm_client, "get_parameter") as mock_get, \
             patch.object(handler.ssm_client, "put_parameter"), \
             patch.object(handler.s3_client, "list_objects_v2") as mock_list, \
             patch.object(handler.s3_client, "get_object") as mock_obj, \
             patch.object(handler, "_ship_to_logscale", return_value=1) as mock_ship:

            mock_get.return_value = {"Parameter": {"Value": "__INIT__"}}
            mock_list.return_value = {
                "Contents": [{"Key": f"audit/{i}.xml", "LastModified": self._lm(i)} for i in range(5)],
                "IsTruncated": False,
            }
            mock_obj.side_effect = lambda **kw: self._s3_body(5)

            handler.lambda_handler(self.SCHEDULER_EVENT, None)

        assert mock_ship.call_count == 2

    def test_directory_markers_are_skipped(self, reset_handler):
        handler = reset_handler
        with patch.object(handler.s3_client, "list_objects_v2") as mock_list:
            mock_list.return_value = {
                "Contents": [
                    {"Key": "audit/"}, {"Key": "audit/2026/"},
                    {"Key": "audit/2026/log.xml", "LastModified": self._lm(0)},
                ],
                "IsTruncated": False,
            }
            keys = handler._list_candidate_keys("audit/", 0)

        assert keys == ["audit/2026/log.xml"]

    def test_listing_paginates(self, reset_handler):
        handler = reset_handler
        with patch.object(handler.s3_client, "list_objects_v2") as mock_list:
            mock_list.side_effect = [
                {"Contents": [{"Key": "audit/a.xml", "LastModified": self._lm(0)}], "IsTruncated": True,
                 "NextContinuationToken": "tok"},
                {"Contents": [{"Key": "audit/b.xml", "LastModified": self._lm(1)}], "IsTruncated": False},
            ]
            keys = handler._list_candidate_keys("audit/", 0)

        assert keys == ["audit/a.xml", "audit/b.xml"]
        assert mock_list.call_args.kwargs["ContinuationToken"] == "tok"

    def test_missing_checkpoint_parameter_starts_from_beginning(self, reset_handler, monkeypatch):
        handler = reset_handler
        from botocore.exceptions import ClientError

        monkeypatch.setattr(handler, "CHECKPOINT_PARAM_NAME", "/fsxn-crowdstrike/test/wm")
        with patch.object(handler.ssm_client, "get_parameter") as mock_get:
            mock_get.side_effect = ClientError(
                {"Error": {"Code": "ParameterNotFound", "Message": "nope"}}, "GetParameter"
            )
            assert handler._get_checkpoint() == 0

    def test_checkpoint_write_failure_does_not_raise(self, reset_handler, monkeypatch):
        """Events were already delivered — failing here would re-ship them."""
        handler = reset_handler
        monkeypatch.setattr(handler, "CHECKPOINT_PARAM_NAME", "/fsxn-crowdstrike/test/wm")
        with patch.object(handler.ssm_client, "put_parameter") as mock_put:
            mock_put.side_effect = Exception("AccessDenied")
            handler._set_checkpoint(12345)  # must not raise

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
