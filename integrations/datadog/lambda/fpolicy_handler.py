"""FSx for ONTAP FPolicy event shipper for Datadog.

Receives FPolicy file operation events from EventBridge custom bus
(source: fpolicy.fsxn) and ships to Datadog Logs Intake API v2.

Supports all Datadog sites (US1, US3, US5, EU1, AP1, US1-FED, AP2).
See: https://docs.datadoghq.com/getting_started/site/
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import time
from typing import Any

import boto3
import urllib3

# ─── Configuration from environment ────────────────────────────────────────
# All configuration is driven by environment variables for multi-region support.
# No hardcoded values — each deployment can target any Datadog site.

DATADOG_SITE = os.environ.get("DATADOG_SITE", "datadoghq.com")
API_KEY_SECRET_ARN = os.environ.get("API_KEY_SECRET_ARN", "")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
DD_ENV = os.environ.get("DD_ENV", "production")
ENABLE_GZIP = os.environ.get("ENABLE_GZIP", "false").lower() == "true"

# ─── Constants ──────────────────────────────────────────────────────────────

# Overridable so the log pipeline / facet filters can be retargeted without a
# code change. The defaults match what template-ems-fpolicy.yaml sets.
DD_SOURCE = os.environ.get("DD_SOURCE", "fsxn-fpolicy")
DD_SERVICE = os.environ.get("DD_SERVICE", "fsxn-ontap")
MAX_BATCH_SIZE_BYTES = 5 * 1024 * 1024  # 5MB per request (Datadog limit)
MAX_BATCH_ITEMS = 1000  # Max items per batch (Datadog limit)
MAX_RETRIES = 3

# Datadog Logs Intake URL — constructed from DATADOG_SITE env var.
INTAKE_URL = f"https://http-intake.logs.{DATADOG_SITE}/api/v2/logs"

# ─── Logger setup ──────────────────────────────────────────────────────────

logger = logging.getLogger()
logger.setLevel(getattr(logging, LOG_LEVEL))

# ─── AWS clients (initialized outside handler for connection reuse) ─────────

secrets_client = boto3.client("secretsmanager")

# HTTP client with connection pooling
http = urllib3.PoolManager(
    num_pools=4,
    maxsize=10,
    retries=urllib3.Retry(total=0),  # We handle retries ourselves
)

# Cache for API key (Lambda execution context reuse)
_api_key_cache: str | None = None


def get_api_key() -> str:
    """Retrieve Datadog API key from Secrets Manager with caching.

    Supports both plain string and JSON format secrets:
    - Plain string: "your-api-key"
    - JSON: {"api_key": "your-api-key"} or {"DD_API_KEY": "your-api-key"}

    Returns:
        Datadog API key string.
    """
    global _api_key_cache
    if _api_key_cache is None:
        response = secrets_client.get_secret_value(SecretId=API_KEY_SECRET_ARN)
        secret = response["SecretString"]
        # Support both plain string and JSON format
        try:
            parsed = json.loads(secret)
            _api_key_cache = parsed.get("api_key", parsed.get("DD_API_KEY", secret))
        except (json.JSONDecodeError, AttributeError):
            _api_key_cache = secret
    return _api_key_cache


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Handle FPolicy events from SQS (primary) or EventBridge (secondary).

    Two trigger paths exist:

    1. **SQS event source mapping** (primary): ONTAP → Fargate → SQS → Lambda.
       Returns a ``batchItemFailures`` response so only the failing messages are
       retried and eventually redriven to the DLQ. This requires
       ``FunctionResponseTypes: [ReportBatchItemFailures]`` on the event source
       mapping — without it, a partial failure re-delivers the whole batch.
    2. **EventBridge rule** (secondary): a single event with source
       ``fpolicy.fsxn`` carrying the FPolicy data in ``detail``.

    Args:
        event: SQS batch event or EventBridge event.
        context: Lambda context object.

    Returns:
        For SQS: ``{"batchItemFailures": [...]}``.
        For EventBridge: dict with statusCode and processing summary.

    Raises:
        Exception: On transient failures (Secrets Manager, Datadog delivery) so
            the invocation fails and the events are retried rather than dropped.
    """
    if _is_sqs_event(event):
        return _handle_sqs_event(event)

    return _handle_eventbridge_event(event)


def _is_sqs_event(event: dict[str, Any]) -> bool:
    """Return True if the payload is an SQS event source mapping batch."""
    records = event.get("Records")
    if not isinstance(records, list) or not records:
        return False
    return any(r.get("eventSource") == "aws:sqs" for r in records)


def _handle_sqs_event(event: dict[str, Any]) -> dict[str, Any]:
    """Process an SQS batch with per-message failure reporting.

    Messages that cannot be parsed are reported as individual failures, so a
    single malformed message is redriven to the DLQ after ``maxReceiveCount``
    attempts instead of blocking (or silently discarding) the whole batch.

    Delivery to Datadog is still done as one batched shipment for efficiency.
    If that shipment fails, every message in the batch is reported as failed so
    SQS re-delivers all of them — at-least-once, never silently dropped.
    """
    records = event["Records"]
    logger.info("FPolicy handler invoked: SQS batch of %d record(s)", len(records))

    # A Secrets Manager failure is transient and applies to the whole batch:
    # raise so SQS re-delivers everything instead of deleting the messages.
    api_key = get_api_key()

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
                message_id, str(e),
            )
            batch_item_failures.append({"itemIdentifier": message_id})
            continue

        fpolicy_events.append(parsed)
        shippable_message_ids.append(message_id)

    if fpolicy_events:
        dd_logs = _format_for_datadog(fpolicy_events)
        try:
            shipped = _ship_to_datadog(dd_logs, api_key)
            logger.info(
                "Shipped %d/%d FPolicy event(s) from %d SQS message(s)",
                shipped, len(dd_logs), len(shippable_message_ids),
            )
        except Exception as e:
            # Delivery failure is not attributable to a single message, so retry
            # all messages that were successfully parsed.
            logger.error(
                "Datadog delivery failed for %d message(s): %s — reporting all for retry",
                len(shippable_message_ids), str(e),
            )
            batch_item_failures.extend(
                {"itemIdentifier": mid} for mid in shippable_message_ids
            )

    if batch_item_failures:
        logger.warning(
            "Reporting %d/%d SQS message(s) as failed", len(batch_item_failures), len(records)
        )

    # Response shape required by ReportBatchItemFailures. An empty list means
    # the whole batch succeeded and every message is deleted.
    return {"batchItemFailures": batch_item_failures}


def _handle_eventbridge_event(event: dict[str, Any]) -> dict[str, Any]:
    """Process a single FPolicy event delivered by an EventBridge rule."""
    logger.info("FPolicy handler invoked: source=%s", event.get("source", "unknown"))

    try:
        fpolicy_events = _extract_fpolicy_events(event)
    except (KeyError, ValueError) as e:
        # Malformed EventBridge payloads are not retryable. EventBridge has no
        # per-item failure protocol, so report the rejection and let the rule's
        # own DLQ (if configured) capture it.
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

    # Let Secrets Manager and Datadog delivery failures propagate: a failed
    # invocation is retried by EventBridge, a swallowed error is data loss.
    api_key = get_api_key()

    dd_logs = _format_for_datadog(fpolicy_events)
    shipped = _ship_to_datadog(dd_logs, api_key)

    result = {
        "statusCode": 200 if shipped == len(dd_logs) else 207,
        "body": {
            "message": "FPolicy events processed",
            "total_events": len(fpolicy_events),
            "shipped": shipped,
        },
    }
    logger.info("Processing complete: %s", json.dumps(result))
    return result


def _extract_fpolicy_events(event: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract FPolicy file operation event(s) from EventBridge or SQS event.

    Supports two invocation patterns:
    1. EventBridge: single event with FPolicy data in ``detail`` field.
    2. SQS: batch of records with FPolicy JSON in ``body`` field.

    Args:
        event: EventBridge event dict or SQS batch event dict.

    Returns:
        List of FPolicy event dictionaries.

    Raises:
        ValueError: If the event format is unrecognized or invalid.
    """
    # SQS event source mapping format
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
                        logger.warning("SQS message body is not a dict: %s", type(parsed).__name__)
                except json.JSONDecodeError as e:
                    logger.error("Failed to parse SQS message body: %s", str(e))
        if events:
            return events
        raise ValueError("No valid FPolicy events found in SQS records")

    # EventBridge format
    detail = event.get("detail")

    if detail is None:
        raise ValueError("Event detail is missing")

    if not isinstance(detail, dict):
        raise ValueError(f"Unexpected detail type: {type(detail).__name__}")

    return [detail]


def _format_for_datadog(
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Format FPolicy events for Datadog Logs Intake API v2.

    Each log entry includes:
    - ddsource: "fsxn-fpolicy"
    - ddtags: source:fsxn-fpolicy, service:fsxn-ontap, env:<DD_ENV>
    - hostname: vserver from FPolicy event
    - service: "fsxn-ontap"
    - message: human-readable summary of the file operation
    - date: event timestamp
    - attributes: structured fields (operation, file_path, user, client_ip)

    Args:
        events: List of FPolicy event dictionaries.

    Returns:
        List of Datadog-formatted log entries.
    """
    dd_logs: list[dict[str, Any]] = []

    for event in events:
        # Support both field names: "operation" (EventBridge) and "operation_type" (SQS/FPolicy server)
        operation = event.get("operation_type", event.get("operation", "unknown"))
        file_path = event.get("file_path", "")
        user = event.get("user", "")
        client_ip = event.get("client_ip", "")
        vserver = event.get("vserver", event.get("svm_name", "fsxn-ontap"))
        protocol = event.get("protocol", "")

        # Build human-readable message
        message = (
            f"FPolicy: {operation} {file_path} by {user} from {client_ip}"
            if file_path and user
            else json.dumps(event, default=str)
        )

        dd_log: dict[str, Any] = {
            "ddsource": DD_SOURCE,
            "ddtags": (
                f"source:{DD_SOURCE},"
                f"service:{DD_SERVICE},"
                f"env:{DD_ENV}"
            ),
            "hostname": vserver,
            "service": DD_SERVICE,
            "message": message,
        }

        # Set timestamp if available
        timestamp = event.get("timestamp", "")
        if timestamp:
            dd_log["date"] = timestamp

        # Structured attributes for Facet-based searching
        dd_log["attributes"] = {
            "operation_type": operation,
            "file_path": file_path,
            "user": user,
            "client_ip": client_ip,
            "svm": vserver,
            "protocol": protocol,
        }

        dd_logs.append(dd_log)

    return dd_logs


def _ship_to_datadog(logs: list[dict[str, Any]], api_key: str) -> int:
    """Ship logs to Datadog Logs Intake API v2 in batches.

    Respects Datadog batch limits (5MB / 1000 items per request).
    Raises RuntimeError if any batch fails after retries, preventing
    the caller from treating the invocation as successful.

    Args:
        logs: Datadog-formatted log entries.
        api_key: Datadog API key.

    Returns:
        Number of successfully shipped logs.

    Raises:
        RuntimeError: If one or more batches fail after all retries.
    """
    if not logs:
        return 0

    shipped = 0
    failed_batches = 0
    batches = _create_batches(logs)

    for batch in batches:
        success = _send_batch(batch, api_key)
        if success:
            shipped += len(batch)
        else:
            failed_batches += 1
            logger.error("Failed to ship batch of %d logs", len(batch))

    if failed_batches:
        raise RuntimeError(
            f"{failed_batches} Datadog batch(es) failed after retries. "
            f"Shipped {shipped}/{len(logs)} logs."
        )

    return shipped


def _create_batches(logs: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Split logs into batches respecting Datadog size limits.

    Each batch must be under 5MB uncompressed and 1000 items.

    Args:
        logs: List of Datadog-formatted log entries.

    Returns:
        List of batches, each batch being a list of log entries.
    """
    batches: list[list[dict[str, Any]]] = []
    current_batch: list[dict[str, Any]] = []
    current_size = 0

    for log in logs:
        log_size = len(json.dumps(log).encode("utf-8"))

        if (
            current_size + log_size > MAX_BATCH_SIZE_BYTES
            or len(current_batch) >= MAX_BATCH_ITEMS
        ):
            if current_batch:
                batches.append(current_batch)
            current_batch = [log]
            current_size = log_size
        else:
            current_batch.append(log)
            current_size += log_size

    if current_batch:
        batches.append(current_batch)

    return batches


def _send_batch(batch: list[dict[str, Any]], api_key: str) -> bool:
    """Send a batch of logs to Datadog with exponential backoff retry.

    Supports optional gzip compression (controlled by ENABLE_GZIP env var).

    Args:
        batch: List of Datadog-formatted log entries.
        api_key: Datadog API key.

    Returns:
        True if successfully sent, False otherwise.
    """
    json_payload = json.dumps(batch).encode("utf-8")

    if ENABLE_GZIP:
        payload = gzip.compress(json_payload)
        headers = {
            "Content-Type": "application/json",
            "Content-Encoding": "gzip",
            "DD-API-KEY": api_key,
        }
    else:
        payload = json_payload
        headers = {
            "Content-Type": "application/json",
            "DD-API-KEY": api_key,
        }

    for attempt in range(MAX_RETRIES):
        try:
            response = http.request(
                "POST",
                INTAKE_URL,
                body=payload,
                headers=headers,
                timeout=30.0,
            )

            if response.status < 300:
                logger.debug(
                    "Successfully shipped %d logs (attempt %d)",
                    len(batch),
                    attempt + 1,
                )
                return True

            if response.status == 429:
                # Rate limited — respect Retry-After header
                retry_after = int(
                    response.headers.get("Retry-After", 2 ** (attempt + 1))
                )
                logger.warning(
                    "Rate limited by Datadog, retrying in %ds", retry_after
                )
                time.sleep(retry_after)
                continue

            if response.status >= 500:
                # Server error — retry with backoff
                wait_time = 2 ** (attempt + 1)
                logger.warning(
                    "Datadog server error %d, retrying in %ds",
                    response.status,
                    wait_time,
                )
                time.sleep(wait_time)
                continue

            # Client error (4xx except 429) — don't retry
            logger.error(
                "Datadog API error %d: %s",
                response.status,
                response.data.decode("utf-8", errors="replace")[:500],
            )
            return False

        except urllib3.exceptions.HTTPError as e:
            wait_time = 2 ** (attempt + 1)
            logger.warning(
                "HTTP error shipping to Datadog (attempt %d/%d): %s",
                attempt + 1,
                MAX_RETRIES,
                str(e),
            )
            if attempt < MAX_RETRIES - 1:
                time.sleep(wait_time)

    return False
