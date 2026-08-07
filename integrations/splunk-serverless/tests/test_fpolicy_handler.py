"""Unit tests for FPolicy event handler (Splunk shipper).

Tests cover:
- SQS batch event extraction and shipping -> 200 with shipped count
- EventBridge single event extraction and shipping -> 200
- Missing/invalid event detail -> 400
- HEC failure after retries -> 207 (partial) status
- HEC token retrieval failure -> 502
- Malformed SQS message body is skipped, valid ones still ship
- HEC payload uses fpolicy sourcetype/index
"""

import json
import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# Set environment variables BEFORE importing fpolicy_handler (module reads at load time)
os.environ.setdefault("SPLUNK_HEC_ENDPOINT", "https://splunk.example.com:8088")
os.environ.setdefault(
    "HEC_TOKEN_SECRET_ARN",
    "arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:splunk-hec-token",
)

# Add lambda directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "lambda"))

import fpolicy_handler


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_caches() -> None:
    """Reset module-level caches between tests."""
    fpolicy_handler._hec_token_cache = None


@pytest.fixture(autouse=True)
def fpolicy_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set required environment variables and module-level constants for FPolicy handler tests."""
    monkeypatch.setenv("SPLUNK_HEC_ENDPOINT", "https://splunk.example.com:8088")
    monkeypatch.setenv(
        "HEC_TOKEN_SECRET_ARN",
        "arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:splunk-hec-token",
    )
    monkeypatch.setattr(fpolicy_handler, "HEC_ENDPOINT", "https://splunk.example.com:8088")
    monkeypatch.setattr(
        fpolicy_handler,
        "HEC_TOKEN_SECRET_ARN",
        "arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:splunk-hec-token",
    )


@pytest.fixture
def valid_hec_token() -> str:
    """Valid Splunk HEC token."""
    return "a1b2c3d4-e5f6-7890-abcd-ef1234567890"


@pytest.fixture
def fpolicy_event_detail() -> dict[str, Any]:
    """A single FPolicy file operation event (SQS/FPolicy server field names)."""
    return {
        "operation_type": "CREATE",
        "file_path": "/vol1/test-data/sample.txt",
        "user": "CORP\\testuser",
        "client_ip": "192.168.10.5",
        "svm_name": "fsxsvm01",
        "protocol": "SMB",
        "timestamp": "2026-07-11T12:00:00Z",
    }


@pytest.fixture
def sqs_event(fpolicy_event_detail: dict[str, Any]) -> dict[str, Any]:
    """SQS event source mapping event with a single FPolicy record."""
    return {
        "Records": [
            {
                "messageId": "msg-1",
                "eventSource": "aws:sqs",
                "body": json.dumps(fpolicy_event_detail),
            }
        ]
    }


@pytest.fixture
def sqs_event_batch(fpolicy_event_detail: dict[str, Any]) -> dict[str, Any]:
    """SQS event source mapping event with multiple FPolicy records."""
    second = dict(fpolicy_event_detail)
    second["operation_type"] = "DELETE"
    second["file_path"] = "/vol1/test-data/old.txt"
    return {
        "Records": [
            {
                "messageId": "msg-1",
                "eventSource": "aws:sqs",
                "body": json.dumps(fpolicy_event_detail),
            },
            {
                "messageId": "msg-2",
                "eventSource": "aws:sqs",
                "body": json.dumps(second),
            },
        ]
    }


@pytest.fixture
def eventbridge_event(fpolicy_event_detail: dict[str, Any]) -> dict[str, Any]:
    """EventBridge event with FPolicy data in the detail field."""
    return {
        "source": "fpolicy.fsxn",
        "detail-type": "CREATE",
        "detail": fpolicy_event_detail,
    }


@pytest.fixture
def mock_secrets(valid_hec_token: str) -> MagicMock:
    """Mock Secrets Manager client returning a valid HEC token."""
    with patch("fpolicy_handler.secrets_client") as mock_client:
        mock_client.get_secret_value.return_value = {"SecretString": valid_hec_token}
        yield mock_client


@pytest.fixture
def mock_http_success() -> MagicMock:
    """Mock urllib3 PoolManager returning successful HEC response."""
    with patch("fpolicy_handler.http") as mock_http:
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.data = json.dumps({"text": "Success", "code": 0}).encode("utf-8")
        mock_http.request.return_value = mock_response
        yield mock_http


@pytest.fixture
def mock_http_failure() -> MagicMock:
    """Mock urllib3 PoolManager returning 503 on all attempts."""
    with patch("fpolicy_handler.http") as mock_http:
        mock_response = MagicMock()
        mock_response.status = 503
        mock_response.data = json.dumps(
            {"text": "Server Error", "code": 9}
        ).encode("utf-8")
        mock_http.request.return_value = mock_response
        yield mock_http


# ---------------------------------------------------------------------------
# Tests: SQS batch -> shipped successfully
# ---------------------------------------------------------------------------


class TestSqsEventShipping:
    """Tests for the primary SQS -> Lambda -> Splunk HEC path."""

    def test_single_sqs_record_reports_no_failures(
        self,
        sqs_event: dict[str, Any],
        mock_secrets: MagicMock,
        mock_http_success: MagicMock,
    ) -> None:
        """A single SQS record ships and reports an empty batchItemFailures list."""
        result = fpolicy_handler.lambda_handler(sqs_event, None)

        assert result == {"batchItemFailures": []}

    def test_sqs_batch_ships_all_events(
        self,
        sqs_event_batch: dict[str, Any],
        mock_secrets: MagicMock,
        mock_http_success: MagicMock,
    ) -> None:
        """A batch of SQS records ships all events with no reported failures."""
        result = fpolicy_handler.lambda_handler(sqs_event_batch, None)

        assert result["batchItemFailures"] == []

    def test_sqs_event_sends_to_hec(
        self,
        sqs_event: dict[str, Any],
        mock_secrets: MagicMock,
        mock_http_success: MagicMock,
    ) -> None:
        """SQS event triggers a POST request to Splunk HEC endpoint."""
        fpolicy_handler.lambda_handler(sqs_event, None)

        mock_http_success.request.assert_called_once()
        call_args = mock_http_success.request.call_args
        assert call_args[0][0] == "POST"
        assert "/services/collector/event" in call_args[0][1]

    def test_hec_payload_has_fpolicy_sourcetype(
        self,
        sqs_event: dict[str, Any],
        mock_secrets: MagicMock,
        mock_http_success: MagicMock,
    ) -> None:
        """HEC payload uses sourcetype fsxn:ontap:fpolicy and index fsxn_fpolicy."""
        fpolicy_handler.lambda_handler(sqs_event, None)

        call_args = mock_http_success.request.call_args
        sent_body = call_args[1]["body"].decode("utf-8")
        sent_event = json.loads(sent_body)
        assert sent_event["sourcetype"] == "fsxn:ontap:fpolicy"
        assert sent_event["index"] == "fsxn_fpolicy"
        assert sent_event["event"]["operation_type"] == "CREATE"
        assert sent_event["event"]["svm"] == "fsxsvm01"

    def test_malformed_sqs_body_reported_valid_ones_ship(
        self,
        fpolicy_event_detail: dict[str, Any],
        mock_secrets: MagicMock,
        mock_http_success: MagicMock,
    ) -> None:
        """A malformed body is reported for DLQ redrive; valid records still ship.

        The malformed message must not be silently deleted — reporting it lets
        the queue's redrive policy move it to the DLQ where it is visible.
        """
        event = {
            "Records": [
                {
                    "messageId": "bad-msg",
                    "eventSource": "aws:sqs",
                    "body": "not valid json {{",
                },
                {
                    "messageId": "good-msg",
                    "eventSource": "aws:sqs",
                    "body": json.dumps(fpolicy_event_detail),
                },
            ]
        }

        result = fpolicy_handler.lambda_handler(event, None)

        assert result["batchItemFailures"] == [{"itemIdentifier": "bad-msg"}]
        # The valid record was still delivered.
        mock_http_success.request.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: SQS partial batch failure contract (ReportBatchItemFailures)
# ---------------------------------------------------------------------------


class TestSqsPartialBatchFailure:
    """Tests that SQS failures are reported per message instead of swallowed.

    The event source mapping declares
    ``FunctionResponseTypes: [ReportBatchItemFailures]``, so returning an
    empty ``batchItemFailures`` list tells SQS to delete every message. Any
    message that was not successfully delivered must appear in the list or it
    is lost.
    """

    def test_hec_failure_reports_every_parsed_message(
        self,
        sqs_event_batch: dict[str, Any],
        mock_secrets: MagicMock,
        mock_http_failure: MagicMock,
    ) -> None:
        """When HEC delivery fails, all parsed messages are reported for retry."""
        result = fpolicy_handler.lambda_handler(sqs_event_batch, None)

        assert {f["itemIdentifier"] for f in result["batchItemFailures"]} == {
            "msg-1",
            "msg-2",
        }

    def test_partial_hec_shipment_reports_all_parsed_messages(
        self,
        sqs_event_batch: dict[str, Any],
        mock_secrets: MagicMock,
        mock_http_success: MagicMock,
    ) -> None:
        """A short shipped count is treated as failure, not as success.

        ``_ship_to_splunk`` returns the number shipped rather than raising, so
        the handler must compare it against the number of events. Otherwise a
        partially delivered batch would be reported as fully successful and the
        undelivered messages deleted.
        """
        with patch.object(fpolicy_handler, "_ship_to_splunk", return_value=0):
            result = fpolicy_handler.lambda_handler(sqs_event_batch, None)

        assert {f["itemIdentifier"] for f in result["batchItemFailures"]} == {
            "msg-1",
            "msg-2",
        }

    def test_secrets_failure_raises_so_sqs_redelivers(
        self,
        sqs_event: dict[str, Any],
    ) -> None:
        """A credential failure applies to the whole batch, so it must raise.

        Returning a response would delete the messages. Raising fails the
        invocation and SQS re-delivers every message in the batch.
        """
        from botocore.exceptions import ClientError

        error = ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "denied"}},
            "GetSecretValue",
        )
        with patch.object(fpolicy_handler, "_get_hec_token", side_effect=error):
            with pytest.raises(ClientError):
                fpolicy_handler.lambda_handler(sqs_event, None)

    def test_all_records_malformed_reports_all(
        self,
        mock_secrets: MagicMock,
    ) -> None:
        """A batch of only malformed messages reports every one of them."""
        event = {
            "Records": [
                {
                    "messageId": "bad-1",
                    "eventSource": "aws:sqs",
                    "body": "not valid json {{",
                },
                {
                    "messageId": "bad-2",
                    "eventSource": "aws:sqs",
                    "body": "[]",
                },
            ]
        }

        result = fpolicy_handler.lambda_handler(event, None)

        assert {f["itemIdentifier"] for f in result["batchItemFailures"]} == {
            "bad-1",
            "bad-2",
        }

    def test_non_sqs_records_use_eventbridge_path(
        self,
        eventbridge_event: dict[str, Any],
        mock_secrets: MagicMock,
        mock_http_success: MagicMock,
    ) -> None:
        """EventBridge events keep the statusCode contract, not batchItemFailures."""
        result = fpolicy_handler.lambda_handler(eventbridge_event, None)

        assert "batchItemFailures" not in result
        assert result["statusCode"] == 200


# ---------------------------------------------------------------------------
# Tests: EventBridge single event -> shipped successfully
# ---------------------------------------------------------------------------


class TestEventBridgeEventShipping:
    """Tests for the secondary EventBridge -> Lambda -> Splunk HEC path."""

    def test_eventbridge_event_returns_200(
        self,
        eventbridge_event: dict[str, Any],
        mock_secrets: MagicMock,
        mock_http_success: MagicMock,
    ) -> None:
        """A single EventBridge event ships successfully and returns HTTP 200."""
        result = fpolicy_handler.lambda_handler(eventbridge_event, None)

        assert result["statusCode"] == 200
        assert result["body"]["total_events"] == 1
        assert result["body"]["shipped"] == 1

    def test_eventbridge_event_sends_to_hec(
        self,
        eventbridge_event: dict[str, Any],
        mock_secrets: MagicMock,
        mock_http_success: MagicMock,
    ) -> None:
        """EventBridge event triggers a POST request to Splunk HEC endpoint."""
        fpolicy_handler.lambda_handler(eventbridge_event, None)

        mock_http_success.request.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: Missing/invalid event detail -> 400
# ---------------------------------------------------------------------------


class TestInvalidEvent:
    """Tests for invalid or unrecognized event payloads."""

    def test_missing_detail_returns_400(
        self,
        mock_secrets: MagicMock,
    ) -> None:
        """EventBridge-shaped event with no detail field returns HTTP 400."""
        event = {"source": "fpolicy.fsxn", "detail-type": "CREATE"}

        result = fpolicy_handler.lambda_handler(event, None)

        assert result["statusCode"] == 400
        assert "error" in result["body"]

    def test_detail_wrong_type_returns_400(
        self,
        mock_secrets: MagicMock,
    ) -> None:
        """EventBridge-shaped event with non-dict detail returns HTTP 400."""
        event = {"source": "fpolicy.fsxn", "detail": "not-a-dict"}

        result = fpolicy_handler.lambda_handler(event, None)

        assert result["statusCode"] == 400

    def test_sqs_all_records_malformed_reports_failures(
        self,
        mock_secrets: MagicMock,
    ) -> None:
        """An all-malformed SQS batch reports failures instead of returning 400.

        A 400 response would tell SQS the batch was handled and delete the
        messages. Reporting them routes them to the DLQ instead.
        """
        event = {
            "Records": [
                {
                    "messageId": "bad-msg",
                    "eventSource": "aws:sqs",
                    "body": "not valid json {{",
                }
            ]
        }

        result = fpolicy_handler.lambda_handler(event, None)

        assert result["batchItemFailures"] == [{"itemIdentifier": "bad-msg"}]

    def test_invalid_event_does_not_call_secrets_manager(
        self,
        mock_secrets: MagicMock,
    ) -> None:
        """Invalid event short-circuits before retrieving the HEC token."""
        event = {"source": "fpolicy.fsxn", "detail-type": "CREATE"}

        fpolicy_handler.lambda_handler(event, None)

        mock_secrets.get_secret_value.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: HEC failure after retries -> 207 (partial)
# ---------------------------------------------------------------------------


class TestHecFailureAfterRetries:
    """Tests for HEC failure after retry exhaustion."""

    @patch("fpolicy_handler.time.sleep")
    def test_hec_503_after_retries_reports_failure(
        self,
        mock_sleep: MagicMock,
        sqs_event: dict[str, Any],
        mock_secrets: MagicMock,
        mock_http_failure: MagicMock,
    ) -> None:
        """HEC returning 503 on all retries reports the message for SQS retry."""
        result = fpolicy_handler.lambda_handler(sqs_event, None)

        assert result["batchItemFailures"] == [{"itemIdentifier": "msg-1"}]

    @patch("fpolicy_handler.time.sleep")
    def test_hec_503_on_eventbridge_path_returns_207(
        self,
        mock_sleep: MagicMock,
        eventbridge_event: dict[str, Any],
        mock_secrets: MagicMock,
        mock_http_failure: MagicMock,
    ) -> None:
        """The EventBridge path still signals partial delivery with HTTP 207."""
        result = fpolicy_handler.lambda_handler(eventbridge_event, None)

        assert result["statusCode"] == 207
        assert result["body"]["shipped"] == 0
        assert result["body"]["total_events"] == 1

    @patch("fpolicy_handler.time.sleep")
    def test_hec_failure_retries_three_times(
        self,
        mock_sleep: MagicMock,
        sqs_event: dict[str, Any],
        mock_secrets: MagicMock,
        mock_http_failure: MagicMock,
    ) -> None:
        """HEC failure triggers exactly 3 retry attempts."""
        fpolicy_handler.lambda_handler(sqs_event, None)

        assert mock_http_failure.request.call_count == 3

    @patch("fpolicy_handler.time.sleep")
    def test_hec_failure_uses_exponential_backoff(
        self,
        mock_sleep: MagicMock,
        sqs_event: dict[str, Any],
        mock_secrets: MagicMock,
        mock_http_failure: MagicMock,
    ) -> None:
        """Retry uses exponential backoff (2s, 4s delays)."""
        fpolicy_handler.lambda_handler(sqs_event, None)

        sleep_calls = [call[0][0] for call in mock_sleep.call_args_list]
        assert len(sleep_calls) == 2
        assert sleep_calls[0] == 2  # BASE_DELAY_SECONDS * 2^0
        assert sleep_calls[1] == 4  # BASE_DELAY_SECONDS * 2^1


# ---------------------------------------------------------------------------
# Tests: HEC token retrieval failure -> 502
# ---------------------------------------------------------------------------


class TestHecTokenRetrievalFailure:
    """Tests for Secrets Manager failures when fetching the HEC token."""

    def test_secrets_manager_error_returns_502_on_eventbridge_path(
        self,
        eventbridge_event: dict[str, Any],
        mock_http_success: MagicMock,
    ) -> None:
        """Secrets Manager failure on the EventBridge path returns HTTP 502."""
        from botocore.exceptions import ClientError

        with patch("fpolicy_handler.secrets_client") as mock_client:
            mock_client.get_secret_value.side_effect = ClientError(
                error_response={
                    "Error": {
                        "Code": "ResourceNotFoundException",
                        "Message": "Secret not found",
                    }
                },
                operation_name="GetSecretValue",
            )

            result = fpolicy_handler.lambda_handler(eventbridge_event, None)

        assert result["statusCode"] == 502
        assert "error" in result["body"]

    def test_secrets_manager_error_does_not_call_hec(
        self,
        sqs_event: dict[str, Any],
        mock_http_success: MagicMock,
    ) -> None:
        """Secrets Manager failure prevents any call to the Splunk HEC endpoint."""
        from botocore.exceptions import ClientError

        with patch("fpolicy_handler.secrets_client") as mock_client:
            mock_client.get_secret_value.side_effect = ClientError(
                error_response={
                    "Error": {
                        "Code": "AccessDeniedException",
                        "Message": "Access denied",
                    }
                },
                operation_name="GetSecretValue",
            )

            # The SQS path lets the error propagate so SQS re-delivers the batch.
            with pytest.raises(ClientError):
                fpolicy_handler.lambda_handler(sqs_event, None)

        mock_http_success.request.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: Empty SQS batch (no records at all)
# ---------------------------------------------------------------------------


class TestEmptyBatch:
    """Tests for edge case of an SQS event with an empty Records list."""

    def test_empty_records_list_raises_no_valid_events_error(
        self,
        mock_secrets: MagicMock,
    ) -> None:
        """An SQS event with an empty Records list returns HTTP 400 (no events found)."""
        event: dict[str, Any] = {"Records": []}

        result = fpolicy_handler.lambda_handler(event, None)

        assert result["statusCode"] == 400
