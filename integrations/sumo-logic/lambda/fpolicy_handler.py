"""FSx for ONTAP FPolicy event shipper for Sumo Logic.

Two trigger paths reach this function:

1. **SQS event source mapping** (primary): ONTAP → Fargate → SQS → Lambda.
   Returns ``batchItemFailures`` so only undelivered messages are retried and
   eventually redriven to the DLQ. This requires
   ``FunctionResponseTypes: [ReportBatchItemFailures]`` on the event source
   mapping — without it SQS deletes the whole batch as soon as the invocation
   returns, losing up to ``BatchSize`` events per delivery failure.
2. **EventBridge rule** (secondary): one event with source ``fpolicy.fsxn``.
   EventBridge has no per-item failure protocol, so that path keeps a
   ``statusCode`` response.

The batch bookkeeping and field normalization live in ``fpolicy_event``; the
retry policy, secret caching and batching live in ``vendor_shipper``. What
remains here is the Sumo Logic payload format and endpoint.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from fpolicy_event import (
    batch_response,
    extract_eventbridge_detail,
    is_sqs_event,
    normalize_fpolicy_event,
    parse_sqs_batch,
)
from vendor_shipper import SecretCache, batch_by_size, build_pool, post_with_retry

# ─── Configuration ─────────────────────────────────────────────────────────

API_KEY_SECRET_ARN = os.environ.get("API_KEY_SECRET_ARN", "")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
SOURCE_NAME = os.environ.get("FPOLICY_SOURCE_NAME", "fsxn-fpolicy")
SOURCE_CATEGORY = os.environ.get("SOURCE_CATEGORY", "aws/fsxn/fpolicy")
SOURCE_HOST = os.environ.get("SOURCE_HOST", "fsxn-ontap")

MAX_BATCH_BYTES = 1 * 1024 * 1024  # Sumo Logic HTTP Source accepts 1MB per request
MAX_BATCH_ITEMS = None

logger = logging.getLogger()
logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))

HTTP = build_pool()
CREDENTIAL = SecretCache(API_KEY_SECRET_ARN, json_keys=("url", "endpoint"))


def _format(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Format normalized FPolicy events for Sumo Logic.

    Args:
        events: Events from ``normalize_fpolicy_event``.

    Returns:
        Vendor-shaped payload items.
    """
    out: list[dict[str, Any]] = []
    for e in events:
        out.append({
            "source": SOURCE_NAME,
            "timestamp": e["timestamp"],
            "operation_type": e["operation_type"],
            "file_path": e["file_path"],
            "user": e["user"],
            "client_ip": e["client_ip"],
            "svm": e["svm"],
            "protocol": e["protocol"],
            "volume": e["volume"],
        })
    return out


def _send(batch: list[dict[str, Any]], cred: str) -> bool:
    """Deliver one batch. Returns False when it was not accepted."""
    # The HTTP Source URL is itself the credential, so it carries no auth header.
    body = "\n".join(json.dumps(x, default=str) for x in batch).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-Sumo-Category": SOURCE_CATEGORY,
        "X-Sumo-Name": SOURCE_NAME,
        "X-Sumo-Host": SOURCE_HOST,
    }
    return post_with_retry(HTTP, cred, body, headers, logger)


def _ship(items: list[dict[str, Any]], cred: str) -> int:
    """Ship all items, returning how many were accepted."""
    shipped = 0
    for batch in batch_by_size(items, MAX_BATCH_BYTES, MAX_BATCH_ITEMS):
        if _send(batch, cred):
            shipped += len(batch)
        else:
            logger.error("Failed to ship batch of %d FPolicy event(s)", len(batch))
    return shipped


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Handle FPolicy events from SQS (primary) or EventBridge (secondary).

    Args:
        event: SQS batch event or EventBridge event.
        context: Lambda context object.

    Returns:
        For SQS: ``{"batchItemFailures": [...]}``.
        For EventBridge: dict with statusCode and a processing summary.
    """
    if is_sqs_event(event):
        return _handle_sqs(event)
    return _handle_eventbridge(event)


def _handle_sqs(event: dict[str, Any]) -> dict[str, Any]:
    """Process an SQS batch with per-message failure reporting.

    Raises:
        Exception: If the credential cannot be read. That failure applies to the
            whole batch, so failing the invocation makes SQS re-deliver every
            message instead of deleting them.
    """
    records = event["Records"]
    logger.info("FPolicy handler invoked: SQS batch of %d record(s)", len(records))

    # Read the credential before parsing so a credential outage fails the whole
    # batch loudly rather than per message.
    cred = CREDENTIAL.get()

    parsed, shippable_ids, failures = parse_sqs_batch(records, logger)

    if parsed:
        items = _format([normalize_fpolicy_event(p) for p in parsed])
        shipped = _ship(items, cred)
        if shipped != len(items):
            # Delivery failure is not attributable to one message, so every
            # parsed message is reported and re-delivered.
            logger.error(
                "Shipped %d/%d item(s) — reporting all %d parsed message(s) for retry",
                shipped, len(items), len(shippable_ids),
            )
            failures.extend({"itemIdentifier": mid} for mid in shippable_ids)

    if failures:
        logger.warning(
            "Reporting %d/%d SQS message(s) as failed", len(failures), len(records)
        )
    return batch_response(failures)


def _handle_eventbridge(event: dict[str, Any]) -> dict[str, Any]:
    """Process a single FPolicy event delivered by an EventBridge rule."""
    logger.info("FPolicy handler invoked: source=%s", event.get("source", "unknown"))

    try:
        detail = extract_eventbridge_detail(event)
    except ValueError as e:
        logger.error("Failed to extract FPolicy event: %s", str(e))
        return {"statusCode": 400, "body": {"error": f"Invalid FPolicy event: {e}"}}

    cred = CREDENTIAL.get()
    items = _format([normalize_fpolicy_event(detail)])
    shipped = _ship(items, cred)

    return {
        "statusCode": 200 if shipped == len(items) else 207,
        "body": {
            "message": "FPolicy events processed",
            "total_events": 1,
            "shipped": shipped,
        },
    }
