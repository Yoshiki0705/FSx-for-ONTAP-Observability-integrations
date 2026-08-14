"""FSx for ONTAP audit log shipper for Splunk via HTTP Event Collector (HEC).

Serverless alternative to the EC2-based syslog-ng + Universal Forwarder pattern.
Ships audit logs directly to Splunk HEC endpoint from Lambda.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import re
import time
from typing import Any

import boto3
import urllib3
from botocore.exceptions import ClientError

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


# Configuration
HEC_ENDPOINT = os.environ.get("SPLUNK_HEC_ENDPOINT", "")
API_KEY_SECRET_ARN = os.environ["API_KEY_SECRET_ARN"]
S3_ACCESS_POINT_ARN = os.environ["S3_ACCESS_POINT_ARN"]
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
SPLUNK_INDEX = os.environ.get("SPLUNK_INDEX", "fsxn_audit")
SPLUNK_SOURCETYPE = os.environ.get("SPLUNK_SOURCETYPE", "fsxn:ontap:audit")
SPLUNK_SOURCE = os.environ.get("SPLUNK_SOURCE", "fsxn-observability")

# HEC has no hard batch size limit, but recommended chunking for reliability
MAX_BATCH_EVENTS = 500
MAX_RETRIES = 3
HEC_TOKEN_RETRIEVAL_TIMEOUT = 5  # seconds

# Disable SSL verification for self-signed certs (common in Splunk deployments)
VERIFY_SSL = os.environ.get("VERIFY_SSL", "true").lower() == "true"

# HEC token format: UUID pattern (8-4-4-4-12 hexadecimal characters)
_HEC_TOKEN_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

logger = logging.getLogger()
logger.setLevel(getattr(logging, LOG_LEVEL))

secrets_client = boto3.client("secretsmanager")
s3_client = boto3.client("s3")
http = urllib3.PoolManager(
    num_pools=4,
    maxsize=10,
    cert_reqs="CERT_REQUIRED" if VERIFY_SSL else "CERT_NONE",
)

_hec_token_cache: str | None = None


def get_hec_token() -> str:
    """Retrieve and validate Splunk HEC token from Secrets Manager.

    Retrieves the HEC token from AWS Secrets Manager, validates it against
    the UUID format (8-4-4-4-12 hexadecimal characters), and caches the
    validated token in a module-level variable for reuse within the same
    Lambda execution context.

    Returns:
        Validated HEC token string in UUID format.

    Raises:
        ValueError: If token is empty or doesn't match UUID format.
        ClientError: If secret retrieval fails (ResourceNotFoundException,
            AccessDeniedException).
    """
    global _hec_token_cache
    if _hec_token_cache is not None:
        return _hec_token_cache

    start_time = time.time()
    try:
        response = secrets_client.get_secret_value(SecretId=API_KEY_SECRET_ARN)
    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        if error_code in ("ResourceNotFoundException", "AccessDeniedException"):
            logger.error(
                "Failed to retrieve HEC token: %s for secret ARN %s",
                error_code,
                API_KEY_SECRET_ARN,
            )
        raise

    elapsed = time.time() - start_time
    if elapsed > HEC_TOKEN_RETRIEVAL_TIMEOUT:
        logger.warning(
            "HEC token retrieval took %.2fs (timeout threshold: %ds)",
            elapsed,
            HEC_TOKEN_RETRIEVAL_TIMEOUT,
        )

    secret = response["SecretString"]
    # Support both plain string and JSON format secrets
    try:
        parsed = json.loads(secret)
        token = parsed.get("hec_token", parsed.get("SPLUNK_HEC_TOKEN", secret))
    except (json.JSONDecodeError, AttributeError):
        token = secret

    # Validate token is not empty
    if not token or not token.strip():
        logger.error("HEC token is empty for secret ARN %s", API_KEY_SECRET_ARN)
        raise ValueError(
            f"HEC token is empty for secret ARN {API_KEY_SECRET_ARN}"
        )

    # Validate UUID format (8-4-4-4-12 hex pattern)
    if not _HEC_TOKEN_PATTERN.match(token.strip()):
        logger.error(
            "Invalid HEC token format for secret ARN %s: "
            "expected UUID pattern (8-4-4-4-12 hex characters)",
            API_KEY_SECRET_ARN,
        )
        raise ValueError(
            f"Invalid HEC token format for secret ARN {API_KEY_SECRET_ARN}: "
            "expected UUID pattern (8-4-4-4-12 hex characters)"
        )

    _hec_token_cache = token.strip()
    return _hec_token_cache


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda handler for FSx for ONTAP audit log shipping to Splunk HEC.

    Processes S3 event records, reads audit logs from S3 Access Point,
    formats them as Splunk HEC JSON, and ships to the HEC endpoint.

    Records referencing non-existent or empty S3 objects are gracefully
    skipped with error details logged. Processing continues for remaining
    records regardless of individual record failures.

    Args:
        event: S3 event notification or EventBridge event containing
            Records with s3.bucket.name and s3.object.key fields.
        context: Lambda execution context.

    Returns:
        Response dict with statusCode (200 all success, 207 partial failure)
        and body containing total_logs, total_shipped, and errors list.
    """
    logger.info("Processing event")

    hec_token = get_hec_token()
    records = _extract_s3_records(event)

    total_logs = 0
    total_shipped = 0
    errors: list[dict[str, str]] = []

    for record in records:
        key = record["key"]

        try:
            data = s3_client.get_object(Bucket=S3_ACCESS_POINT_ARN, Key=key)
            raw = data["Body"].read()

            # Skip empty S3 objects
            if not raw:
                logger.error(
                    "Skipping empty S3 object: %s", key
                )
                errors.append({"key": key, "error": "S3 object is empty"})
                continue

            logs = _parse_logs(raw, key)
            total_logs += len(logs)

            hec_events = _format_for_splunk(logs, key)
            shipped = _ship_to_splunk(hec_events, hec_token)
            total_shipped += shipped

        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code in ("NoSuchKey", "NoSuchBucket"):
                logger.error(
                    "Skipping record — S3 object not found (%s): %s",
                    error_code,
                    key,
                )
                errors.append({"key": key, "error": f"{error_code}: {str(e)}"})
                continue
            # Re-raise unexpected ClientErrors (e.g., AccessDenied)
            logger.error("S3 ClientError for key %s: %s", key, str(e))
            errors.append({"key": key, "error": str(e)})

        except Exception as e:
            logger.error("Failed to process key %s: %s", key, str(e))
            errors.append({"key": key, "error": str(e)})

    status_code = 200 if not errors else 207
    return {
        "statusCode": status_code,
        "body": {
            "total_logs": total_logs,
            "total_shipped": total_shipped,
            "errors": errors,
        },
    }


def _extract_s3_records(event: dict[str, Any]) -> list[dict[str, str]]:
    """Extract S3 bucket/key pairs from event."""
    records = []
    if "Records" in event:
        for r in event["Records"]:
            s3 = r.get("s3", {})
            records.append({
                "bucket": s3.get("bucket", {}).get("name", ""),
                "key": s3.get("object", {}).get("key", ""),
            })
    elif "detail" in event:
        d = event["detail"]
        records.append({
            "bucket": d.get("bucket", {}).get("name", ""),
            "key": d.get("object", {}).get("key", ""),
        })
    return [r for r in records if r["bucket"] and r["key"]]


def _parse_logs(data: bytes, key: str) -> list[dict[str, Any]]:
    """Parse an FSx for ONTAP audit log file into normalized events.

    Delegates to the shared ONTAP parser, which understands the EVTX and XML
    formats ONTAP actually writes. Falls back to JSON lines only if that module
    was not packaged with this function.
    """
    if _parse_ontap_audit_log is not None:
        return _parse_ontap_audit_log(data, key)

    logger.warning(
        "ontap_audit_parser unavailable; falling back to JSON-only parsing. "
        "XML/EVTX audit logs will NOT be parsed into fields."
    )
    if key.endswith(".gz"):
        data = gzip.decompress(data)
    events = []
    for line in data.decode("utf-8", errors="replace").strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            events.append({"message": line})
    return events


def _format_for_splunk(
    logs: list[dict[str, Any]], source_key: str
) -> list[dict[str, Any]]:
    """Format logs for Splunk HEC /services/collector/event endpoint.

    Converts parsed FSx for ONTAP audit log entries into Splunk HEC JSON objects
    suitable for the /services/collector/event endpoint. Uses the SplunkHecEvent
    dataclass for type safety and consistent field mapping.

    Each output event contains: time (epoch seconds), host (SVMName),
    source, sourcetype, index, and a structured event dict.

    Args:
        logs: Parsed audit log entries. Each entry may contain fields such as
            EventID, SVMName, UserName, ClientIP, Operation, ObjectName,
            Result, and timestamp.
        source_key: The S3 object key from which the logs were read.

    Returns:
        List of HEC JSON dicts ready for serialization and submission to
        the Splunk HEC endpoint.
    """
    from models import SplunkHecEvent

    hec_events: list[dict[str, Any]] = []
    for log in logs:
        # Extract timestamp and convert to epoch seconds
        timestamp = log.get("timestamp", log.get("Timestamp", ""))
        epoch_time = _to_epoch(timestamp) if timestamp else None

        # Build structured event sub-object
        event_data: dict[str, Any] = {
            "event_type": log.get("EventID", log.get("event_type", "unknown")),
            "user": log.get("UserName", log.get("user", "")),
            "client_ip": log.get("ClientIP", log.get("client_ip", "")),
            "operation": log.get("Operation", log.get("operation", "")),
            "path": log.get("ObjectName", log.get("path", "")),
            "result": log.get("Result", log.get("result", "")),
            "svm": log.get("SVMName", log.get("svm", "")),
        }

        # Create typed HEC event using dataclass
        hec_event = SplunkHecEvent(
            time=epoch_time,
            host=log.get("SVMName", log.get("svm", "fsxn-ontap")),
            source=SPLUNK_SOURCE,
            sourcetype=SPLUNK_SOURCETYPE,
            index=SPLUNK_INDEX,
            event=event_data,
        )

        hec_events.append(hec_event.to_hec_json())

    return hec_events


def _to_epoch(timestamp: str) -> float | None:
    """Convert ISO timestamp to epoch seconds."""
    from datetime import datetime

    try:
        if timestamp.endswith("Z"):
            timestamp = timestamp[:-1] + "+00:00"
        dt = datetime.fromisoformat(timestamp)
        return dt.timestamp()
    except (ValueError, AttributeError):
        return None


def _ship_to_splunk(events: list[dict[str, Any]], hec_token: str) -> int:
    """Ship events to Splunk HEC in batches."""
    if not events:
        return 0

    shipped = 0
    for i in range(0, len(events), MAX_BATCH_EVENTS):
        batch = events[i : i + MAX_BATCH_EVENTS]
        if _send_batch(batch, hec_token):
            shipped += len(batch)
        else:
            logger.error("Failed to ship batch of %d events", len(batch))

    return shipped


def _send_to_hec(payload: str, token: str) -> bool:
    """Send payload to Splunk HEC endpoint with exponential backoff retry.

    Retries on HTTP 429 (rate limited) and 5xx (server errors) with
    exponential backoff: base delay 2 seconds, doubling per attempt,
    up to 3 total attempts. Does not retry on 4xx client errors
    (except 429).

    Args:
        payload: Newline-delimited JSON string of HEC events.
        token: Splunk HEC authentication token.

    Returns:
        True if the payload was successfully delivered, False otherwise.
    """
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Splunk {token}",
    }

    for attempt in range(MAX_RETRIES):
        try:
            response = http.request(
                "POST",
                f"{HEC_ENDPOINT}/services/collector/event",
                body=payload.encode("utf-8"),
                headers=headers,
                timeout=30.0,
            )

            if response.status == 200:
                return True

            # Parse HEC response for error details
            try:
                resp_body = json.loads(response.data.decode("utf-8"))
                code = resp_body.get("code", -1)
            except (json.JSONDecodeError, UnicodeDecodeError):
                code = -1

            # Retry on 429 (rate limited) or 5xx (server error)
            if response.status == 429 or response.status >= 500:
                if attempt < MAX_RETRIES - 1:
                    delay = 2 * (2 ** attempt)  # 2s, 4s
                    logger.warning(
                        "Splunk HEC returned %d (code=%d), "
                        "attempt %d/%d, retrying in %ds",
                        response.status, code,
                        attempt + 1, MAX_RETRIES, delay,
                    )
                    time.sleep(delay)
                    continue
                # Final attempt exhausted
                logger.error(
                    "Splunk HEC returned %d (code=%d), "
                    "all %d attempts exhausted",
                    response.status, code, MAX_RETRIES,
                )
                return False

            # 4xx (except 429): do not retry
            logger.error(
                "Splunk HEC client error %d (code=%d): %s",
                response.status, code,
                response.data.decode("utf-8", errors="replace")[:300],
            )
            return False

        except urllib3.exceptions.HTTPError as e:
            if attempt < MAX_RETRIES - 1:
                delay = 2 * (2 ** attempt)  # 2s, 4s
                logger.warning(
                    "HTTP error on attempt %d/%d: %s, retrying in %ds",
                    attempt + 1, MAX_RETRIES, str(e), delay,
                )
                time.sleep(delay)
            else:
                logger.error(
                    "HTTP error on final attempt %d/%d: %s",
                    attempt + 1, MAX_RETRIES, str(e),
                )
                return False

    return False


def _send_batch(batch: list[dict[str, Any]], hec_token: str) -> bool:
    """Send a batch of events to Splunk HEC with retry.

    Formats events as newline-delimited JSON (one JSON object per line,
    not a JSON array) and delegates to _send_to_hec for delivery with
    exponential backoff retry.

    Args:
        batch: List of HEC event dicts to send.
        hec_token: Splunk HEC authentication token.

    Returns:
        True if the batch was successfully delivered, False otherwise.
    """
    # HEC format: one JSON object per line (not an array)
    payload = "\n".join(json.dumps(event) for event in batch)
    return _send_to_hec(payload, hec_token)
