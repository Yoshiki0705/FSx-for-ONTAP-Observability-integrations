"""Tests for the shared FSx for ONTAP log-parser layer.

Focus: the XML path, which has two implementations selected by file size
(`_parse_xml_dom` below the streaming threshold, `_parse_xml_streaming` above).
Both must produce identical results for the same input — a divergence there means
audit records are silently lost or merged depending only on how large the rotated
file happened to be.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "python"))

from fsxn_log_parser import parser  # noqa: E402


class TestNamespacedXmlParsing:
    """ONTAP emits the Windows Event Log XML schema, which is namespaced.

    ElementTree reports such tags as "{uri}Event", so a plain iter("Event")
    matches nothing. The DOM path previously fell through to its flat-record
    fallback and merged the whole file into one record, discarding every event
    but the last. The streaming path never had this problem, so the same file
    parsed correctly or incorrectly depending on its size.
    """

    def test_dom_parser_keeps_events_separate(self, namespaced_ontap_xml):
        events = parser._parse_xml_dom(namespaced_ontap_xml)

        assert len(events) == 2, "each <Event> must produce its own record"
        assert [e["event_type"] for e in events] == ["4663", "4660"]

    def test_streaming_parser_keeps_events_separate(self, namespaced_ontap_xml):
        events = parser._parse_xml_streaming(namespaced_ontap_xml)

        assert len(events) == 2
        assert [e["event_type"] for e in events] == ["4663", "4660"]

    def test_dom_and_streaming_agree(self, namespaced_ontap_xml):
        """The chosen implementation must not change the result."""
        dom = parser._parse_xml_dom(namespaced_ontap_xml)
        streaming = parser._parse_xml_streaming(namespaced_ontap_xml)

        assert len(dom) == len(streaming)
        for a, b in zip(dom, streaming):
            assert a["event_type"] == b["event_type"]
            assert a.get("user") == b.get("user")
            assert a.get("path") == b.get("path")

    def test_first_event_is_not_lost(self, namespaced_ontap_xml):
        """Regression: the merged-record bug kept only the last event."""
        events = parser._parse_xml_dom(namespaced_ontap_xml)

        paths = [e.get("path") for e in events]
        assert "/vol/data/first.txt" in paths
        assert "/vol/data/second.docx" in paths

    def test_namespaced_fields_still_extract(self, namespaced_ontap_xml):
        """Namespace stripping must not break Data Name= extraction."""
        first = parser._parse_xml_dom(namespaced_ontap_xml)[0]

        assert first.get("svm") == "svm-prod-01"
        assert first.get("user") == "CORP\\jdoe"
        assert first.get("client_ip") == "198.51.100.10"


class TestPlainXmlParsing:
    """Non-namespaced documents must keep working."""

    def test_dom_parser(self, plain_ontap_xml):
        events = parser._parse_xml_dom(plain_ontap_xml)
        assert len(events) == 2
        assert [e.get("user") for e in events] == ["u1", "u2"]

    def test_streaming_parser(self, plain_ontap_xml):
        events = parser._parse_xml_streaming(plain_ontap_xml)
        assert len(events) == 2


class TestXmlFallback:
    """Documents with no <Event> elements at all."""

    def test_wrapper_records_are_not_merged(self):
        """A wrapper around N records must yield N records, not one."""
        xml = """<?xml version="1.0"?>
<AuditRecords>
  <Record><EventID>1</EventID><UserName>a</UserName></Record>
  <Record><EventID>2</EventID><UserName>b</UserName></Record>
  <Record><EventID>3</EventID><UserName>c</UserName></Record>
</AuditRecords>"""
        events = parser._parse_xml_dom(xml)

        assert len(events) == 3
        assert [e.get("user") for e in events] == ["a", "b", "c"]

    def test_malformed_xml_does_not_raise(self):
        """A truncated rotation must degrade, not crash the shipper."""
        events = parser._parse_xml_dom("<Events><Event><EventID>1</EventID>")
        assert isinstance(events, list)


class TestPublicParseEntryPoint:
    """The public parse() surface used by vendor handlers.

    parse() returns a result mapping (events / errors / format / timing), not a
    bare list — vendor handlers must read result["events"].
    """

    def test_parse_returns_a_result_mapping(self, namespaced_ontap_xml):
        result = parser.parse(namespaced_ontap_xml.encode("utf-8"), "audit/log.xml")

        assert set(result).issuperset({"events", "errors", "format"})
        assert result["format"] == "xml"

    def test_parse_routes_xml_by_content(self, namespaced_ontap_xml):
        result = parser.parse(namespaced_ontap_xml.encode("utf-8"), "audit/log.xml")

        assert len(result["events"]) == 2
        assert not result["errors"]

    def test_parse_handles_empty_input(self):
        result = parser.parse(b"", "audit/empty.xml")

        assert result["events"] == []
