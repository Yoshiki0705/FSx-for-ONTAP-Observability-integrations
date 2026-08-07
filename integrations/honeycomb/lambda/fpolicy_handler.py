"""FSx for ONTAP FPolicy event shipper for Honeycomb.

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
remains here is the Honeycomb payload format and endpoint.
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
HONEYCOMB_API_URL = os.environ.get("HONEYCOMB_API_URL", "https://api.honeycomb.io")
HONEYCOMB_DATASET = os.environ.get("HONEYCOMB_DATASET", "fsxn-fpolicy")

MAX_BATCH_BYTES = 5 * 1024 * 1024  # Honeycomb accepts 5MB per batch
MAX_BATCH_ITEMS = 100  # Honeycomb batch API item ceiling

logger = logging.getLogger()
logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))

HTTP = build_pool()
CREDENTIAL = SecretCache(API_KEY_SECRET_ARN, json_keys=("api_key", "ingest_key", "key"))


def _format(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Format normalized FPolicy events for Honeycomb.

    Args:
        events: Events from ``normalize_fpolicy_event``.

    Returns:
        Vendor-shaped payload items.
    """
    out: list[dict[str, Any]] = []
    for e in events:
        entry: dict[str, Any] = {
            "data": {
                "source": SOURCE_NAME,
                "service": "ontap-fpolicy",
                "operation_type": e["operation_type"],
                "file_path": e["file_path"],
                "user": e["user"],
                "client_ip": e["client_ip"],
                "svm": e["svm"],
                "protocol": e["protocol"],
                "volume": e["volume"],
            }
        }
        if e["timestamp"]:
            entry["time"] = e["timestamp"]
        out.append(entry)
    return out


def _send(batch: list[dict[str, Any]], cred: str) -> bool:
    """Deliver one batch. Returns False when it was not accepted."""
    body = json.dumps(batch, default=str).encode("utf-8")
    headers = {"Content-Type": "application/json", "X-Honeycomb-Team": cred}
    url = f"{HONEYCOMB_API_URL.rstrip('/')}/1/batch/{HONEYCOMB_DATASET}"
    return post_with_retry(HTTP, url, body, headers, logger)


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
