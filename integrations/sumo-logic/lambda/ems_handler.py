"""FSx for ONTAP EMS event shipper for Sumo Logic.

ONTAP posts EMS (Event Management System) events to an API Gateway endpoint,
which invokes this function synchronously. The API Gateway extraction, parser
delegation and response shaping live in ``ems_event``; the retry policy, secret
caching and batching live in ``vendor_shipper``. What remains here is the
Sumo Logic payload format and endpoint.

Because API Gateway invokes this synchronously, a single unparseable event is
skipped rather than failing the request: raising would return 5xx to ONTAP and
discard the events that were valid.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from ems_event import (
    api_response,
    extract_ems_events,
    normalize_ems_events,
    request_id,
    severity_of,
)
from vendor_shipper import SecretCache, batch_by_size, build_pool, post_with_retry

# ─── Configuration ─────────────────────────────────────────────────────────

API_KEY_SECRET_ARN = os.environ.get("API_KEY_SECRET_ARN", "")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
SOURCE_NAME = os.environ.get("EMS_SOURCE_NAME", "fsxn-ems")
SOURCE_CATEGORY = os.environ.get("SOURCE_CATEGORY", "aws/fsxn/ems")
SOURCE_HOST = os.environ.get("SOURCE_HOST", "fsxn-ontap")

MAX_BATCH_BYTES = 1 * 1024 * 1024  # Sumo Logic HTTP Source accepts 1MB per request
MAX_BATCH_ITEMS = None

logger = logging.getLogger()
logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))

HTTP = build_pool()
CREDENTIAL = SecretCache(API_KEY_SECRET_ARN, json_keys=("url", "endpoint"))


def _format(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Format normalized EMS events for Sumo Logic.

    Args:
        events: Normalized EMS events from ``normalize_ems_events``.

    Returns:
        Vendor-shaped payload items.
    """
    out: list[dict[str, Any]] = []
    for event in events:
        out.append({
            "source": SOURCE_NAME,
            "timestamp": event.get("timestamp", ""),
            "severity": severity_of(event),
            "event_name": event.get("event_name", ""),
            "svm": event.get("svm", ""),
            "source_node": event.get("source_node", ""),
            "parameters": event.get("parameters", {}),
            "message": event.get("message") or json.dumps(event, default=str),
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
    """Ship all items, returning how many were accepted.

    Args:
        items: Vendor-shaped payload items.
        cred: Credential from :class:`SecretCache`.

    Returns:
        Count of accepted items. A short count means the caller must not report
        full success.
    """
    shipped = 0
    for batch in batch_by_size(items, MAX_BATCH_BYTES, MAX_BATCH_ITEMS):
        if _send(batch, cred):
            shipped += len(batch)
        else:
            logger.error("Failed to ship batch of %d EMS event(s)", len(batch))
    return shipped


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Handle an EMS webhook event from API Gateway.

    Args:
        event: API Gateway proxy event with the EMS payload in ``body``.
        context: Lambda context object.

    Returns:
        API Gateway proxy response. 200 all shipped, 207 partial, 400 invalid
        payload, 502 credential retrieval failed.
    """
    logger.info("EMS handler invoked: requestId=%s", request_id(event))

    try:
        raw_events = extract_ems_events(event)
    except (json.JSONDecodeError, ValueError) as e:
        logger.error("Failed to parse EMS payload: %s", str(e))
        return api_response(400, {"error": f"Invalid EMS payload: {e}"})

    if not raw_events:
        logger.warning("No EMS events found in payload")
        return api_response(200, {"message": "No events to process", "shipped": 0})

    normalized = normalize_ems_events(raw_events, logger)
    if not normalized:
        # Every event failed to parse. Report it rather than claiming success,
        # so a schema change surfaces instead of looking like an idle pipeline.
        logger.error("All %d EMS event(s) failed to parse", len(raw_events))
        return api_response(422, {
            "error": "No EMS event could be parsed",
            "received": len(raw_events),
        })

    try:
        cred = CREDENTIAL.get()
    except Exception as e:  # noqa: BLE001 - botocore ClientError and friends
        logger.error("Failed to retrieve the HTTP Source URL: %s", str(e))
        return api_response(502, {"error": "Could not retrieve credentials"})

    items = _format(normalized)
    shipped = _ship(items, cred)

    body = {
        "message": "EMS events processed",
        "total_events": len(raw_events),
        "shipped": shipped,
    }
    logger.info("Processing complete: %s", json.dumps(body))
    return api_response(200 if shipped == len(items) else 207, body)
