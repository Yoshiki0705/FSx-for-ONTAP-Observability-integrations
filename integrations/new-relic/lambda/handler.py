"""FSx for ONTAP audit log shipper for New Relic.

Reads audit logs from S3 Access Point, parses EVTX/JSON format,
and ships to New Relic Log API v1.
"""

import gzip
import json
import logging
import os
import time
from typing import Any, Optional

import boto3
import urllib3

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
NEW_RELIC_REGION = os.environ.get("NEW_RELIC_REGION", "US")
API_KEY_SECRET_ARN = os.environ["API_KEY_SECRET_ARN"]
S3_ACCESS_POINT_ARN = os.environ["S3_ACCESS_POINT_ARN"]
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

# New Relic endpoints by region
ENDPOINTS = {
    "US": "https://log-api.newrelic.com/log/v1",
    "EU": "https://log-api.eu.newrelic.com/log/v1",
}
INTAKE_URL = ENDPOINTS.get(NEW_RELIC_REGION, ENDPOINTS["US"])

# Limits: 1MB per payload (compressed), 10MB uncompressed
MAX_PAYLOAD_BYTES = 1 * 1024 * 1024  # 1MB compressed
MAX_UNCOMPRESSED_BYTES = 10 * 1024 * 1024  # 10MB uncompressed
MAX_RETRIES = 3

logger = logging.getLogger()
logger.setLevel(getattr(logging, LOG_LEVEL))

secrets_client = boto3.client("secretsmanager")
s3_client = boto3.client("s3")
http = urllib3.PoolManager(num_pools=4, maxsize=10)

_api_key_cache: Optional[str] = None


def get_api_key() -> str:
    """Retrieve New Relic License Key from Secrets Manager."""
    global _api_key_cache
    if _api_key_cache is None:
        response = secrets_client.get_secret_value(SecretId=API_KEY_SECRET_ARN)
        secret = response["SecretString"]
        try:
            parsed = json.loads(secret)
            _api_key_cache = parsed.get(
                "license_key", parsed.get("NR_LICENSE_KEY", secret)
            )
        except (json.JSONDecodeError, AttributeError):
            _api_key_cache = secret
    return _api_key_cache


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda handler for FSx for ONTAP audit log shipping to New Relic."""
    logger.info("Processing event with %d records", len(event.get("Records", [])))

    api_key = get_api_key()
    records = _extract_s3_records(event)

    total_logs = 0
    total_shipped = 0
    errors = []

    for record in records:
        bucket = record["bucket"]
        key = record["key"]

        try:
            data = s3_client.get_object(Bucket=S3_ACCESS_POINT_ARN, Key=key)
            raw = data["Body"].read()

            logs = _parse_logs(raw, key)
            total_logs += len(logs)

            nr_payload = _format_for_new_relic(logs, key)
            shipped = _ship_to_new_relic(nr_payload, api_key)
            total_shipped += shipped

        except Exception as e:
            logger.error("Failed to process %s/%s: %s", bucket, key, str(e))
            errors.append({"bucket": bucket, "key": key, "error": str(e)})

    return {
        "statusCode": 200 if not errors else 207,
        "body": {
            "total_logs": total_logs,
            "total_shipped": total_shipped,
            "errors": errors,
        },
    }


def _extract_s3_records(event: dict[str, Any]) -> list[dict[str, str]]:
    """Extract S3 bucket/key pairs from S3 or EventBridge events."""
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


def _format_for_new_relic(
    logs: list[dict[str, Any]], source_key: str
) -> list[dict[str, Any]]:
    """Format logs for New Relic Log API v1.

    New Relic expects a JSON array of log entry objects wrapped in a
    common block with attributes.
    """
    formatted = []
    for log in logs:
        entry: dict[str, Any] = {
            "message": log.get("message", json.dumps(log, default=str)),
            "attributes": {
                "source": "fsxn-ontap",
                "service": "ontap-audit",
                "s3_key": source_key,
                "event_type": log.get("EventID", log.get("event_type", "unknown")),
                "svm": log.get("SVMName", log.get("svm", "")),
                "user": log.get("UserName", log.get("user", "")),
                "client_ip": log.get("ClientIP", log.get("client_ip", "")),
                "operation": log.get("Operation", log.get("operation", "")),
                "path": log.get("ObjectName", log.get("path", "")),
                "result": log.get("Result", log.get("result", "")),
            },
        }
        ts = log.get("timestamp", log.get("Timestamp"))
        if ts:
            # New Relic expects Unix epoch in milliseconds
            if isinstance(ts, str):
                try:
                    from datetime import datetime, timezone
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    entry["timestamp"] = int(dt.timestamp() * 1000)
                except (ValueError, TypeError):
                    pass  # Skip invalid timestamps
            elif isinstance(ts, (int, float)):
                # If already numeric, assume seconds and convert to ms
                entry["timestamp"] = int(ts * 1000) if ts < 1e12 else int(ts)
        formatted.append(entry)
    return formatted


def _ship_to_new_relic(logs: list[dict[str, Any]], api_key: str) -> int:
    """Ship logs to New Relic in batches respecting 1MB limit."""
    if not logs:
        return 0

    shipped = 0
    batches = _create_batches(logs)

    for batch in batches:
        payload = [{"common": {"attributes": {"logtype": "fsxn-audit"}}, "logs": batch}]
        if _send_batch(payload, api_key):
            shipped += len(batch)
        else:
            logger.error("Failed to ship batch of %d logs", len(batch))

    return shipped


def _create_batches(logs: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Split logs into batches under 1MB compressed."""
    batches = []
    current: list[dict[str, Any]] = []
    current_size = 0

    for log in logs:
        log_size = len(json.dumps(log).encode("utf-8"))
        # Estimate compressed size as ~30% of raw
        if current_size + log_size > MAX_UNCOMPRESSED_BYTES * 0.8 or len(current) >= 2000:
            if current:
                batches.append(current)
            current = [log]
            current_size = log_size
        else:
            current.append(log)
            current_size += log_size

    if current:
        batches.append(current)
    return batches


def _send_batch(payload: list[dict[str, Any]], api_key: str) -> bool:
    """Send batch to New Relic with exponential backoff."""
    body = gzip.compress(json.dumps(payload).encode("utf-8"))

    headers = {
        "Content-Type": "application/gzip",
        "Api-Key": api_key,
    }

    for attempt in range(MAX_RETRIES):
        try:
            response = http.request(
                "POST", INTAKE_URL, body=body, headers=headers, timeout=30.0
            )
            if response.status == 202:
                return True
            if response.status == 429 or response.status >= 500:
                wait = 2 ** (attempt + 1)
                logger.warning("New Relic %d, retry in %ds", response.status, wait)
                time.sleep(wait)
                continue
            logger.error("New Relic error %d: %s", response.status,
                         response.data.decode("utf-8", errors="replace")[:300])
            return False
        except urllib3.exceptions.HTTPError as e:
            if attempt < MAX_RETRIES - 1:
                time.sleep(2 ** (attempt + 1))
            else:
                logger.error("HTTP error: %s", str(e))

    return False
