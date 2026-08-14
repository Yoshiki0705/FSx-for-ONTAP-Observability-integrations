"""EMS Webhook → OTel Collector OTLP log forwarder.

Receives ONTAP EMS events via API Gateway, parses them using the
shared ems-parser layer, formats into OTLP Log Data Model, and
forwards to the configured OTel Collector endpoint.

Supports ARP (Anti-Ransomware Protection) events, quota exceeded
events, and all other EMS event types.

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

# Conditionally import ems_parser (available as Lambda Layer)
try:
    from ems_parser import EmsParseError, parse_ems_event, format_ems_event
except ImportError:
    # Fallback for testing without the layer installed
    class EmsParseError(Exception):
        pass

    def parse_ems_event(payload):
        """Fallback parser for testing."""
        if payload is None:
            raise EmsParseError("payload is None")
        if isinstance(payload, str) and payload == "":
            raise EmsParseError("payload is empty")
        if isinstance(payload, str):
            try:
                data = json.loads(payload)
            except json.JSONDecodeError as e:
                raise EmsParseError(f"invalid JSON: {e}") from e
        else:
            data = payload
        if "messageName" not in data:
            raise EmsParseError("missing required field: messageName")
        return {
            "timestamp": data.get("time", ""),
            "event_name": data.get("messageName", ""),
            "severity": data.get("severity", ""),
            "source_node": data.get("node", ""),
            "svm": data.get("svmName", ""),
            "message": data.get("message", ""),
            "parameters": data.get("parameters", {}),
            "raw": data,
        }

    def format_ems_event(normalized):
        return json.dumps(normalized, ensure_ascii=False, default=str)


# ─── Configuration ─────────────────────────────────────────────────────────

OTLP_ENDPOINT = os.environ.get("OTLP_ENDPOINT", "http://localhost:4318")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
SERVICE_NAME = os.environ.get("SERVICE_NAME", "fsxn-ems")
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

# EMS severity to OTLP severity mapping
EMS_SEVERITY_MAP = {
    "emergency": (21, "FATAL"),
    "alert": (17, "ERROR"),
    "critical": (17, "ERROR"),
    "error": (17, "ERROR"),
    "warning": (13, "WARN"),
    "notice": (9, "INFO"),
    "informational": (9, "INFO"),
    "debug": (5, "DEBUG"),
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


def _ems_severity_to_otlp(severity: str) -> tuple[int, str]:
    """Map EMS severity string to OTLP severity number and text.

    Args:
        severity: EMS severity string (e.g., "alert", "warning").

    Returns:
        Tuple of (severityNumber, severityText).
    """
    return EMS_SEVERITY_MAP.get(severity.lower(), (9, "INFO"))


def _timestamp_to_unix_nano(timestamp: str) -> str:
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


def build_ems_otlp_payload(normalized: dict[str, Any]) -> dict[str, Any]:
    """Build OTLP Log Data Model payload from a normalized EMS event.

    Args:
        normalized: Normalized EMS event dict from parse_ems_event().

    Returns:
        OTLP payload dict with resourceLogs structure.
    """
    severity_num, severity_text = _ems_severity_to_otlp(
        normalized.get("severity", "informational")
    )
    time_unix_nano = _timestamp_to_unix_nano(normalized.get("timestamp", ""))

    # Build attributes from EMS event fields
    attributes: list[dict[str, Any]] = []

    if normalized.get("event_name"):
        attributes.append({
            "key": "event_name",
            "value": {"stringValue": normalized["event_name"]},
        })
    if normalized.get("severity"):
        attributes.append({
            "key": "severity",
            "value": {"stringValue": normalized["severity"]},
        })
    if normalized.get("source_node"):
        attributes.append({
            "key": "source_node",
            "value": {"stringValue": normalized["source_node"]},
        })
    if normalized.get("svm"):
        attributes.append({
            "key": "svm",
            "value": {"stringValue": normalized["svm"]},
        })

    # Add parameters as attributes
    params = normalized.get("parameters", {})
    for key, value in params.items():
        if value is not None and str(value) != "":
            attributes.append({
                "key": key,
                "value": {"stringValue": str(value)},
            })

    # Build logRecord
    log_record: dict[str, Any] = {
        "timeUnixNano": time_unix_nano,
        "severityNumber": severity_num,
        "severityText": severity_text,
        "body": {
            "stringValue": format_ems_event(normalized),
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
                        {"key": "service.namespace", "value": {"stringValue": "fsxn-ontap"}},
                    ]
                },
                "scopeLogs": [
                    {
                        "scope": {
                            "name": "fsxn-ems-shipper",
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
                logger.debug("EMS OTLP payload sent successfully (attempt %d)", attempt + 1)
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


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Handle EMS Webhook event from API Gateway.

    Parses the EMS event, builds an OTLP payload, and forwards to
    the OTel Collector.

    Args:
        event: API Gateway proxy event with body containing EMS payload.
        context: Lambda context object.

    Returns:
        API Gateway proxy response dict.
    """
    body = event.get("body", "")
    logger.info("EMS event received, body length: %d", len(body) if body else 0)

    # Get optional auth headers
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

    try:
        # Parse EMS event using shared layer
        normalized = parse_ems_event(body)
        logger.info(
            "EMS event parsed: event_name=%s severity=%s svm=%s",
            normalized["event_name"],
            normalized["severity"],
            normalized["svm"],
        )

        # Build OTLP payload
        payload = build_ems_otlp_payload(normalized)

        # Forward to OTel Collector
        success = _send_otlp_payload(payload, auth_headers, OTLP_CONTENT_TYPE)

        if success:
            return {
                "statusCode": 200,
                "body": json.dumps({
                    "status": "ok",
                    "event_name": normalized["event_name"],
                    "otlp_delivered": True,
                }),
            }
        else:
            return {
                "statusCode": 502,
                "body": json.dumps({
                    "status": "error",
                    "message": "OTLP delivery failed after retries",
                    "event_name": normalized["event_name"],
                }),
            }

    except EmsParseError as e:
        logger.error("EMS parse failed: %s", str(e))
        return {
            "statusCode": 400,
            "body": json.dumps({"status": "error", "message": str(e)}),
        }
    except Exception as e:
        logger.error("Unexpected error: %s", str(e))
        return {
            "statusCode": 500,
            "body": json.dumps({"status": "error", "message": str(e)}),
        }
