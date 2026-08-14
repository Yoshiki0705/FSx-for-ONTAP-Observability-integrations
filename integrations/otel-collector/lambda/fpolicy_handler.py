"""FPolicy → OTel Collector OTLP log forwarder.

Subscribes to EventBridge custom bus events (source: fpolicy.fsxn),
formats FPolicy file operation events into OTLP Log Data Model,
and forwards to the configured OTel Collector endpoint.

Architecture: ONTAP → TCP:9898 → ECS Fargate → SQS → EventBridge → This Lambda → OTel Collector

Auth supports "bearer", "basic", or "header" (custom header name/value,
via AUTH_HEADER_NAME) plus optional EXTRA_HEADERS_JSON for static
non-secret headers a vendor's OTLP endpoint requires. No vendor-specific
branching exists in this file — only generic, vendor-agnostic primitives.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import random
import time
from datetime import datetime, timezone
from typing import Any, Optional

import boto3
import urllib3

from otlp_auth import validate_auth_mode_header, validate_extra_headers_json
from otlp_protobuf import encode_logs_data

# ─── Configuration ─────────────────────────────────────────────────────────

OTLP_ENDPOINT = os.environ.get("OTLP_ENDPOINT", "http://localhost:4318")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
SERVICE_NAME = os.environ.get("SERVICE_NAME", "fsxn-fpolicy")
API_KEY_SECRET_ARN = os.environ.get("API_KEY_SECRET_ARN", "")
AUTH_MODE = os.environ.get("AUTH_MODE", "none")  # "none", "bearer", "basic", or "header"
# Used only when AUTH_MODE="header": the header name to send the secret value
# under, verbatim (no "Bearer "/"Basic " prefix). Needed for vendors with a
# custom auth header, e.g. Mackerel's "Mackerel-Api-Key".
AUTH_HEADER_NAME = os.environ.get("AUTH_HEADER_NAME", "Authorization")
# Optional static (non-secret) extra headers as a JSON object string, e.g.
# '{"Accept": "*/*"}' — needed for Mackerel's OTLP endpoint.
EXTRA_HEADERS_JSON = os.environ.get("EXTRA_HEADERS_JSON", "")
# "json" (default) or "protobuf". Some vendors' OTLP/HTTP endpoints only
# accept Protobuf and reject OTLP/JSON (confirmed for Mackerel's log-feature
# endpoint). Set to "protobuf" for those vendors when sending directly to
# the vendor's endpoint (not through an OTel Collector, which already
# sends Protobuf by default).
OTLP_CONTENT_TYPE = os.environ.get("OTLP_CONTENT_TYPE", "json")

# ─── Constants ──────────────────────────────────────────────────────────────

MAX_RETRIES = 3
BASE_INTERVAL = 2

# FPolicy field mapping to OTLP attributes
# Supports both "operation_type" (SQS/FPolicy server) and "operation" (EventBridge) field names
FPOLICY_FIELD_MAPPING = {
    "operation_type": "operation_type",
    "operation": "operation_type",
    "file_path": "file_path",
    "user": "user",
    "client_ip": "client_ip",
}

# ─── Logger ────────────────────────────────────────────────────────────────

logger = logging.getLogger()
logger.setLevel(getattr(logging, LOG_LEVEL))

# ─── Startup validation (shared module) ────────────────────────────────────

validate_auth_mode_header(AUTH_MODE, AUTH_HEADER_NAME, logger)
EXTRA_HEADERS_JSON = validate_extra_headers_json(EXTRA_HEADERS_JSON, logger)

# HTTP client
http = urllib3.PoolManager(
    num_pools=4,
    maxsize=10,
    retries=urllib3.Retry(total=0),
)

# AWS client (initialized outside handler for connection reuse)
secrets_client = boto3.client("secretsmanager")

# Cache for API key (Lambda execution context reuse)
_api_key_cache: Optional[str] = None


def get_api_key() -> str:
    """Retrieve optional auth token from Secrets Manager with caching.

    Supports both plain string and JSON format secrets:
    - Plain string: "your-token"
    - JSON: {"api_key": "your-token"}
    """
    global _api_key_cache
    if _api_key_cache is None:
        response = secrets_client.get_secret_value(SecretId=API_KEY_SECRET_ARN)
        secret = response["SecretString"]
        try:
            parsed = json.loads(secret)
            _api_key_cache = parsed.get("api_key", parsed.get("token", secret))
        except (json.JSONDecodeError, AttributeError):
            _api_key_cache = secret
    return _api_key_cache


# ─── OTLP payload construction ────────────────────────────────────────────


def _timestamp_to_unix_nano(timestamp: Optional[str]) -> str:
    """Convert ISO 8601 timestamp to Unix nanoseconds string.

    Args:
        timestamp: ISO 8601 timestamp string.

    Returns:
        Unix nanoseconds as string. Falls back to current time.
    """
    if not timestamp:
        return str(int(datetime.now(timezone.utc).timestamp() * 1_000_000_000))

    try:
        ts = timestamp.strip()
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return str(int(dt.timestamp() * 1_000_000_000))
    except (ValueError, AttributeError):
        return str(int(datetime.now(timezone.utc).timestamp() * 1_000_000_000))


def build_fpolicy_otlp_payload(event_detail: dict[str, Any]) -> dict[str, Any]:
    """Build OTLP Log Data Model payload from a FPolicy event.

    Args:
        event_detail: EventBridge event detail dict with FPolicy fields.

    Returns:
        OTLP payload dict with resourceLogs structure.
    """
    timestamp = event_detail.get("timestamp", event_detail.get("time", ""))
    time_unix_nano = _timestamp_to_unix_nano(timestamp)

    # Build attributes from FPolicy fields
    attributes: list[dict[str, Any]] = []
    for source_field, otlp_key in FPOLICY_FIELD_MAPPING.items():
        value = event_detail.get(source_field)
        if value is not None and str(value) != "":
            attributes.append({
                "key": otlp_key,
                "value": {"stringValue": str(value)},
            })

    # Add any additional fields as attributes
    for key in ("volume_name", "volume", "svm", "vserver", "protocol", "event_id", "file_size", "timestamp"):
        value = event_detail.get(key)
        if value is not None and str(value) != "":
            attributes.append({
                "key": key,
                "value": {"stringValue": str(value)},
            })

    # Determine severity (file operations are INFO by default)
    severity_number = 9
    severity_text = "INFO"

    # Build logRecord
    log_record: dict[str, Any] = {
        "timeUnixNano": time_unix_nano,
        "severityNumber": severity_number,
        "severityText": severity_text,
        "body": {
            "stringValue": json.dumps(event_detail, default=str),
        },
        "attributes": attributes,
    }

    # Build full OTLP payload
    payload: dict[str, Any] = {
        "resourceLogs": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": SERVICE_NAME}},
                    ]
                },
                "scopeLogs": [
                    {
                        "scope": {
                            "name": "fsxn-fpolicy-shipper",
                            "version": "1.0.0",
                        },
                        "logRecords": [log_record],
                    }
                ],
            }
        ]
    }

    return payload


# ─── OTLP delivery ────────────────────────────────────────────────────────


def _send_otlp_payload(
    payload: dict[str, Any],
    auth_headers: Optional[dict[str, str]] = None,
    content_type: str = "json",
) -> bool:
    """Send OTLP payload to the configured endpoint with retry.

    Args:
        payload: OTLP-JSON-style payload dict.
        auth_headers: Optional additional headers for authentication.
        content_type: "json" (default) or "protobuf" — see handler.py's
            _send_otlp_payload docstring for the vendor-compatibility reason.

    Returns:
        True if successfully sent, False otherwise.
    """
    url = f"{OTLP_ENDPOINT}/v1/logs"

    if content_type == "protobuf":
        headers = {"Content-Type": "application/x-protobuf"}
        body = encode_logs_data(payload)
    else:
        headers = {"Content-Type": "application/json"}
        body = json.dumps(payload).encode("utf-8")

    if auth_headers:
        headers.update(auth_headers)

    for attempt in range(MAX_RETRIES):
        try:
            response = http.request(
                "POST",
                url,
                body=body,
                headers=headers,
                timeout=30.0,
            )

            if response.status < 300:
                logger.debug("FPolicy OTLP payload sent successfully (attempt %d)", attempt + 1)
                return True

            if response.status == 429:
                retry_after = int(
                    response.headers.get("Retry-After", BASE_INTERVAL * (2 ** attempt))
                )
                logger.warning("OTLP rate limited, retrying in %ds", retry_after)
                time.sleep(retry_after)
                continue

            if response.status >= 500:
                wait_time = BASE_INTERVAL * (2 ** attempt) + random.uniform(0, 1)
                logger.warning("OTLP server error %d, retrying in %.1fs", response.status, wait_time)
                time.sleep(wait_time)
                continue

            logger.error("OTLP error %d: %s", response.status,
                         response.data.decode("utf-8", errors="replace")[:500])
            return False

        except urllib3.exceptions.HTTPError as e:
            wait_time = BASE_INTERVAL * (2 ** attempt) + random.uniform(0, 1)
            logger.warning("HTTP error (attempt %d/%d): %s", attempt + 1, MAX_RETRIES, str(e))
            if attempt < MAX_RETRIES - 1:
                time.sleep(wait_time)

    return False


# ─── Lambda handler ────────────────────────────────────────────────────────


def _resolve_request_headers() -> Optional[dict[str, str]]:
    """Build the auth + static extra headers for OTLP requests.

    Shared by the SQS and EventBridge paths so both send identical headers.

    Returns:
        Header dict, or None when no auth and no extra headers are configured.
    """
    auth_headers: Optional[dict[str, str]] = None

    if API_KEY_SECRET_ARN and AUTH_MODE != "none":
        try:
            token = get_api_key()
            if token:
                if AUTH_MODE == "basic":
                    encoded = base64.b64encode(token.encode("utf-8")).decode("utf-8")
                    auth_headers = {"Authorization": f"Basic {encoded}"}
                elif AUTH_MODE == "header":
                    auth_headers = {AUTH_HEADER_NAME: token}
                else:
                    auth_headers = {"Authorization": f"Bearer {token}"}
        except Exception as e:
            logger.warning("Could not retrieve auth token: %s", str(e))

    if EXTRA_HEADERS_JSON:
        try:
            extra_headers = json.loads(EXTRA_HEADERS_JSON)
            auth_headers = {**(auth_headers or {}), **extra_headers}
        except json.JSONDecodeError:
            logger.warning(
                "EXTRA_HEADERS_JSON is not valid JSON, ignoring: %s",
                EXTRA_HEADERS_JSON[:100],
            )

    return auth_headers


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Handle FPolicy events from SQS (primary) or EventBridge (secondary).

    Two trigger paths exist:

    1. **SQS event source mapping** (primary): ONTAP → Fargate → SQS → Lambda.
       Returns a ``batchItemFailures`` response so only the failing messages
       are retried and eventually redriven to the DLQ. This requires
       ``FunctionResponseTypes: [ReportBatchItemFailures]`` on the event source
       mapping — without it, returning a success dict makes SQS delete every
       message in the batch even when OTLP delivery failed.
    2. **EventBridge rule** (secondary): a single event with source
       ``fpolicy.fsxn`` carrying the FPolicy data in ``detail``.

    Args:
        event: SQS batch event or EventBridge event with FPolicy details.
        context: Lambda context object.

    Returns:
        For SQS: ``{"batchItemFailures": [...]}``.
        For EventBridge: dict with statusCode and processing summary.
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

    Each message is shipped as its own OTLP request so a delivery failure is
    attributable to exactly one message. Unparseable messages are reported as
    failures too, so the queue's redrive policy moves them to the DLQ (visible)
    instead of dropping them silently.

    Args:
        event: SQS batch event dict.

    Returns:
        ``{"batchItemFailures": [{"itemIdentifier": "<messageId>"}, ...]}``.
        An empty list means the whole batch succeeded.
    """
    records = event["Records"]
    logger.info("FPolicy handler invoked: SQS batch of %d record(s)", len(records))

    auth_headers = _resolve_request_headers()
    batch_item_failures: list[dict[str, str]] = []
    shipped = 0

    for record in records:
        message_id = record.get("messageId", "")
        try:
            detail = json.loads(record.get("body", ""))
            if not isinstance(detail, dict):
                raise ValueError(
                    f"SQS message body is not a JSON object "
                    f"(got {type(detail).__name__})"
                )
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.error(
                "Unparseable SQS message %s: %s — reporting as failure for DLQ redrive",
                message_id,
                str(e),
            )
            batch_item_failures.append({"itemIdentifier": message_id})
            continue

        try:
            payload = build_fpolicy_otlp_payload(detail)
            if not _send_otlp_payload(payload, auth_headers, OTLP_CONTENT_TYPE):
                raise RuntimeError("OTLP delivery failed after retries")
            shipped += 1
        except Exception as e:
            logger.error(
                "OTLP delivery failed for SQS message %s: %s — reporting for retry",
                message_id,
                str(e),
            )
            batch_item_failures.append({"itemIdentifier": message_id})

    logger.info(
        "SQS batch complete: shipped %d/%d, reporting %d failure(s)",
        shipped,
        len(records),
        len(batch_item_failures),
    )

    # Response shape required by ReportBatchItemFailures. An empty list means
    # the whole batch succeeded and every message is deleted.
    return {"batchItemFailures": batch_item_failures}


def _handle_eventbridge_event(event: dict[str, Any]) -> dict[str, Any]:
    """Handle a single FPolicy event delivered by an EventBridge rule.

    Args:
        event: EventBridge event with FPolicy file operation details.

    Returns:
        Response dict with statusCode and processing summary.
    """
    logger.info("FPolicy event received: %s", json.dumps(event, default=str)[:500])

    auth_headers = _resolve_request_headers()

    try:
        # Extract event detail (EventBridge format)
        detail = event.get("detail", event)

        # Validate minimum required fields
        operation = detail.get("operation", "unknown")
        file_path = detail.get("file_path", "unknown")

        logger.info(
            "FPolicy event: operation=%s file_path=%s user=%s client_ip=%s",
            operation,
            file_path,
            detail.get("user", "unknown"),
            detail.get("client_ip", "unknown"),
        )

        # Build OTLP payload
        payload = build_fpolicy_otlp_payload(detail)

        # Forward to OTel Collector
        success = _send_otlp_payload(payload, auth_headers, OTLP_CONTENT_TYPE)

        if success:
            return {
                "statusCode": 200,
                "body": {
                    "status": "ok",
                    "operation": operation,
                    "file_path": file_path,
                    "otlp_delivered": True,
                },
            }
        else:
            return {
                "statusCode": 502,
                "body": {
                    "status": "error",
                    "message": "OTLP delivery failed after retries",
                    "operation": operation,
                    "file_path": file_path,
                },
            }

    except Exception as e:
        logger.error("Unexpected error processing FPolicy event: %s", str(e))
        return {
            "statusCode": 500,
            "body": {"status": "error", "message": str(e)},
        }
