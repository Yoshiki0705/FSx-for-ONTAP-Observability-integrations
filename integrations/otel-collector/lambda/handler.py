"""FSx for ONTAP audit log shipper via OTLP/HTTP.

Reads audit logs from S3 Access Point, parses JSON format,
maps fields to OTLP Log Data Model attributes, and sends
to a configured OTel Collector endpoint via OTLP/HTTP.

The Lambda is backend-agnostic. When sending through an OTel Collector
(recommended), adding/removing destinations requires only Collector config
changes, zero Lambda code changes. When sending directly to a vendor's OTLP
endpoint (no Collector), the Lambda supports pluggable auth via AUTH_MODE
("bearer", "basic", or "header" for a custom header name/value) and optional
EXTRA_HEADERS_JSON for static non-secret headers a vendor's endpoint requires
(e.g. Mackerel's required "Accept: */*"). It also supports OTLP_CONTENT_TYPE
("json", the default, or "protobuf") for vendors whose OTLP/HTTP endpoint
only accepts Protobuf-encoded request bodies and rejects OTLP/JSON outright
(confirmed for Mackerel's log-feature endpoint). No vendor-specific branching
exists in this file — only generic, vendor-agnostic primitives.
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

from otlp_auth import build_auth_headers, validate_auth_mode_header, validate_extra_headers_json
from otlp_protobuf import encode_logs_data

# ─── ONTAP audit log parsing ────────────────────────────────────────────────
# ONTAP writes audit logs as EVTX or XML (vserver audit create -format), never
# JSON. The shared parser handles namespaced Windows Event Log XML, EVTX
# detection and JSON, and normalizes field names. Imported defensively so a
# packaging miss degrades to the JSON-only fallback instead of breaking the
# function at import time.
try:
    from ontap_audit_parser import parse_audit_log as _parse_ontap_audit_log
except ImportError:  # pragma: no cover - packaging fallback
    _parse_ontap_audit_log = None


# ─── Configuration from environment ────────────────────────────────────────

OTLP_ENDPOINT = os.environ.get("OTLP_ENDPOINT", "http://localhost:4318")
API_KEY_SECRET_ARN = os.environ.get("API_KEY_SECRET_ARN", "")
S3_ACCESS_POINT_ARN = os.environ.get("S3_ACCESS_POINT_ARN", "")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
SERVICE_NAME = os.environ.get("SERVICE_NAME", "fsxn-audit")
AUTH_MODE = os.environ.get("AUTH_MODE", "bearer")  # "bearer", "basic", or "header"
# Used only when AUTH_MODE="header": the header name to send the secret value
# under, verbatim (no "Bearer "/"Basic " prefix, no encoding). Needed for
# vendors with a custom auth header, e.g. Mackerel's "Mackerel-Api-Key", when
# sending directly to the vendor's OTLP endpoint (skipping a local Collector).
AUTH_HEADER_NAME = os.environ.get("AUTH_HEADER_NAME", "Authorization")
# Optional static (non-secret) extra headers as a JSON object string, e.g.
# '{"Accept": "*/*"}' — needed for Mackerel's OTLP endpoint, which requires
# this header regardless of auth mode. Leave unset for vendors that don't need it.
EXTRA_HEADERS_JSON = os.environ.get("EXTRA_HEADERS_JSON", "")
# "json" (default) or "protobuf". Some vendors' OTLP/HTTP endpoints only
# accept Protobuf and reject OTLP/JSON with an HTTP 400 (confirmed for
# Mackerel's log-feature endpoint: {"code":400,"message":"json is not
# supported yet"}). Set to "protobuf" for those vendors when sending
# directly to the vendor's endpoint (i.e. not through an OTel Collector,
# which already sends Protobuf by default).
OTLP_CONTENT_TYPE = os.environ.get("OTLP_CONTENT_TYPE", "json")

# ─── Constants ──────────────────────────────────────────────────────────────

MAX_RETRIES = 3
BASE_INTERVAL = 2  # seconds

# OTLP field mapping: FSx for ONTAP field → OTLP attribute key
FIELD_MAPPING = {
    "EventID": "event.type",
    "UserName": "user.name",
    "ClientIP": "client.address",
    "Operation": "fsxn.operation",
    "ObjectName": "fsxn.path",
    "Result": "fsxn.result",
    "SVMName": "fsxn.svm",
}

# Severity keywords (case-insensitive)
WARN_KEYWORDS = ("fail", "denied", "error")

# ─── Logger setup ──────────────────────────────────────────────────────────

logger = logging.getLogger()
logger.setLevel(getattr(logging, LOG_LEVEL))

# ─── Startup validation (uses shared module, after logger init) ─────────────

validate_auth_mode_header(AUTH_MODE, AUTH_HEADER_NAME, logger)
EXTRA_HEADERS_JSON = validate_extra_headers_json(EXTRA_HEADERS_JSON, logger)

# ─── AWS clients (initialized outside handler for connection reuse) ─────────

secrets_client = boto3.client("secretsmanager")
s3_client = boto3.client("s3")

# HTTP client with connection pooling
http = urllib3.PoolManager(
    num_pools=4,
    maxsize=10,
    retries=urllib3.Retry(total=0),
)

# Cache for API key (Lambda execution context reuse)
_api_key_cache: Optional[str] = None


# ─── Pure functions: Field mapping and OTLP payload construction ───────────


def determine_severity(result: Optional[str]) -> tuple[int, str]:
    """Determine OTLP severity from FSx for ONTAP Result field.

    Args:
        result: The Result field value from an audit log record.

    Returns:
        Tuple of (severityNumber, severityText).
        (13, "WARN") if result contains "fail", "denied", or "error" (case-insensitive).
        (9, "INFO") otherwise (including None or empty string).
    """
    if not result:
        return (9, "INFO")
    lower = result.lower()
    for keyword in WARN_KEYWORDS:
        if keyword in lower:
            return (13, "WARN")
    return (9, "INFO")


def timestamp_to_unix_nano(timestamp: Optional[str]) -> str:
    """Convert ISO 8601 timestamp to Unix nanoseconds string.

    Args:
        timestamp: ISO 8601 timestamp string (e.g., "2026-01-15T12:00:01Z").

    Returns:
        Unix nanoseconds as a string. Falls back to current time if input
        is None, empty, or unparseable.
    """
    if not timestamp:
        return str(int(datetime.now(timezone.utc).timestamp() * 1_000_000_000))

    try:
        # Handle various ISO 8601 formats
        ts = timestamp.strip()
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return str(int(dt.timestamp() * 1_000_000_000))
    except (ValueError, AttributeError):
        return str(int(datetime.now(timezone.utc).timestamp() * 1_000_000_000))


def map_log_record(log: dict[str, Any]) -> dict[str, Any]:
    """Map a single FSx for ONTAP audit log to an OTLP logRecord.

    Applies field mapping from FSx for ONTAP fields to OTLP attributes.
    Absent or empty source fields are omitted from the attributes array.

    Args:
        log: A single parsed audit log record dict.

    Returns:
        OTLP logRecord dict with timeUnixNano, severityNumber,
        severityText, body, and attributes.
    """
    # Determine severity from Result field
    severity_number, severity_text = determine_severity(log.get("Result"))

    # Convert timestamp
    time_unix_nano = timestamp_to_unix_nano(
        log.get("timestamp", log.get("Timestamp"))
    )

    # Build attributes — omit absent or empty fields
    attributes: list[dict[str, Any]] = []
    for source_field, otlp_key in FIELD_MAPPING.items():
        value = log.get(source_field)
        if value is not None and value != "":
            attributes.append({
                "key": otlp_key,
                "value": {"stringValue": str(value)},
            })

    # Build logRecord
    log_record: dict[str, Any] = {
        "timeUnixNano": time_unix_nano,
        "severityNumber": severity_number,
        "severityText": severity_text,
        "body": {
            "stringValue": json.dumps(log, default=str),
        },
        "attributes": attributes,
    }

    return log_record


def build_otlp_payload(
    logs: list[dict[str, Any]],
    service_name: str,
    source_key: str,
) -> dict[str, Any]:
    """Build OTLP Log Data Model payload.

    Constructs the full nested OTLP structure with resource attributes,
    scope metadata, and mapped log records.

    Args:
        logs: List of parsed FSx for ONTAP audit log dicts.
        service_name: Value for the service.name resource attribute.
        source_key: S3 object key (for traceability, not included in payload).

    Returns:
        OTLP payload dict with resourceLogs structure.
    """
    log_records = [map_log_record(log) for log in logs]

    payload: dict[str, Any] = {
        "resourceLogs": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": service_name}},
                        {"key": "cloud.provider", "value": {"stringValue": "aws"}},
                        {"key": "cloud.platform", "value": {"stringValue": "aws_fsx"}},
                    ]
                },
                "scopeLogs": [
                    {
                        "scope": {
                            "name": "fsxn-otel-shipper",
                            "version": "1.0.0",
                        },
                        "logRecords": log_records,
                    }
                ],
            }
        ]
    }

    return payload


# ─── S3 and HTTP logic ─────────────────────────────────────────────────────


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


def _extract_s3_records(event: dict[str, Any]) -> list[dict[str, str]]:
    """Extract S3 bucket/key pairs from event.

    Handles both S3 event notification and EventBridge formats.

    Args:
        event: Lambda event payload.

    Returns:
        List of dicts with 'bucket' and 'key' fields.
    """
    records: list[dict[str, str]] = []

    # S3 event notification format
    if "Records" in event:
        for record in event["Records"]:
            s3_info = record.get("s3", {})
            records.append({
                "bucket": s3_info.get("bucket", {}).get("name", ""),
                "key": s3_info.get("object", {}).get("key", ""),
            })

    # EventBridge S3 Object Created format
    elif "detail" in event:
        detail = event["detail"]
        records.append({
            "bucket": detail.get("bucket", {}).get("name", ""),
            "key": detail.get("object", {}).get("key", ""),
        })

    return [r for r in records if r["bucket"] and r["key"]]


def _read_s3_object(bucket: str, key: str) -> bytes:
    """Read object from S3 Access Point.

    Args:
        bucket: S3 bucket name (unused when S3_ACCESS_POINT_ARN is set).
        key: S3 object key.

    Returns:
        Raw file content as bytes.
    """
    response = s3_client.get_object(Bucket=S3_ACCESS_POINT_ARN, Key=key)
    return response["Body"].read()


def _parse_json_logs(data: str) -> list[dict[str, Any]]:
    """Parse JSON format audit logs (newline-delimited or array).

    Args:
        data: Raw text content.

    Returns:
        List of parsed log event dicts.
    """
    events: list[dict[str, Any]] = []
    data = data.strip()

    if not data:
        return events

    # Try as JSON array first
    if data.startswith("["):
        try:
            events = json.loads(data)
            return events if isinstance(events, list) else [events]
        except json.JSONDecodeError:
            pass

    # Newline-delimited JSON
    for line in data.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
            events.append(event)
        except json.JSONDecodeError:
            logger.debug("Skipping non-JSON line: %s", line[:100])
            continue

    return events


def _parse_audit_logs(data: bytes, key: str) -> list[dict[str, Any]]:
    """Parse an FSx for ONTAP audit log file into normalized events.

    Delegates to the shared ONTAP parser, which understands the EVTX and XML
    formats ONTAP actually writes (`vserver audit create -format {evtx|xml}`).
    The previous JSON-only implementation dropped every non-JSON line, so an XML
    audit log yielded zero events with nothing but a debug log to show for it.

    Args:
        data: Raw file content.
        key: S3 object key (used to determine format).

    Returns:
        List of parsed log events.
    """
    if _parse_ontap_audit_log is not None:
        return _parse_ontap_audit_log(data, key)

    logger.warning(
        "ontap_audit_parser unavailable; falling back to JSON-only parsing. "
        "XML/EVTX audit logs will NOT be parsed into fields."
    )
    text = data.decode("utf-8", errors="replace")
    return _parse_json_logs(text)


def _send_otlp_payload(
    payload: dict[str, Any],
    endpoint: str,
    auth_headers: Optional[dict[str, str]] = None,
    content_type: str = "json",
) -> bool:
    """Send OTLP payload via HTTP POST with retry logic.

    Implements exponential backoff (2s, 4s, 8s) with max 3 attempts.
    Retries on HTTP 429 and 5xx. Does not retry on 4xx (except 429).

    Args:
        payload: OTLP-JSON-style payload dict (resourceLogs -> scopeLogs ->
            logRecords), regardless of the wire format actually sent.
        endpoint: Full URL (e.g., "http://collector:4318/v1/logs").
        auth_headers: Optional additional headers for authentication.
        content_type: "json" (default) sends OTLP/JSON with
            Content-Type: application/json. "protobuf" encodes the same
            payload as OTLP/Protobuf (via otlp_protobuf.encode_logs_data)
            and sends it with Content-Type: application/x-protobuf, for
            vendors whose OTLP/HTTP endpoint rejects JSON bodies.

    Returns:
        True if successfully sent, False otherwise.
    """
    url = f"{endpoint}/v1/logs"

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
                logger.debug(
                    "OTLP payload sent successfully (attempt %d)", attempt + 1
                )
                return True

            if response.status == 429:
                retry_after = int(
                    response.headers.get("Retry-After", BASE_INTERVAL * (2 ** attempt))
                )
                logger.warning(
                    "OTLP endpoint rate limited, retrying in %ds", retry_after
                )
                time.sleep(retry_after)
                continue

            if response.status >= 500:
                wait_time = BASE_INTERVAL * (2 ** attempt) + random.uniform(0, 1)
                logger.warning(
                    "OTLP endpoint server error %d, retrying in %.1fs",
                    response.status,
                    wait_time,
                )
                time.sleep(wait_time)
                continue

            # Client error (4xx except 429) — don't retry
            logger.error(
                "OTLP endpoint error %d: %s",
                response.status,
                response.data.decode("utf-8", errors="replace")[:500],
            )
            return False

        except urllib3.exceptions.HTTPError as e:
            wait_time = BASE_INTERVAL * (2 ** attempt) + random.uniform(0, 1)
            logger.warning(
                "HTTP error sending OTLP payload (attempt %d/%d): %s",
                attempt + 1,
                MAX_RETRIES,
                str(e),
            )
            if attempt < MAX_RETRIES - 1:
                time.sleep(wait_time)

    return False


# ─── Lambda handler ────────────────────────────────────────────────────────


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda handler for FSx for ONTAP audit log shipping via OTLP/HTTP.

    Supports both S3 event notifications and EventBridge events.

    Args:
        event: S3 event notification or EventBridge event.
        context: Lambda context object.

    Returns:
        Response with status code and processing summary.
    """
    logger.info("Processing event: %s", json.dumps(event, default=str))

    # Get optional auth headers
    auth_headers: Optional[dict[str, str]] = None
    if API_KEY_SECRET_ARN:
        try:
            token = get_api_key()
            if token:
                if AUTH_MODE == "basic":
                    # For Grafana Cloud OTLP: token should be "instanceId:apiToken"
                    encoded = base64.b64encode(token.encode("utf-8")).decode("utf-8")
                    auth_headers = {"Authorization": f"Basic {encoded}"}
                elif AUTH_MODE == "header":
                    # Generic custom-header auth (e.g. Mackerel: header
                    # "Mackerel-Api-Key", token is the raw write-scoped API key).
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

    records = _extract_s3_records(event)

    total_logs = 0
    total_shipped = 0
    errors: list[dict[str, str]] = []

    for record in records:
        bucket = record["bucket"]
        key = record["key"]
        logger.info("Processing object: s3://%s/%s", bucket, key)

        try:
            # Read from S3 Access Point
            data = _read_s3_object(bucket, key)

            # Parse audit logs
            logs = _parse_audit_logs(data, key)
            total_logs += len(logs)

            if not logs:
                logger.info("No logs found in %s", key)
                continue

            # Build OTLP payload
            payload = build_otlp_payload(logs, SERVICE_NAME, key)

            # Send to OTLP endpoint
            success = _send_otlp_payload(payload, OTLP_ENDPOINT, auth_headers, OTLP_CONTENT_TYPE)

            if success:
                total_shipped += len(logs)
            else:
                errors.append({
                    "bucket": bucket,
                    "key": key,
                    "error": "OTLP delivery failed after retries",
                })

        except Exception as e:
            logger.error("Failed to process %s/%s: %s", bucket, key, str(e))
            errors.append({"bucket": bucket, "key": key, "error": str(e)})

    # Determine status code
    if errors and total_shipped == 0:
        status_code = 502
    elif errors:
        status_code = 207
    else:
        status_code = 200

    result = {
        "statusCode": status_code,
        "body": {
            "total_logs": total_logs,
            "total_shipped": total_shipped,
            "errors": errors,
        },
    }
    logger.info("Processing complete: %s", json.dumps(result))
    return result
