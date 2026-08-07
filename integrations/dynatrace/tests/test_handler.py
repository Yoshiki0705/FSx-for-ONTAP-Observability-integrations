import json, sys
from pathlib import Path
from unittest.mock import MagicMock, patch
sys.path.insert(0, str(Path(__file__).parent.parent / "lambda"))
sys.modules.pop("handler", None)
import handler

class TestFormatEntry:
    def test_basic(self):
        log = {"EventID": "4663", "SVMName": "svm-01", "UserName": "admin",
               "Operation": "ReadData", "Result": "Success", "timestamp": "2026-01-15T12:00:00Z"}
        entry = handler._format_entry(log, "audit/test.json")
        assert entry["log.source"] == "fsxn-ontap"
        assert entry["severity"] == "info"
        assert entry["fsxn.svm"] == "svm-01"

    def test_failure_severity(self):
        log = {"Result": "Failure"}
        entry = handler._format_entry(log, "test.json")
        assert entry["severity"] == "warn"

class TestSendBatch:
    @patch("handler.http")
    def test_success(self, mock_http):
        mock_http.request.return_value = MagicMock(status=204)
        assert handler._send_batch([{"content": "test"}], "dt-token") is True
        headers = mock_http.request.call_args[1]["headers"]
        assert headers["Authorization"] == "Api-Token dt-token"


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
