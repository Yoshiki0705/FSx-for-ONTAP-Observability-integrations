"""Unit tests for object_ledger module.

Tests cover:
- should_process claim logic (conditional PutItem, ConditionalCheckFailedException)
- mark_success / mark_failure state transitions
- Poison-pill auto-promotion after max_failures
- get_poison_pills / get_status read paths

All tests run against a mocked boto3 DynamoDB client (`dynamodb_client`
parameter) -- none of them exercise a real DynamoDB table.
"""

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

sys.path.insert(0, str(Path(__file__).parent.parent))

from object_ledger import ObjectLedger


def make_conditional_check_failed() -> ClientError:
    """Build a ClientError matching DynamoDB's ConditionalCheckFailedException."""
    return ClientError(
        error_response={
            "Error": {
                "Code": "ConditionalCheckFailedException",
                "Message": "The conditional request failed",
            }
        },
        operation_name="PutItem",
    )


@pytest.fixture
def ddb_client():
    return MagicMock()


@pytest.fixture
def ledger(ddb_client):
    return ObjectLedger(
        table_name="test-ledger",
        max_failures=3,
        dynamodb_client=ddb_client,
    )


# ==========================================================================
# should_process
# ==========================================================================


class TestShouldProcess:
    def test_should_process_claims_new_object(self, ledger, ddb_client):
        ddb_client.put_item.return_value = {}

        result = ledger.should_process("audit/2026/01/15/log.json", "etag-abc")

        assert result is True
        ddb_client.put_item.assert_called_once()
        call_kwargs = ddb_client.put_item.call_args.kwargs
        assert call_kwargs["Item"]["s3_key"] == {"S": "audit/2026/01/15/log.json"}
        assert call_kwargs["Item"]["etag"] == {"S": "etag-abc"}
        assert call_kwargs["Item"]["status"] == {"S": "processing"}
        assert "ConditionExpression" in call_kwargs

    def test_should_process_records_worker_id(self, ledger, ddb_client):
        ddb_client.put_item.return_value = {}

        ledger.should_process("key1", "etag1", worker_id="req-123")

        call_kwargs = ddb_client.put_item.call_args.kwargs
        assert call_kwargs["Item"]["worker_id"] == {"S": "req-123"}

    def test_should_process_skips_already_succeeded_same_etag(self, ledger, ddb_client):
        ddb_client.put_item.side_effect = make_conditional_check_failed()

        result = ledger.should_process("key1", "etag1")

        assert result is False

    def test_should_process_skips_poison_pill(self, ledger, ddb_client):
        # Same failure mode surfaces as ConditionalCheckFailedException --
        # the condition expression excludes both "success" (same etag) and
        # "poison_pill" regardless of etag.
        ddb_client.put_item.side_effect = make_conditional_check_failed()

        result = ledger.should_process("key1", "any-etag")

        assert result is False

    def test_should_process_reraises_other_client_errors(self, ledger, ddb_client):
        ddb_client.put_item.side_effect = ClientError(
            error_response={
                "Error": {"Code": "ProvisionedThroughputExceededException", "Message": "x"}
            },
            operation_name="PutItem",
        )

        with pytest.raises(ClientError):
            ledger.should_process("key1", "etag1")


# ==========================================================================
# mark_success
# ==========================================================================


class TestMarkSuccess:
    def test_mark_success_updates_status_and_resets_failure_count(self, ledger, ddb_client):
        ledger.mark_success("key1", "etag1")

        ddb_client.update_item.assert_called_once()
        call_kwargs = ddb_client.update_item.call_args.kwargs
        assert call_kwargs["Key"] == {"s3_key": {"S": "key1"}}
        assert call_kwargs["ExpressionAttributeValues"][":status"] == {"S": "success"}
        assert call_kwargs["ExpressionAttributeValues"][":etag"] == {"S": "etag1"}
        assert call_kwargs["ExpressionAttributeValues"][":zero"] == {"N": "0"}


# ==========================================================================
# mark_failure / poison-pill promotion
# ==========================================================================


class TestMarkFailure:
    def test_mark_failure_below_threshold_does_not_promote(self, ledger, ddb_client):
        ddb_client.update_item.return_value = {
            "Attributes": {"failure_count": {"N": "1"}}
        }

        ledger.mark_failure("key1", "etag1", "boom")

        # Only the failure-recording update_item call, no poison-pill promotion.
        assert ddb_client.update_item.call_count == 1

    def test_mark_failure_at_threshold_promotes_to_poison_pill(self, ledger, ddb_client):
        ddb_client.update_item.return_value = {
            "Attributes": {"failure_count": {"N": "3"}}
        }

        ledger.mark_failure("key1", "etag1", "boom")

        # First call records the failure, second call promotes to poison_pill.
        assert ddb_client.update_item.call_count == 2
        poison_call_kwargs = ddb_client.update_item.call_args_list[1].kwargs
        assert poison_call_kwargs["ExpressionAttributeValues"][":status"] == {
            "S": "poison_pill"
        }

    def test_mark_failure_above_threshold_still_promotes(self, ledger, ddb_client):
        ddb_client.update_item.return_value = {
            "Attributes": {"failure_count": {"N": "5"}}
        }

        ledger.mark_failure("key1", "etag1", "boom")

        assert ddb_client.update_item.call_count == 2

    def test_mark_failure_truncates_long_error_messages(self, ledger, ddb_client):
        ddb_client.update_item.return_value = {
            "Attributes": {"failure_count": {"N": "1"}}
        }
        long_error = "x" * 1000

        ledger.mark_failure("key1", "etag1", long_error)

        call_kwargs = ddb_client.update_item.call_args_list[0].kwargs
        stored_error = call_kwargs["ExpressionAttributeValues"][":err"]["S"]
        assert len(stored_error) == 500

    def test_mark_failure_swallows_client_error(self, ledger, ddb_client):
        ddb_client.update_item.side_effect = ClientError(
            error_response={"Error": {"Code": "InternalServerError", "Message": "x"}},
            operation_name="UpdateItem",
        )

        # Should not raise -- mark_failure logs and returns.
        ledger.mark_failure("key1", "etag1", "boom")


# ==========================================================================
# get_poison_pills
# ==========================================================================


class TestGetPoisonPills:
    def test_get_poison_pills_returns_parsed_items(self, ledger, ddb_client):
        ddb_client.scan.return_value = {
            "Items": [
                {
                    "s3_key": {"S": "bad/file.json"},
                    "last_error": {"S": "parse error"},
                    "failure_count": {"N": "3"},
                    "failed_at": {"N": "1700000000"},
                }
            ]
        }

        results = ledger.get_poison_pills()

        assert results == [
            {
                "key": "bad/file.json",
                "last_error": "parse error",
                "failure_count": 3,
                "failed_at": 1700000000,
            }
        ]
        scan_kwargs = ddb_client.scan.call_args.kwargs
        assert scan_kwargs["ExpressionAttributeValues"][":poison"] == {"S": "poison_pill"}

    def test_get_poison_pills_empty_result(self, ledger, ddb_client):
        ddb_client.scan.return_value = {"Items": []}

        results = ledger.get_poison_pills()

        assert results == []

    def test_get_poison_pills_respects_limit(self, ledger, ddb_client):
        ddb_client.scan.return_value = {"Items": []}

        ledger.get_poison_pills(limit=10)

        assert ddb_client.scan.call_args.kwargs["Limit"] == 10


# ==========================================================================
# get_status
# ==========================================================================


class TestGetStatus:
    def test_get_status_returns_parsed_item(self, ledger, ddb_client):
        ddb_client.get_item.return_value = {
            "Item": {
                "s3_key": {"S": "key1"},
                "status": {"S": "success"},
                "etag": {"S": "etag1"},
                "failure_count": {"N": "0"},
                "last_error": {"S": ""},
                "processed_at": {"N": "1700000000"},
            }
        }

        result = ledger.get_status("key1")

        assert result == {
            "key": "key1",
            "status": "success",
            "etag": "etag1",
            "failure_count": 0,
            "last_error": "",
            "processed_at": 1700000000,
        }

    def test_get_status_returns_none_when_missing(self, ledger, ddb_client):
        ddb_client.get_item.return_value = {}

        result = ledger.get_status("nonexistent")

        assert result is None

    def test_get_status_defaults_missing_attributes(self, ledger, ddb_client):
        ddb_client.get_item.return_value = {
            "Item": {"s3_key": {"S": "key1"}}
        }

        result = ledger.get_status("key1")

        assert result["status"] == "unknown"
        assert result["etag"] == ""
        assert result["failure_count"] == 0


# ==========================================================================
# Retention (DynamoDB TTL)
# ==========================================================================


class TestTtlConfiguration:
    """How ttl_days is resolved.

    DynamoDB expires an item only if it carries the attribute named in the
    table's TimeToLiveSpecification. The template enabling TTL is therefore only
    half of retention -- this class has to write expires_at. Both halves were
    missing while the stack exposed a TTLDays parameter, so retention read as
    configured while the table grew without bound.
    """

    def test_defaults_to_no_expiry(self, ddb_client):
        ledger = ObjectLedger(table_name="t", dynamodb_client=ddb_client)
        assert ledger.ttl_days == 0

    def test_explicit_ttl_days_wins_over_environment(self, ddb_client, monkeypatch):
        monkeypatch.setenv("LEDGER_TTL_DAYS", "30")
        ledger = ObjectLedger(
            table_name="t", dynamodb_client=ddb_client, ttl_days=7
        )
        assert ledger.ttl_days == 7

    def test_reads_ledger_ttl_days_environment_variable(self, ddb_client, monkeypatch):
        monkeypatch.setenv("LEDGER_TTL_DAYS", "45")
        ledger = ObjectLedger(table_name="t", dynamodb_client=ddb_client)
        assert ledger.ttl_days == 45

    def test_non_numeric_environment_value_disables_expiry(
        self, ddb_client, monkeypatch
    ):
        """A typo must not crash the shipper on cold start."""
        monkeypatch.setenv("LEDGER_TTL_DAYS", "ninety")
        ledger = ObjectLedger(table_name="t", dynamodb_client=ddb_client)
        assert ledger.ttl_days == 0

    def test_negative_ttl_days_is_clamped_to_zero(self, ddb_client):
        """A negative TTL would write an expiry in the past, deleting on write."""
        ledger = ObjectLedger(
            table_name="t", dynamodb_client=ddb_client, ttl_days=-5
        )
        assert ledger.ttl_days == 0


class TestMarkSuccessWritesExpiry:
    def test_no_expires_at_when_ttl_disabled(self, ledger, ddb_client):
        ledger.mark_success("key1", "etag-abc")

        call = ddb_client.update_item.call_args.kwargs
        assert "expires_at" not in call["UpdateExpression"]
        assert ":exp" not in call["ExpressionAttributeValues"]

    def test_writes_expires_at_when_ttl_enabled(self, ddb_client):
        ledger = ObjectLedger(
            table_name="t", dynamodb_client=ddb_client, ttl_days=90
        )
        before = int(time.time())

        ledger.mark_success("key1", "etag-abc")

        call = ddb_client.update_item.call_args.kwargs
        assert "expires_at = :exp" in call["UpdateExpression"]
        expiry = int(call["ExpressionAttributeValues"][":exp"]["N"])
        expected = before + 90 * 86400
        assert expected <= expiry <= expected + 5

    def test_still_sets_the_other_success_attributes(self, ddb_client):
        ledger = ObjectLedger(
            table_name="t", dynamodb_client=ddb_client, ttl_days=30
        )
        ledger.mark_success("key1", "etag-abc")

        call = ddb_client.update_item.call_args.kwargs
        update = call["UpdateExpression"]
        for fragment in ("#s = :status", "etag = :etag", "processed_at = :ts",
                         "failure_count = :zero"):
            assert fragment in update


class TestPoisonPillNeverExpires:
    """Poison pills are the permanent skip list.

    If one expires, should_process starts returning True for a file already known
    to be unprocessable. The pipeline then re-enters the same failure loop, marks
    it poison again, and the entry expires again -- so the loop repeats forever
    rather than once.
    """

    def test_poison_pill_removes_expires_at(self, ddb_client):
        ledger = ObjectLedger(
            table_name="t", dynamodb_client=ddb_client, ttl_days=90
        )

        ledger._mark_poison_pill("key1")

        call = ddb_client.update_item.call_args.kwargs
        assert "REMOVE expires_at" in call["UpdateExpression"]

    def test_promotion_path_removes_expires_at(self, ddb_client):
        """Reaching max_failures must clear any expiry left by a prior success."""
        ledger = ObjectLedger(
            table_name="t",
            max_failures=3,
            dynamodb_client=ddb_client,
            ttl_days=90,
        )
        ddb_client.update_item.return_value = {
            "Attributes": {"failure_count": {"N": "3"}}
        }

        ledger.mark_failure("key1", "etag-abc", "boom")

        # Last call is the poison-pill promotion.
        assert "REMOVE expires_at" in (
            ddb_client.update_item.call_args.kwargs["UpdateExpression"]
        )

    def test_failure_below_threshold_does_not_set_expiry(self, ddb_client):
        """A failed entry is not a success, so it gets no retention window."""
        ledger = ObjectLedger(
            table_name="t",
            max_failures=3,
            dynamodb_client=ddb_client,
            ttl_days=90,
        )
        ddb_client.update_item.return_value = {
            "Attributes": {"failure_count": {"N": "1"}}
        }

        ledger.mark_failure("key1", "etag-abc", "boom")

        call = ddb_client.update_item.call_args.kwargs
        assert "expires_at" not in call["UpdateExpression"]


class TestTemplateAndWriterAgree:
    """The template's TTL attribute name must match what this module writes.

    A mismatch is invisible: DynamoDB accepts the table configuration, the Lambda
    writes its attribute, and nothing ever expires.
    """

    TEMPLATE = (
        Path(__file__).resolve().parents[3]
        / "shared/templates/object-ledger.yaml"
    )

    def test_template_enables_ttl_on_expires_at(self):
        template = self.TEMPLATE.read_text(encoding="utf-8")
        assert "TimeToLiveSpecification" in template, (
            "object-ledger.yaml must enable TTL, or expires_at is written and "
            "never acted on."
        )
        assert "AttributeName: expires_at" in template, (
            "The template's TTL attribute must be expires_at, which is what "
            "ObjectLedger writes."
        )

    def test_writer_uses_the_same_attribute_name(self):
        source = (
            Path(__file__).resolve().parents[1] / "object_ledger.py"
        ).read_text(encoding="utf-8")
        assert "expires_at" in source
