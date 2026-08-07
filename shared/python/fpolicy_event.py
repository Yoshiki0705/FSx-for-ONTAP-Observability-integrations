"""Shared FPolicy event plumbing for vendor shipper Lambdas.

FPolicy reaches a vendor Lambda by two routes:

1. **SQS event source mapping** (primary): ONTAP → Fargate → SQS → Lambda. The
   handler must return ``{"batchItemFailures": [...]}`` and the event source
   mapping must declare ``FunctionResponseTypes: [ReportBatchItemFailures]``.
   Without that pairing, SQS deletes every message in the batch as soon as the
   invocation returns without raising — losing up to ``BatchSize`` events per
   delivery failure.
2. **EventBridge rule** (secondary): one event with source ``fpolicy.fsxn`` and
   the FPolicy data in ``detail``. EventBridge has no per-item failure protocol,
   so this path keeps a ``statusCode`` response.

This module owns the batch bookkeeping and field normalization. Formatting and
shipping stay with the vendor.

Typical use in a vendor's ``fpolicy_handler.py``::

    from fpolicy_event import (
        batch_response, extract_eventbridge_detail, is_sqs_event, parse_sqs_batch,
    )

    def lambda_handler(event, context):
        if is_sqs_event(event):
            parsed, ids, failures = parse_sqs_batch(event["Records"], logger)
            ...  # ship `parsed`; on failure extend `failures` with `ids`
            return batch_response(failures)
        detail = extract_eventbridge_detail(event)
"""

from __future__ import annotations

import json
import logging
from typing import Any

__all__ = [
    "batch_response",
    "extract_eventbridge_detail",
    "is_sqs_event",
    "normalize_fpolicy_event",
    "parse_sqs_batch",
]


def is_sqs_event(event: dict[str, Any]) -> bool:
    """Return True when the payload is an SQS event source mapping batch.

    An empty ``Records`` list is deliberately *not* treated as an SQS batch:
    there is nothing to report per item, and routing it to the EventBridge path
    preserves the existing error response for a malformed payload.

    Args:
        event: The raw Lambda event payload.

    Returns:
        True when at least one record carries ``eventSource == "aws:sqs"``.
    """
    records = event.get("Records")
    if not isinstance(records, list) or not records:
        return False
    return any(r.get("eventSource") == "aws:sqs" for r in records)


def parse_sqs_batch(
    records: list[dict[str, Any]],
    logger: logging.Logger | None = None,
) -> tuple[list[dict[str, Any]], list[str], list[dict[str, str]]]:
    """Parse SQS record bodies into FPolicy events.

    A message whose body is not a JSON object is reported as an individual
    failure rather than dropped. Retrying a poison pill will never succeed, but
    reporting it lets the queue's redrive policy move it to the DLQ after
    ``maxReceiveCount`` attempts, where it is visible.

    Args:
        records: ``event["Records"]`` from an SQS event source mapping.
        logger: Optional logger for unparseable messages.

    Returns:
        Tuple of ``(events, shippable_message_ids, batch_item_failures)``.
        ``events[i]`` corresponds to ``shippable_message_ids[i]``.
    """
    log = logger or logging.getLogger(__name__)
    events: list[dict[str, Any]] = []
    shippable_ids: list[str] = []
    failures: list[dict[str, str]] = []

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
            log.error(
                "Unparseable SQS message %s: %s — reporting as failure for DLQ redrive",
                message_id,
                str(e),
            )
            failures.append({"itemIdentifier": message_id})
            continue

        events.append(parsed)
        shippable_ids.append(message_id)

    return events, shippable_ids, failures


def batch_response(failures: list[dict[str, str]]) -> dict[str, Any]:
    """Build the ReportBatchItemFailures response.

    Args:
        failures: ``[{"itemIdentifier": "<messageId>"}, ...]``.

    Returns:
        ``{"batchItemFailures": [...]}``. An empty list tells SQS the whole
        batch succeeded and every message may be deleted.
    """
    return {"batchItemFailures": failures}


def extract_eventbridge_detail(event: dict[str, Any]) -> dict[str, Any]:
    """Extract the FPolicy payload from an EventBridge event.

    Args:
        event: EventBridge event with source ``fpolicy.fsxn``.

    Returns:
        The ``detail`` object.

    Raises:
        ValueError: If ``detail`` is missing or is not an object.
    """
    detail = event.get("detail")
    if detail is None:
        raise ValueError("Event detail is missing")
    if not isinstance(detail, dict):
        raise ValueError(f"Unexpected detail type: {type(detail).__name__}")
    return detail


def normalize_fpolicy_event(event: dict[str, Any]) -> dict[str, Any]:
    """Normalize an FPolicy event to one stable set of field names.

    The Fargate FPolicy server and the EventBridge path disagree on two field
    names, so every vendor formatter would otherwise need the same pair of
    fallbacks:

    ==================  ==================  ==================
    Normalized          Fargate/SQS         EventBridge
    ==================  ==================  ==================
    ``operation_type``  ``operation_type``  ``operation``
    ``svm``             ``vserver``         ``svm_name``
    ==================  ==================  ==================

    Args:
        event: Raw FPolicy event from either path.

    Returns:
        Dict with keys ``timestamp``, ``operation_type``, ``file_path``,
        ``user``, ``client_ip``, ``svm``, ``protocol``, ``volume``, ``raw``.
        Missing values become empty strings so formatters can emit them
        unconditionally.
    """
    def pick(*names: str) -> str:
        for n in names:
            value = event.get(n)
            if value not in (None, ""):
                return str(value)
        return ""

    return {
        "timestamp": pick("timestamp", "time"),
        "operation_type": pick("operation_type", "operation") or "unknown",
        "file_path": pick("file_path", "path"),
        "user": pick("user", "username"),
        "client_ip": pick("client_ip", "clientIp"),
        "svm": pick("svm", "vserver", "svm_name") or "fsxn-ontap",
        "protocol": pick("protocol"),
        "volume": pick("volume_name", "volume"),
        "raw": event,
    }
