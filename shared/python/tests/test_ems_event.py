"""Tests for the shared EMS webhook plumbing."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ems_event  # noqa: E402

SAMPLE = {
    "messageName": "wafl.vol.autoSize.done",
    "severity": "NOTICE",
    "time": "2026-08-07T12:00:00Z",
    "node": "node-01",
    "svmName": "svm-prod-01",
    "message": "Volume autosize complete",
    "parameters": {"volume": "vol_data"},
}


class TestRequestId:
    def test_returns_request_id(self):
        assert ems_event.request_id({"requestContext": {"requestId": "abc"}}) == "abc"

    def test_missing_context_returns_unknown(self):
        assert ems_event.request_id({}) == "unknown"


class TestExtractEmsEvents:
    def test_single_object_body_string(self):
        out = ems_event.extract_ems_events({"body": json.dumps(SAMPLE)})
        assert out == [SAMPLE]

    def test_array_body_string(self):
        out = ems_event.extract_ems_events({"body": json.dumps([SAMPLE, SAMPLE])})
        assert len(out) == 2

    def test_already_parsed_dict_body(self):
        """Direct invocation and tests pass an object, not a JSON string."""
        assert ems_event.extract_ems_events({"body": SAMPLE}) == [SAMPLE]

    def test_already_parsed_list_body(self):
        assert len(ems_event.extract_ems_events({"body": [SAMPLE]})) == 1

    def test_non_dict_array_entries_are_dropped(self):
        """A stray scalar must not reach the parser and kill the batch."""
        out = ems_event.extract_ems_events({"body": json.dumps([SAMPLE, "junk", 5])})
        assert out == [SAMPLE]

    def test_missing_body_raises(self):
        with pytest.raises(ValueError, match="body is missing"):
            ems_event.extract_ems_events({})

    def test_empty_body_raises(self):
        with pytest.raises(ValueError, match="body is empty"):
            ems_event.extract_ems_events({"body": "   "})

    def test_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            ems_event.extract_ems_events({"body": "not json {{"})

    def test_scalar_body_raises(self):
        with pytest.raises(ValueError, match="Unexpected body type"):
            ems_event.extract_ems_events({"body": "42"})


class TestNormalizeEmsEvents:
    def test_normalizes_via_parser(self):
        out = ems_event.normalize_ems_events([SAMPLE])
        assert len(out) == 1
        # With the layer present the parser maps messageName -> event_name.
        if ems_event._parse_ems_event is not None:
            assert out[0]["event_name"] == "wafl.vol.autoSize.done"
            assert out[0]["svm"] == "svm-prod-01"

    def test_unparseable_event_is_skipped_not_raised(self, caplog):
        """A synchronous API Gateway call must not 5xx over one bad event."""
        if ems_event._parse_ems_event is None:
            pytest.skip("ems_parser layer not importable in this environment")
        with caplog.at_level(logging.WARNING):
            out = ems_event.normalize_ems_events([{"no": "messageName"}, SAMPLE])
        assert len(out) == 1
        assert "Skipping unparseable EMS event" in caplog.text

    def test_empty_list(self):
        assert ems_event.normalize_ems_events([]) == []

    def test_degrades_to_passthrough_without_layer(self, monkeypatch, caplog):
        """Without the layer the events still ship, just unparsed."""
        monkeypatch.setattr(ems_event, "_parse_ems_event", None)
        with caplog.at_level(logging.WARNING):
            out = ems_event.normalize_ems_events([SAMPLE])
        assert out == [SAMPLE]
        assert "ems_parser layer is not available" in caplog.text


class TestSeverityOf:
    def test_lowercases(self):
        assert ems_event.severity_of({"severity": "ERROR"}) == "error"

    def test_strips_whitespace(self):
        assert ems_event.severity_of({"severity": "  ALERT "}) == "alert"

    def test_empty_falls_back_to_default(self):
        assert ems_event.severity_of({}) == "info"
        assert ems_event.severity_of({"severity": ""}, default="warning") == "warning"


class TestApiResponse:
    def test_shape(self):
        r = ems_event.api_response(200, {"shipped": 3})
        assert r["statusCode"] == 200
        assert r["headers"]["Content-Type"] == "application/json"
        assert json.loads(r["body"]) == {"shipped": 3}

    def test_serializes_non_json_types(self):
        """default=str keeps a datetime or Decimal from raising."""
        from datetime import datetime

        r = ems_event.api_response(200, {"at": datetime(2026, 8, 7)})
        assert "2026-08-07" in json.loads(r["body"])["at"]
