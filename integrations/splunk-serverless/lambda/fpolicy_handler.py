"""FSx for ONTAP FPolicy event shipper for Splunk via HTTP Event Collector (HEC).

Receives FPolicy file operation events from either:
- EventBridge custom bus (source: fpolicy.fsxn), one event per invocation, or
- SQS event source mapping (primary path: Fargate FPolicy server -> SQS -> Lambda),
  a batch of records per invocation.

Formats each event as Splunk HEC JSON (sourcetype fsxn:ontap:fpolicy, index
fsxn_fpolicy) and ships to the configured Splunk HEC endpoint with retry.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import boto3
import urllib3
from botocore.exceptions import ClientError

from models import SplunkHecEvent

# Configuration from environment variables
HEC_ENDPOINT = os.environ.get("SPLUNK_HEC_ENDPOINT", "")
HEC_TOKEN_SECRET_ARN = os.environ.get("HEC_TOKEN_SECRET_ARN", "")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

# FPolicy-specific Splunk configuration
FPOLICY_SOURCETYPE = "fsxn:ontap:fpolicy"
FPOLICY_INDEX = "fsxn_fpolicy"
FPOLICY_SOURCE = "fsxn-fpolicy"

# Retry configuration (matches ems_handler.py / handler.py convention)
MAX_RETRIES = 3
BASE_DELAY_SECONDS = 2

# HEC batching (matches handler.py convention; FPolicy events are small so
# a single SQS batch invocation rarely approaches this limit)
MAX_BATCH_EVENTS = 500

logger = logging.getLogger()
logger.setLevel(getattr(logging, LOG_LEVEL))

# AWS clients (initialized once per execution context for connection reuse)
secrets_client = boto3.client("secretsmanager")
http = urllib3.PoolManager(num_pools=2, maxsize=4)

# Cached HEC token (per execution context, not per invocation)
_hec_token_cache: str | None = None


def _get_hec_token() -> str:
    """Retrieve Splunk HEC token from Secrets Manager (cached per execution context).

    Returns:
        The HEC token string stored in Secrets Manager.

    Raises:
        ClientError: If secret retrieval fails.
    """
    global _hec_token_cache
    if _hec_token_cache is not None:
        return _hec_token_cache

    response = secrets_client.get_secret_value(SecretId=HEC_TOKEN_SECRET_ARN)
    secret = response["SecretString"]
    try:
        parsed = json.loads(secret)
        _hec_token_cache = parsed.get("hec_token", parsed.get("SPLUNK_HEC_TOKEN", secret))
    except (json.JSONDecodeError, AttributeError):
        _hec_token_cache = secret
    return _hec_token_cache


def _extract_fpolicy_events(event: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract FPolicy file operation event(s) from EventBridge or SQS event.

    Supports two invocation patterns:
    1. SQS event source mapping: batch of records with FPolicy JSON in
       each record's ``body`` field (primary path: Fargate -> SQS -> Lambda).
    2. EventBridge: single event with FPolicy data in the ``detail`` field
       (secondary/alternative path).

    Args:
        event: SQS batch event dict or EventBridge event dict.

    Returns:
        List of FPolicy event dictionaries.

    Raises:
        ValueError: If the event format is unrecognized or invalid.
    """
    if "Records" in event:
        events: list[dict[str, Any]] = []
        for record in event["Records"]:
            if record.get("eventSource") == "aws:sqs":
                body = record.get("body", "")
                try:
                    parsed = json.loads(body)
                    if isinstance(parsed, dict):
                        events.append(parsed)
                    else:
                        logger.warning(
                            "SQS message body is not a dict: %s", type(parsed).__name__
                        )
                except json.JSONDecodeError as e:
                    logger.error("Failed to parse SQS message body: %s", str(e))
        if events:
            return events
        raise ValueError("No valid FPolicy events found in SQS records")

    detail = event.get("detail")
    if detail is None:
        raise ValueError("Event detail is missing")
    if not isinstance(detail, dict):
        raise ValueError(f"Unexpected detail type: {type(detail).__name__}")

    return [detail]


def _format_for_splunk(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Format FPolicy events as Splunk HEC JSON.

    Each event is converted to a Splunk HEC event with sourcetype
    fsxn:ontap:fpolicy, index fsxn_fpolicy, and a structured event object
    containing the file operation details.

    Args:
        events: List of FPolicy event dictionaries. Supports both
            "operation" (EventBridge convention) and "operation_type"
            (SQS/FPolicy server convention) field names, matching the
            reference Datadog fpolicy_handler.py implementation.

    Returns:
        List of dictionaries suitable for the Splunk HEC
        /services/collector/event endpoint.
    """
    hec_events: list[dict[str, Any]] = []

    for fp_event in events:
        operation = fp_event.get("operation_type", fp_event.get("operation", "unknown"))
        file_path = fp_event.get("file_path", "")
        user = fp_event.get("user", "")
        client_ip = fp_event.get("client_ip", "")
        vserver = fp_event.get("vserver", fp_event.get("svm_name", "fsxn-ontap"))
        protocol = fp_event.get("protocol", "")
        timestamp = fp_event.get("timestamp", "")

        event_data: dict[str, Any] = {
            "operation_type": operation,
            "file_path": file_path,
            "user": user,
            "client_ip": client_ip,
            "svm": vserver,
            "protocol": protocol,
        }

        hec_event = SplunkHecEvent(
            time=_to_epoch(timestamp) if timestamp else None,
            host=vserver,
            source=FPOLICY_SOURCE,
            sourcetype=FPOLICY_SOURCETYPE,
            index=FPOLICY_INDEX,
            event=event_data,
        )
        hec_events.append(hec_event.to_hec_json())

    return hec_events


def _to_epoch(timestamp: str) -> float | None:
    """Convert ISO timestamp to epoch seconds.

    Args:
        timestamp: ISO 8601 timestamp string, optionally with a trailing
            "Z" suffix.

    Returns:
        Epoch seconds as a float, or None if the timestamp cannot be parsed.
    """
    from datetime import datetime

    try:
        if timestamp.endswith("Z"):
            timestamp = timestamp[:-1] + "+00:00"
        dt = datetime.fromisoformat(timestamp)
        return dt.timestamp()
    except (ValueError, AttributeError):
        return None


def _ship_to_splunk(events: list[dict[str, Any]], hec_token: str) -> int:
    """Ship HEC-formatted events to Splunk in batches.

    Args:
        events: Splunk HEC-formatted event dictionaries.
        hec_token: Splunk HEC authentication token.

    Returns:
        Number of successfully shipped events.
    """
    if not events:
        return 0

    shipped = 0
    for i in range(0, len(events), MAX_BATCH_EVENTS):
        batch = events[i : i + MAX_BATCH_EVENTS]
        if _send_batch(batch, hec_token):
            shipped += len(batch)
        else:
            logger.error("Failed to ship batch of %d FPolicy events", len(batch))

    return shipped


def _send_batch(batch: list[dict[str, Any]], hec_token: str) -> bool:
    """Send a batch of HEC events to Splunk with retry.

    Formats events as newline-delimited JSON (one JSON object per line,
    not a JSON array) per the HEC /services/collector/event contract.

    Args:
        batch: List of HEC event dicts to send.
        hec_token: Splunk HEC authentication token.

    Returns:
        True if the batch was successfully delivered, False otherwise.
    """
    payload = "\n".join(json.dumps(event) for event in batch)
    return _send_to_hec(payload, hec_token)


def _send_to_hec(payload: str, hec_token: str) -> bool:
    """Send payload to Splunk HEC endpoint with exponential backoff retry.

    Retries on HTTP 429 (rate limited) and 5xx (server errors) with
    exponential backoff: base delay 2 seconds, doubling per attempt,
    up to 3 total attempts. Does not retry on 4xx client errors
    (except 429).

    Args:
        payload: Newline-delimited JSON string of HEC events.
        hec_token: Splunk HEC authentication token.

    Returns:
        True if the payload was successfully delivered, False otherwise.
    """
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Splunk {hec_token}",
    }
    body = payload.encode("utf-8")

    for attempt in range(MAX_RETRIES):
        try:
            response = http.request(
                "POST",
                f"{HEC_ENDPOINT}/services/collector/event",
                body=body,
                headers=headers,
                timeout=30.0,
            )

            if response.status == 200:
                logger.info("Successfully shipped FPolicy events to Splunk HEC")
                return True

            if response.status == 429 or response.status >= 500:
                if attempt < MAX_RETRIES - 1:
                    delay = BASE_DELAY_SECONDS * (2 ** attempt)
                    logger.warning(
                        "Splunk HEC returned %d, attempt %d/%d, retrying in %ds",
                        response.status,
                        attempt + 1,
                        MAX_RETRIES,
                        delay,
                    )
                    time.sleep(delay)
                    continue

                logger.error(
                    "Splunk HEC returned %d, all %d attempts exhausted",
                    response.status,
                    MAX_RETRIES,
                )
                return False

            # 4xx (except 429): do not retry
            logger.error(
                "Splunk HEC client error %d: %s",
                response.status,
                response.data.decode("utf-8", errors="replace")[:300],
            )
            return False

        except urllib3.exceptions.HTTPError as e:
            if attempt < MAX_RETRIES - 1:
                delay = BASE_DELAY_SECONDS * (2 ** attempt)
                logger.warning(
                    "HTTP error on attempt %d/%d: %s, retrying in %ds",
                    attempt + 1,
                    MAX_RETRIES,
                    str(e),
                    delay,
                )
                time.sleep(delay)
            else:
                logger.error(
                    "HTTP error on final attempt %d/%d: %s",
                    attempt + 1,
                    MAX_RETRIES,
                    str(e),
                )
                return False

    return False


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Handle FPolicy events from SQS (primary) or EventBridge (secondary).

    Two trigger paths exist:

    1. **SQS event source mapping** (primary): ONTAP → Fargate → SQS → Lambda.
       Returns a ``batchItemFailures`` response so only the failing messages
       are retried and eventually redriven to the DLQ. This requires
       ``FunctionResponseTypes: [ReportBatchItemFailures]`` on the event source
       mapping — without it, returning a success dict makes SQS delete every
       message in the batch even when delivery to Splunk failed.
    2. **EventBridge rule** (secondary): a single event with source
       ``fpolicy.fsxn`` carrying the FPolicy data in ``detail``.

    Args:
        event: SQS batch event dict (primary path) or EventBridge event
            dict (secondary path) containing FPolicy file operation data.
        context: Lambda execution context.

    Returns:
        For SQS: ``{"batchItemFailures": [...]}``.
        For EventBridge: dict with statusCode (200 all shipped, 207 partial
        failure, 400 invalid event, 502 HEC credential retrieval failure) and
        a body summarizing the processing result.
    """
    if _is_sqs_event(event):
        return _handle_sqs_event(event)

    return _handle_eventbridge_event(event)


def _is_sqs_event(event: dict[str, Any]) -> bool:
    """Return True if the payload is an SQS event source mapping batch.

    Args:
        event: The raw Lambda event payload.

    Returns:
        True when at least one record carries ``eventSource == "aws:sqs"``.
    """
    records = event.get("Records")
    if not isinstance(records, list) or not records:
        return False
    return any(r.get("eventSource") == "aws:sqs" for r in records)


def _handle_sqs_event(event: dict[str, Any]) -> dict[str, Any]:
    """Process an SQS batch with per-message failure reporting.

    Messages that cannot be parsed are reported as individual failures, so a
    single malformed message is redriven to the DLQ after ``maxReceiveCount``
    attempts instead of being silently discarded with the rest of the batch.

    Delivery to Splunk HEC is still done as one batched shipment for
    efficiency. If that shipment fails, every parsed message is reported as
    failed so SQS re-delivers all of them — at-least-once, never silently
    dropped.

    Args:
        event: SQS batch event dict.

    Returns:
        ``{"batchItemFailures": [{"itemIdentifier": "<messageId>"}, ...]}``.
        An empty list means the whole batch succeeded.

    Raises:
        ClientError: If the HEC token cannot be retrieved. This is transient
            and applies to the whole batch, so failing the invocation makes
            SQS re-deliver every message rather than deleting them.
    """
    records = event["Records"]
    logger.info("FPolicy handler invoked: SQS batch of %d record(s)", len(records))

    # A Secrets Manager failure is transient and applies to the whole batch:
    # let it propagate so SQS re-delivers everything instead of deleting it.
    hec_token = _get_hec_token()

    batch_item_failures: list[dict[str, str]] = []
    fpolicy_events: list[dict[str, Any]] = []
    shippable_message_ids: list[str] = []

    for record in records:
        message_id = record.get("messageId", "")
        try:
            parsed = json.loads(record.get("body", ""))
            if not isinstance(parsed, dict):
                raise ValueError(
                    f"SQS message body is not a JSON object "
                    f"(got {type(parsed).__name__})"
                )
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            # Poison pill: retrying will never succeed, but reporting it as a
            # failure is still correct — the queue's redrive policy moves it to
            # the DLQ, which is visible, instead of dropping it silently.
            logger.error(
                "Unparseable SQS message %s: %s — reporting as failure for DLQ redrive",
                message_id,
                str(e),
            )
            batch_item_failures.append({"itemIdentifier": message_id})
            continue

        fpolicy_events.append(parsed)
        shippable_message_ids.append(message_id)

    if fpolicy_events:
        hec_events = _format_for_splunk(fpolicy_events)
        try:
            shipped = _ship_to_splunk(hec_events, hec_token)
            if shipped != len(hec_events):
                raise RuntimeError(
                    f"Shipped {shipped}/{len(hec_events)} FPolicy event(s) to Splunk HEC"
                )
            logger.info(
                "Shipped %d/%d FPolicy event(s) from %d SQS message(s)",
                shipped,
                len(hec_events),
                len(shippable_message_ids),
            )
        except Exception as e:
            # Delivery failure is not attributable to a single message, so
            # retry all messages that were successfully parsed.
            logger.error(
                "Splunk HEC delivery failed for %d message(s): %s — reporting all for retry",
                len(shippable_message_ids),
                str(e),
            )
            batch_item_failures.extend(
                {"itemIdentifier": mid} for mid in shippable_message_ids
            )

    if batch_item_failures:
        logger.warning(
            "Reporting %d/%d SQS message(s) as failed",
            len(batch_item_failures),
            len(records),
        )

    # Response shape required by ReportBatchItemFailures. An empty list means
    # the whole batch succeeded and every message is deleted.
    return {"batchItemFailures": batch_item_failures}


def _handle_eventbridge_event(event: dict[str, Any]) -> dict[str, Any]:
    """Process a single FPolicy event delivered by an EventBridge rule.

    Args:
        event: EventBridge event dict with FPolicy data in ``detail``.

    Returns:
        Dict with statusCode (200 all shipped, 207 partial failure, 400
        invalid event, 502 HEC credential retrieval failure) and a body
        summarizing the processing result.
    """
    logger.info("FPolicy handler invoked")

    try:
        fpolicy_events = _extract_fpolicy_events(event)
    except ValueError as e:
        logger.error("Failed to extract FPolicy event: %s", str(e))
        return {
            "statusCode": 400,
            "body": {"error": f"Invalid FPolicy event: {e}"},
        }

    if not fpolicy_events:
        logger.warning("No FPolicy events found in payload")
        return {
            "statusCode": 200,
            "body": {"message": "No events to process", "shipped": 0},
        }

    logger.info("Extracted %d FPolicy event(s)", len(fpolicy_events))

    try:
        hec_token = _get_hec_token()
    except ClientError as e:
        logger.error(
            "Failed to retrieve HEC token from Secrets Manager (%s): %s",
            HEC_TOKEN_SECRET_ARN,
            e.response["Error"]["Code"],
        )
        return {
            "statusCode": 502,
            "body": {"error": "Internal error retrieving HEC credentials"},
        }

    hec_events = _format_for_splunk(fpolicy_events)
    shipped = _ship_to_splunk(hec_events, hec_token)

    result = {
        "statusCode": 200 if shipped == len(hec_events) else 207,
        "body": {
            "message": "FPolicy events processed",
            "total_events": len(fpolicy_events),
            "shipped": shipped,
        },
    }
    logger.info("Processing complete: %s", json.dumps(result))
    return result
