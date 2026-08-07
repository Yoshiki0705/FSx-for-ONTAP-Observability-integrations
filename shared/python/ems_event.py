"""Shared EMS webhook plumbing for vendor shipper Lambdas.

ONTAP delivers EMS events to an API Gateway endpoint, which invokes a vendor
Lambda synchronously. Everything between "API Gateway handed us an event" and
"we have a list of normalized EMS dicts" is identical for every vendor, so it
lives here rather than being copied per vendor.

What is vendor-specific and therefore *not* here: formatting the normalized dict
into the vendor's log shape, and shipping it to the vendor's endpoint.

Normalized event fields (produced by the ems_parser layer):
    timestamp, event_name, severity, source_node, svm, message, parameters, raw

Typical use in a vendor's ``ems_handler.py``::

    from ems_event import (
        api_response, extract_ems_events, normalize_ems_events, request_id,
    )

    def lambda_handler(event, context):
        try:
            raw = extract_ems_events(event)
        except (ValueError, json.JSONDecodeError) as e:
            return api_response(400, {"error": f"Invalid EMS payload: {e}"})
        normalized = normalize_ems_events(raw, logger)
        ...
"""

from __future__ import annotations

import json
import logging
from typing import Any

__all__ = [
    "api_response",
    "extract_ems_events",
    "normalize_ems_events",
    "request_id",
    "severity_of",
]

# The ems_parser module ships as a Lambda Layer. Falling back to a passthrough
# keeps the handler importable for local development and unit tests without the
# layer, at the cost of unparsed fields — the same defensive pattern the audit
# handlers use for ontap_audit_parser.
try:  # pragma: no cover - exercised implicitly by both branches in tests
    from ems_parser import parse_ems_event as _parse_ems_event
except ImportError:  # pragma: no cover
    _parse_ems_event = None


def request_id(event: dict[str, Any]) -> str:
    """Return the API Gateway request ID, or ``"unknown"``.

    Args:
        event: API Gateway proxy event.

    Returns:
        The request ID for correlating logs with API Gateway access logs.
    """
    return event.get("requestContext", {}).get("requestId", "unknown")


def extract_ems_events(event: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract raw EMS event dicts from an API Gateway proxy event.

    ONTAP posts either a single EMS object or a JSON array of them. API Gateway
    delivers ``body`` as a string; direct invocation and tests may pass a dict
    or list already parsed.

    Args:
        event: API Gateway proxy event.

    Returns:
        List of raw EMS event dictionaries.

    Raises:
        ValueError: If the body is missing, empty, or not an object/array.
        json.JSONDecodeError: If the body is a string that is not valid JSON.
    """
    body = event.get("body")

    if body is None:
        raise ValueError("Event body is missing")

    if isinstance(body, str):
        if not body.strip():
            raise ValueError("Event body is empty")
        parsed = json.loads(body)
    else:
        parsed = body

    if isinstance(parsed, list):
        # Reject non-dict entries rather than passing them to the parser, which
        # would raise per item and lose the rest of the batch.
        return [item for item in parsed if isinstance(item, dict)]
    if isinstance(parsed, dict):
        return [parsed]
    raise ValueError(f"Unexpected body type: {type(parsed).__name__}")


def normalize_ems_events(
    raw_events: list[dict[str, Any]],
    logger: logging.Logger | None = None,
) -> list[dict[str, Any]]:
    """Normalize raw EMS events through the shared parser layer.

    An event that cannot be parsed is logged and skipped rather than failing the
    whole batch: EMS arrives over a synchronous API Gateway call, so raising
    would return 5xx to ONTAP and discard the events that *were* valid.

    Args:
        raw_events: Raw EMS event dictionaries.
        logger: Optional logger for parse failures.

    Returns:
        List of normalized event dictionaries.
    """
    log = logger or logging.getLogger(__name__)

    if _parse_ems_event is None:
        log.warning(
            "ems_parser layer is not available — shipping raw EMS events "
            "without field normalization. Attach the EMS Parser Lambda Layer "
            "(EmsParserLayerArn) to get parsed fields."
        )
        return list(raw_events)

    normalized: list[dict[str, Any]] = []
    for raw in raw_events:
        try:
            normalized.append(_parse_ems_event(raw))
        except Exception as e:  # noqa: BLE001 - parser raises EmsParseError
            log.warning(
                "Skipping unparseable EMS event: %s (event: %s)",
                str(e),
                json.dumps(raw, default=str)[:200],
            )
    return normalized


def severity_of(event: dict[str, Any], default: str = "info") -> str:
    """Return a lowercase severity for a normalized EMS event.

    ONTAP severities are EMERGENCY, ALERT, ERROR, NOTICE, INFORMATIONAL and
    DEBUG. Vendors expect lowercase level names, and several of them reject an
    unknown value, so an empty severity falls back to ``default``.

    Args:
        event: Normalized EMS event.
        default: Value to use when the event carries no severity.

    Returns:
        Lowercase severity string.
    """
    sev = str(event.get("severity", "")).strip().lower()
    return sev or default


def api_response(status_code: int, body: dict[str, Any]) -> dict[str, Any]:
    """Build an API Gateway proxy response.

    Args:
        status_code: HTTP status to return to ONTAP.
        body: Response body, JSON-serialized.

    Returns:
        API Gateway proxy response dict.
    """
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=str),
    }
