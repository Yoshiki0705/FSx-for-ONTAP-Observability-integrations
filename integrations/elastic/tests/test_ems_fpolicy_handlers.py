"""Tests for the Elastic EMS and FPolicy handlers.

These handlers are thin: the API Gateway / SQS plumbing lives in
``shared/python/{ems,fpolicy}_event.py`` and the retry policy in
``vendor_shipper.py``, all covered by ``shared/python/tests/``. What is asserted
here is the part that is specific to Elastic: the payload shape, the endpoint,
the auth header, and that every failure path refuses to report success.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lambda"))

import ems_handler  # noqa: E402
import fpolicy_handler  # noqa: E402

EMS_EVENT = {
    "messageName": "wafl.vol.autoSize.done",
    "severity": "NOTICE",
    "time": "2026-08-07T12:00:00Z",
    "node": "node-01",
    "svmName": "svm-prod-01",
    "message": "Volume autosize complete",
    "parameters": {"volume": "vol_data"},
}

FPOLICY_EVENT = {
    "operation_type": "create",
    "file_path": "/vol/data/a.txt",
    "user": "admin@corp.local",
    "client_ip": "198.51.100.10",
    "vserver": "svm-prod-01",
    "protocol": "cifs",
    "timestamp": "2026-08-07T12:00:00Z",
}


def sqs_event(*pairs):
    """Build an SQS event source mapping payload from (messageId, body) pairs."""
    return {
        "Records": [
            {
                "messageId": mid,
                "eventSource": "aws:sqs",
                "body": body if isinstance(body, str) else json.dumps(body),
            }
            for mid, body in pairs
        ]
    }


@pytest.fixture
def sent():
    """Capture what would have been POSTed instead of making a request."""
    calls: list[tuple[str, dict, bytes]] = []

    def capture(http, url, body, headers, logger=None, **kwargs):
        calls.append((url, headers, body))
        return True

    return calls, capture


# ─── EMS ───────────────────────────────────────────────────────────────────


class TestEmsHandler:
    def test_ships_and_returns_200(self, sent):
        calls, capture = sent
        with patch.object(ems_handler.CREDENTIAL, "get", return_value="CRED"), \
             patch.object(ems_handler, "post_with_retry", capture):
            r = ems_handler.lambda_handler({"body": json.dumps(EMS_EVENT)}, None)
        assert r["statusCode"] == 200
        assert json.loads(r["body"])["shipped"] == 1
        assert len(calls) == 1

    def test_payload_carries_parsed_ems_fields(self, sent):
        """Guards against shipping the raw webhook body with no field extraction."""
        calls, capture = sent
        with patch.object(ems_handler.CREDENTIAL, "get", return_value="CRED"), \
             patch.object(ems_handler, "post_with_retry", capture):
            ems_handler.lambda_handler({"body": json.dumps(EMS_EVENT)}, None)
        payload = calls[0][2]
        text = payload.decode("utf-8", errors="replace") if isinstance(
            payload, (bytes, bytearray)) else str(payload)
        if payload[:2] == b"\x1f\x8b":  # gzip
            import gzip
            text = gzip.decompress(payload).decode("utf-8")
        assert "wafl.vol.autoSize.done" in text
        assert "svm-prod-01" in text

    def test_auth_header_present(self, sent):
        calls, capture = sent
        with patch.object(ems_handler.CREDENTIAL, "get", return_value="CRED"), \
             patch.object(ems_handler, "post_with_retry", capture):
            ems_handler.lambda_handler({"body": json.dumps(EMS_EVENT)}, None)
        headers = calls[0][1]
        assert headers["Authorization"].startswith("ApiKey ")

    def test_array_body_ships_every_event(self, sent):
        calls, capture = sent
        with patch.object(ems_handler.CREDENTIAL, "get", return_value="CRED"), \
             patch.object(ems_handler, "post_with_retry", capture):
            r = ems_handler.lambda_handler(
                {"body": json.dumps([EMS_EVENT, EMS_EVENT, EMS_EVENT])}, None
            )
        assert json.loads(r["body"])["shipped"] == 3

    def test_invalid_json_returns_400(self):
        r = ems_handler.lambda_handler({"body": "not json {{"}, None)
        assert r["statusCode"] == 400

    def test_missing_body_returns_400(self):
        assert ems_handler.lambda_handler({}, None)["statusCode"] == 400

    def test_all_events_unparseable_returns_422_not_200(self):
        """Reporting success here would make a schema break look like idleness."""
        with patch.object(ems_handler.CREDENTIAL, "get", return_value="CRED"):
            r = ems_handler.lambda_handler({"body": json.dumps({"bad": 1})}, None)
        assert r["statusCode"] == 422

    def test_delivery_failure_returns_207(self):
        with patch.object(ems_handler.CREDENTIAL, "get", return_value="CRED"), \
             patch.object(ems_handler, "post_with_retry", lambda *a, **k: False):
            r = ems_handler.lambda_handler({"body": json.dumps(EMS_EVENT)}, None)
        assert r["statusCode"] == 207
        assert json.loads(r["body"])["shipped"] == 0

    def test_credential_failure_returns_502_without_shipping(self, sent):
        calls, capture = sent
        with patch.object(ems_handler.CREDENTIAL, "get", side_effect=RuntimeError("no")), \
             patch.object(ems_handler, "post_with_retry", capture):
            r = ems_handler.lambda_handler({"body": json.dumps(EMS_EVENT)}, None)
        assert r["statusCode"] == 502
        assert calls == []


# ─── FPolicy ───────────────────────────────────────────────────────────────


class TestFpolicySqsPath:
    def test_returns_batch_item_failures_shape(self, sent):
        _, capture = sent
        with patch.object(fpolicy_handler.CREDENTIAL, "get", return_value="CRED"), \
             patch.object(fpolicy_handler, "post_with_retry", capture):
            r = fpolicy_handler.lambda_handler(sqs_event(("m1", FPOLICY_EVENT)), None)
        assert r == {"batchItemFailures": []}
        assert "statusCode" not in r

    def test_payload_carries_fpolicy_fields(self, sent):
        calls, capture = sent
        with patch.object(fpolicy_handler.CREDENTIAL, "get", return_value="CRED"), \
             patch.object(fpolicy_handler, "post_with_retry", capture):
            fpolicy_handler.lambda_handler(sqs_event(("m1", FPOLICY_EVENT)), None)
        payload = calls[0][2]
        if payload[:2] == b"\x1f\x8b":
            import gzip
            text = gzip.decompress(payload).decode("utf-8")
        else:
            text = payload.decode("utf-8", errors="replace")
        assert "/vol/data/a.txt" in text
        assert "198.51.100.10" in text

    def test_unparseable_message_reported_valid_one_ships(self, sent):
        calls, capture = sent
        with patch.object(fpolicy_handler.CREDENTIAL, "get", return_value="CRED"), \
             patch.object(fpolicy_handler, "post_with_retry", capture):
            r = fpolicy_handler.lambda_handler(
                sqs_event(("bad", "not json"), ("good", FPOLICY_EVENT)), None
            )
        assert r["batchItemFailures"] == [{"itemIdentifier": "bad"}]
        assert len(calls) == 1

    def test_delivery_failure_reports_every_parsed_message(self):
        with patch.object(fpolicy_handler.CREDENTIAL, "get", return_value="CRED"), \
             patch.object(fpolicy_handler, "post_with_retry", lambda *a, **k: False):
            r = fpolicy_handler.lambda_handler(
                sqs_event(("m1", FPOLICY_EVENT), ("m2", FPOLICY_EVENT)), None
            )
        assert {f["itemIdentifier"] for f in r["batchItemFailures"]} == {"m1", "m2"}

    def test_credential_failure_raises_so_sqs_redelivers(self):
        """Returning a response would delete the messages."""
        with patch.object(fpolicy_handler.CREDENTIAL, "get",
                          side_effect=RuntimeError("no")):
            with pytest.raises(RuntimeError):
                fpolicy_handler.lambda_handler(sqs_event(("m1", FPOLICY_EVENT)), None)


class TestFpolicyEventBridgePath:
    def test_keeps_status_code_contract(self, sent):
        _, capture = sent
        with patch.object(fpolicy_handler.CREDENTIAL, "get", return_value="CRED"), \
             patch.object(fpolicy_handler, "post_with_retry", capture):
            r = fpolicy_handler.lambda_handler(
                {"source": "fpolicy.fsxn", "detail": FPOLICY_EVENT}, None
            )
        assert r["statusCode"] == 200
        assert "batchItemFailures" not in r

    def test_eventbridge_field_names_are_normalized(self, sent):
        """`operation`/`svm_name` must reach the payload, not come out empty."""
        calls, capture = sent
        detail = {"operation": "rename", "file_path": "/x", "user": "u",
                  "client_ip": "198.51.100.9", "svm_name": "svm-eb"}
        with patch.object(fpolicy_handler.CREDENTIAL, "get", return_value="CRED"), \
             patch.object(fpolicy_handler, "post_with_retry", capture):
            fpolicy_handler.lambda_handler(
                {"source": "fpolicy.fsxn", "detail": detail}, None
            )
        payload = calls[0][2]
        if payload[:2] == b"\x1f\x8b":
            import gzip
            text = gzip.decompress(payload).decode("utf-8")
        else:
            text = payload.decode("utf-8", errors="replace")
        assert "rename" in text and "svm-eb" in text

    def test_missing_detail_returns_400(self):
        r = fpolicy_handler.lambda_handler({"source": "fpolicy.fsxn"}, None)
        assert r["statusCode"] == 400

    def test_delivery_failure_returns_207(self):
        with patch.object(fpolicy_handler.CREDENTIAL, "get", return_value="CRED"), \
             patch.object(fpolicy_handler, "post_with_retry", lambda *a, **k: False):
            r = fpolicy_handler.lambda_handler(
                {"source": "fpolicy.fsxn", "detail": FPOLICY_EVENT}, None
            )
        assert r["statusCode"] == 207
