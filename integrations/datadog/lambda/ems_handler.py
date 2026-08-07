"""FSx for ONTAP EMS event shipper for Datadog.

Receives EMS (Event Management System) webhook events from API Gateway,
parses them using the shared EMS parser layer, and ships to Datadog
Logs Intake API v2.

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

# ─── EMS Parser Layer Import ───────────────────────────────────────────────
# The ems_parser module is provided as a Lambda Layer at runtime.
# Fallback stubs allow local development without the layer installed.

try:
    from ems_parser import parse_ems_event, format_ems_event
except ImportError:
    # Fallback for local development without Lambda Layer
    def parse_ems_event(raw_event: dict) -> dict:  # type: ignore[misc]
        """Stub: return raw event as-is when layer is unavailable."""
        return raw_event

    def format_ems_event(event: dict) -> dict:  # type: ignore[misc]
        """Stub: return event as-is when layer is unavailable."""
        return event


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
DD_SOURCE = os.environ.get("DD_SOURCE", "fsxn-ems")
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
    """Handle EMS webhook event from API Gateway.

    Receives an API Gateway proxy event containing one or more EMS events
    in the body, parses them using the shared EMS parser layer, formats
    for Datadog Logs API v2, and ships with retry logic.

    Args:
        event: API Gateway proxy event. The ``body`` field contains the
            EMS JSON payload (single object or JSON array).
        context: Lambda context object.

    Returns:
        API Gateway proxy response with statusCode and body.
    """
    logger.info("EMS handler invoked: requestId=%s", _get_request_id(event))

    # Extract and parse the EMS payload from API Gateway body
    try:
        ems_events = _extract_ems_events(event)
    except (json.JSONDecodeError, ValueError) as e:
        logger.error("Failed to parse EMS payload: %s", str(e))
        return _api_response(400, {"error": f"Invalid EMS payload: {e}"})

    if not ems_events:
        logger.warning("No EMS events found in payload")
        return _api_response(200, {"message": "No events to process", "shipped": 0})

    logger.info("Parsed %d EMS event(s)", len(ems_events))

    # Get API key
    try:
        api_key = get_api_key()
    except Exception as e:
        logger.error("Failed to retrieve API key: %s", str(e))
        return _api_response(500, {"error": "Failed to retrieve API key"})

    # Parse and normalize EMS events using the shared layer
    normalized_events = _normalize_ems_events(ems_events)

    # Format for Datadog Logs API v2
    dd_logs = _format_for_datadog(normalized_events)

    # Ship to Datadog in batches
    shipped = _ship_to_datadog(dd_logs, api_key)

    result_body = {
        "message": "EMS events processed",
        "total_events": len(ems_events),
        "shipped": shipped,
    }

    status_code = 200 if shipped == len(dd_logs) else 207
    logger.info("Processing complete: %s", json.dumps(result_body))
    return _api_response(status_code, result_body)


def _get_request_id(event: dict[str, Any]) -> str:
    """Extract API Gateway request ID from event context.

    Args:
        event: API Gateway proxy event.

    Returns:
        Request ID string, or "unknown" if not available.
    """
    request_context = event.get("requestContext", {})
    return request_context.get("requestId", "unknown")


def _extract_ems_events(event: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract EMS event(s) from API Gateway proxy event body.

    Handles both single EMS event objects and JSON arrays of events.
    The body may be a JSON string (from API Gateway) or already parsed dict
    (from direct invocation / testing).

    Args:
        event: API Gateway proxy event.

    Returns:
        List of raw EMS event dictionaries.

    Raises:
        ValueError: If body is missing or empty.
        json.JSONDecodeError: If body is not valid JSON.
    """
    body = event.get("body")

    if body is None:
        raise ValueError("Event body is missing")

    # API Gateway sends body as JSON string
    if isinstance(body, str):
        if not body.strip():
            raise ValueError("Event body is empty")
        parsed = json.loads(body)
    else:
        parsed = body

    # Support both single event and array of events
    if isinstance(parsed, list):
        return parsed
    elif isinstance(parsed, dict):
        return [parsed]
    else:
        raise ValueError(f"Unexpected body type: {type(parsed).__name__}")


def _normalize_ems_events(
    raw_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Normalize raw EMS events using the shared parser layer.

    Events that fail parsing are logged and skipped.

    Args:
        raw_events: List of raw EMS event dictionaries.

    Returns:
        List of normalized event dictionaries.
    """
    normalized: list[dict[str, Any]] = []
    for raw_event in raw_events:
        try:
            parsed = parse_ems_event(raw_event)
            normalized.append(parsed)
        except Exception as e:
            logger.warning(
                "Failed to parse EMS event: %s (event: %s)",
                str(e),
                json.dumps(raw_event, default=str)[:200],
            )
    return normalized


def _format_for_datadog(
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Format normalized EMS events for Datadog Logs Intake API v2.

    Each log entry includes:
    - ddsource: "fsxn-ems"
    - ddtags: source:fsxn-ems, service:fsxn-ontap, env:<DD_ENV>
    - hostname: source node from EMS event
    - service: "fsxn-ontap"
    - message: EMS event message
    - date: event timestamp
    - attributes: structured fields for Facet-based searching

    Args:
        events: List of normalized EMS event dictionaries.

    Returns:
        List of Datadog-formatted log entries.
    """
    dd_logs: list[dict[str, Any]] = []

    for event in events:
        dd_log: dict[str, Any] = {
            "ddsource": DD_SOURCE,
            "ddtags": (
                f"source:{DD_SOURCE},"
                f"service:{DD_SERVICE},"
                f"env:{DD_ENV}"
            ),
            "hostname": event.get("source_node", event.get("node", "fsxn-ontap")),
            "service": DD_SERVICE,
        }

        # Set message — prefer the parsed message, fall back to JSON dump
        message = event.get("message", "")
        if message:
            dd_log["message"] = message
        else:
            dd_log["message"] = json.dumps(event, default=str)

        # Set timestamp if available
        timestamp = event.get("timestamp", event.get("time", ""))
        if timestamp:
            dd_log["date"] = timestamp

        # Structured attributes for Facet-based searching
        dd_log["attributes"] = {
            "event_name": event.get("event_name", event.get("messageName", "")),
            "severity": event.get("severity", ""),
            "svm": event.get("svm", event.get("svmName", "")),
            "source_node": event.get("source_node", event.get("node", "")),
            "parameters": event.get("parameters", {}),
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


def _api_response(status_code: int, body: dict[str, Any]) -> dict[str, Any]:
    """Create an API Gateway proxy response.

    Args:
        status_code: HTTP status code.
        body: Response body dictionary (will be JSON-serialized).

    Returns:
        API Gateway proxy response dict.
    """
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
        },
        "body": json.dumps(body, default=str),
    }
