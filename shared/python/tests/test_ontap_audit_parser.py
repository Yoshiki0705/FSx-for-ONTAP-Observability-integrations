"""Tests for the canonical FSx for ONTAP audit log parser.

The premise these tests defend: ONTAP writes EVTX or XML, never JSON. A parser
that only understands JSON lines produces one garbage record per line of XML,
which is how eight vendor integrations shipped unusable data.
"""

import gzip
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from ontap_audit_parser import (  # noqa: E402
    detect_format,
    normalize_event,
    parse_audit_log,
    parse_evtx,
    parse_json_lines,
    parse_xml,
)

NAMESPACED_XML = """<?xml version="1.0" encoding="UTF-8"?>
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


class TestFormatDetection:
    def test_evtx_by_magic(self):
        assert detect_format(b"ElfFile\x00rest", "audit/rotated") == "evtx"

    def test_evtx_by_extension(self):
        assert detect_format(b"anything", "audit/log.evtx") == "evtx"

    def test_xml_by_extension(self):
        assert detect_format(b"<Event/>", "audit/log.xml") == "xml"

    def test_xml_by_content_without_suffix(self):
        """ONTAP rotations do not always carry a meaningful suffix."""
        assert detect_format(NAMESPACED_XML.encode(), "audit/rotated_0001") == "xml"

    def test_json_by_content(self):
        assert detect_format(b'{"a":1}', "audit/x") == "json"

    def test_unknown(self):
        assert detect_format(b"plain text", "audit/x") == "unknown"


class TestNamespacedXml:
    """The defect that made eight integrations ship unusable data."""

    def test_each_event_becomes_one_record(self):
        events = parse_xml(NAMESPACED_XML)
        assert len(events) == 2

    def test_fields_are_extracted_not_raw_lines(self):
        first = parse_xml(NAMESPACED_XML)[0]
        assert first["event_type"] == "4663"
        assert first["user"] == "CORP\\jdoe"
        assert first["path"] == "/vol/data/first.txt"
        assert first["operation"] == "ReadData"
        assert first["client_ip"] == "198.51.100.10"
        assert first["result"] == "Audit Success"
        assert first["svm"] == "svm-prod-01"
        assert first["timestamp"] == "2026-08-07T04:00:00Z"

    def test_first_event_is_not_discarded(self):
        """Merging into one record kept only the last event."""
        paths = [e["path"] for e in parse_xml(NAMESPACED_XML)]
        assert paths == ["/vol/data/first.txt", "/vol/data/second.docx"]

    def test_no_message_blob_records(self):
        """The old JSON-only path produced {"message": "<xml line>"} per line."""
        events = parse_xml(NAMESPACED_XML)
        assert not any(str(e.get("message", "")).startswith("<") for e in events)

    def test_raw_fields_are_preserved(self):
        first = parse_xml(NAMESPACED_XML)[0]
        assert first["raw"]["EventID"] == "4663"
        assert first["raw"]["TimeCreated_SystemTime"] == "2026-08-07T04:00:00Z"


class TestPlainXml:
    def test_non_namespaced_document(self):
        xml = """<?xml version="1.0"?>
<Events>
<Event><System><EventID>4663</EventID><Computer>svm1</Computer></System>
<EventData><Data Name="UserName">u1</Data><Data Name="ObjectName">/a.txt</Data></EventData></Event>
<Event><System><EventID>4660</EventID><Computer>svm1</Computer></System>
<EventData><Data Name="UserName">u2</Data><Data Name="ObjectName">/b.txt</Data></EventData></Event>
</Events>"""
        events = parse_xml(xml)
        assert len(events) == 2
        assert [e["user"] for e in events] == ["u1", "u2"]

    def test_bare_concatenated_fragments(self):
        xml = (
            "<Event><EventID>4663</EventID><UserName>x</UserName></Event>"
            "<Event><EventID>4660</EventID><UserName>y</UserName></Event>"
        )
        events = parse_xml(xml)
        assert len(events) == 2
        assert [e["user"] for e in events] == ["x", "y"]

    def test_wrapper_without_event_tags(self):
        xml = """<?xml version="1.0"?>
<AuditRecords>
  <Record><EventID>1</EventID><UserName>a</UserName></Record>
  <Record><EventID>2</EventID><UserName>b</UserName></Record>
</AuditRecords>"""
        events = parse_xml(xml)
        assert len(events) == 2
        assert [e["user"] for e in events] == ["a", "b"]

    def test_truncated_document_recovers_whole_events(self):
        """A rotation cut mid-write must not lose the events already written."""
        truncated = NAMESPACED_XML[: NAMESPACED_XML.rfind("</Event>") + len("</Event>")]
        events = parse_xml(truncated)
        assert len(events) >= 1
        assert events[0]["event_type"] == "4663"

    def test_single_line_document_is_not_lost(self):
        """Regression: declaration and content on one line.

        The per-vendor parsers stripped the declaration by dropping line 0, so a
        document written entirely on one line became <AuditEvents></AuditEvents>
        and every event was silently discarded.
        """
        one_line = (
            '<?xml version="1.0" encoding="UTF-8"?><Events>'
            '<Event><EventID>4663</EventID><UserName>u1</UserName></Event>'
            '<Event><EventID>4660</EventID><UserName>u2</UserName></Event>'
            '</Events>'
        )
        events = parse_xml(one_line)

        assert len(events) == 2
        assert [e["user"] for e in events] == ["u1", "u2"]

    def test_single_line_namespaced_document(self):
        one_line = (
            '<?xml version="1.0"?><Events>'
            '<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event">'
            '<System><EventID>4663</EventID></System>'
            '<EventData><Data Name="ObjectName">/a.txt</Data></EventData>'
            '</Event></Events>'
        )
        events = parse_xml(one_line)

        assert len(events) == 1
        assert events[0]["path"] == "/a.txt"

    def test_empty_input(self):
        assert parse_xml("") == []
        assert parse_xml("   ") == []


class TestEvtx:
    def test_evtx_reports_itself_rather_than_pretending(self):
        events = parse_evtx(b"ElfFile\x00" + b"\x00" * 100)
        assert len(events) == 1
        assert events[0]["event_type"] == "evtx_unparsed"
        # The message must tell the operator how to get real fields
        assert "-format xml" in events[0]["message"]

    def test_non_evtx_bytes_return_nothing(self):
        assert parse_evtx(b"not evtx") == []


class TestJsonLines:
    def test_ndjson(self):
        text = '{"EventID":"4663","UserName":"u1"}\n{"EventID":"4660","UserName":"u2"}'
        events = parse_json_lines(text)
        assert len(events) == 2
        assert [e["event_type"] for e in events] == ["4663", "4660"]

    def test_json_array(self):
        text = json.dumps([{"EventID": "4663"}, {"EventID": "4660"}])
        assert len(parse_json_lines(text)) == 2

    def test_unparseable_line_is_preserved_not_dropped(self):
        events = parse_json_lines("not json at all")
        assert len(events) == 1
        assert events[0]["message"] == "not json at all"


class TestParseAuditLogEntryPoint:
    def test_routes_namespaced_xml(self):
        events = parse_audit_log(NAMESPACED_XML.encode(), "audit/2026/08/07/log.xml")
        assert len(events) == 2
        assert events[0]["event_type"] == "4663"

    def test_routes_xml_without_suffix(self):
        events = parse_audit_log(NAMESPACED_XML.encode(), "audit/rotated_0001")
        assert len(events) == 2

    def test_handles_gzip(self):
        packed = gzip.compress(NAMESPACED_XML.encode())
        events = parse_audit_log(packed, "audit/log.xml.gz")
        assert len(events) == 2

    def test_corrupt_gzip_does_not_raise(self):
        events = parse_audit_log(b"not gzip data", "audit/log.xml.gz")
        assert isinstance(events, list)

    def test_empty_payload(self):
        assert parse_audit_log(b"", "audit/empty.xml") == []

    def test_routes_json(self):
        events = parse_audit_log(b'{"EventID":"4663"}', "audit/log.json")
        assert len(events) == 1
        assert events[0]["event_type"] == "4663"


class TestNormalizeEvent:
    def test_evtx_xml_field_names(self):
        n = normalize_event({
            "SubjectUserName": "u", "ObjectName": "/p", "ObjectType": "ReadData",
            "IpAddress": "198.51.100.1", "Keywords": "Audit Success",
            "Computer": "svm1", "EventID": "4663",
            "TimeCreated_SystemTime": "2026-08-07T04:00:00Z",
        })
        assert n["user"] == "u" and n["path"] == "/p" and n["operation"] == "ReadData"
        assert n["client_ip"] == "198.51.100.1" and n["result"] == "Audit Success"
        assert n["svm"] == "svm1" and n["event_type"] == "4663"
        assert n["timestamp"] == "2026-08-07T04:00:00Z"

    def test_ontap_json_field_names(self):
        n = normalize_event({
            "UserName": "u", "ClientIP": "198.51.100.2", "Operation": "WriteData",
            "SVMName": "svm2", "Result": "Success", "Timestamp": "t",
        })
        assert n["user"] == "u" and n["client_ip"] == "198.51.100.2"
        assert n["operation"] == "WriteData" and n["svm"] == "svm2"

    def test_already_normalized_is_idempotent(self):
        once = normalize_event({"user": "u", "path": "/p", "event_type": "4663"})
        twice = normalize_event(once)
        assert twice["user"] == "u" and twice["path"] == "/p"
        assert twice["event_type"] == "4663"

    def test_source_is_always_set(self):
        assert normalize_event({})["source"] == "fsxn-ontap"

    def test_missing_fields_become_empty_strings(self):
        n = normalize_event({})
        assert n["user"] == "" and n["path"] == "" and n["client_ip"] == ""
        # event_type falls back to a usable default rather than empty
        assert n["event_type"] == "audit"
