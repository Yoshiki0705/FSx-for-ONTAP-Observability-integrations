"""Tests for the shared FPolicy event plumbing."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fpolicy_event  # noqa: E402

SQS_DETAIL = {
    "operation_type": "create",
    "file_path": "/vol/data/a.txt",
    "user": "admin@corp.local",
    "client_ip": "198.51.100.10",
    "vserver": "svm-prod-01",
    "protocol": "cifs",
    "timestamp": "2026-08-07T12:00:00Z",
}

EB_DETAIL = {
    "operation": "write",
    "file_path": "/vol/data/b.txt",
    "user": "admin@corp.local",
    "client_ip": "198.51.100.11",
    "svm_name": "svm-prod-01",
    "protocol": "nfs",
}


def sqs_record(mid, body):
    return {"messageId": mid, "eventSource": "aws:sqs",
            "body": body if isinstance(body, str) else json.dumps(body)}


class TestIsSqsEvent:
    def test_sqs_batch(self):
        assert fpolicy_event.is_sqs_event({"Records": [sqs_record("m1", SQS_DETAIL)]})

    def test_eventbridge_event(self):
        assert not fpolicy_event.is_sqs_event({"source": "fpolicy.fsxn", "detail": {}})

    def test_empty_records_is_not_sqs(self):
        """Nothing to report per item, so the EventBridge path handles it."""
        assert not fpolicy_event.is_sqs_event({"Records": []})

    def test_records_not_a_list(self):
        assert not fpolicy_event.is_sqs_event({"Records": "nope"})

    def test_non_sqs_records(self):
        assert not fpolicy_event.is_sqs_event(
            {"Records": [{"eventSource": "aws:s3"}]}
        )


class TestParseSqsBatch:
    def test_all_valid(self):
        events, ids, failures = fpolicy_event.parse_sqs_batch(
            [sqs_record("m1", SQS_DETAIL), sqs_record("m2", EB_DETAIL)]
        )
        assert len(events) == 2
        assert ids == ["m1", "m2"]
        assert failures == []

    def test_events_and_ids_stay_aligned(self):
        """A formatter reporting per-message failures relies on index alignment."""
        events, ids, failures = fpolicy_event.parse_sqs_batch(
            [sqs_record("bad", "not json"), sqs_record("good", SQS_DETAIL)]
        )
        assert ids == ["good"]
        assert events[0]["file_path"] == "/vol/data/a.txt"
        assert failures == [{"itemIdentifier": "bad"}]

    def test_non_object_body_is_reported(self):
        events, ids, failures = fpolicy_event.parse_sqs_batch(
            [sqs_record("arr", "[1,2,3]")]
        )
        assert events == [] and ids == []
        assert failures == [{"itemIdentifier": "arr"}]

    def test_all_malformed_reports_all(self):
        _, _, failures = fpolicy_event.parse_sqs_batch(
            [sqs_record("b1", "{{"), sqs_record("b2", "null")]
        )
        assert {f["itemIdentifier"] for f in failures} == {"b1", "b2"}

    def test_missing_message_id_becomes_empty_string(self):
        _, _, failures = fpolicy_event.parse_sqs_batch(
            [{"eventSource": "aws:sqs", "body": "junk"}]
        )
        assert failures == [{"itemIdentifier": ""}]


class TestBatchResponse:
    def test_empty_means_full_success(self):
        assert fpolicy_event.batch_response([]) == {"batchItemFailures": []}

    def test_carries_failures(self):
        r = fpolicy_event.batch_response([{"itemIdentifier": "m1"}])
        assert r["batchItemFailures"][0]["itemIdentifier"] == "m1"


class TestExtractEventbridgeDetail:
    def test_returns_detail(self):
        assert fpolicy_event.extract_eventbridge_detail(
            {"detail": EB_DETAIL}
        ) == EB_DETAIL

    def test_missing_detail_raises(self):
        with pytest.raises(ValueError, match="detail is missing"):
            fpolicy_event.extract_eventbridge_detail({"source": "fpolicy.fsxn"})

    def test_wrong_detail_type_raises(self):
        with pytest.raises(ValueError, match="Unexpected detail type"):
            fpolicy_event.extract_eventbridge_detail({"detail": "str"})


class TestNormalizeFpolicyEvent:
    def test_sqs_field_names(self):
        n = fpolicy_event.normalize_fpolicy_event(SQS_DETAIL)
        assert n["operation_type"] == "create"
        assert n["svm"] == "svm-prod-01"
        assert n["client_ip"] == "198.51.100.10"

    def test_eventbridge_field_names_map_to_the_same_keys(self):
        """`operation`/`svm_name` must land on `operation_type`/`svm`."""
        n = fpolicy_event.normalize_fpolicy_event(EB_DETAIL)
        assert n["operation_type"] == "write"
        assert n["svm"] == "svm-prod-01"

    def test_missing_fields_become_empty_strings(self):
        n = fpolicy_event.normalize_fpolicy_event({})
        assert n["file_path"] == "" and n["user"] == "" and n["protocol"] == ""

    def test_operation_defaults_to_unknown(self):
        assert fpolicy_event.normalize_fpolicy_event({})["operation_type"] == "unknown"

    def test_svm_defaults_to_fsxn_ontap(self):
        assert fpolicy_event.normalize_fpolicy_event({})["svm"] == "fsxn-ontap"

    def test_raw_is_preserved(self):
        n = fpolicy_event.normalize_fpolicy_event(SQS_DETAIL)
        assert n["raw"] is SQS_DETAIL

    def test_empty_string_falls_through_to_next_candidate(self):
        """An explicit empty value must not shadow the alternate field name."""
        n = fpolicy_event.normalize_fpolicy_event(
            {"operation_type": "", "operation": "rename"}
        )
        assert n["operation_type"] == "rename"

    def test_non_string_values_are_stringified(self):
        n = fpolicy_event.normalize_fpolicy_event({"file_size": 1, "volume": 42})
        assert n["volume"] == "42"
