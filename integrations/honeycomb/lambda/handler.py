"""FSx for ONTAP audit log shipper for Honeycomb (Events API)."""

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


API_KEY_SECRET_ARN = os.environ["API_KEY_SECRET_ARN"]
S3_ACCESS_POINT_ARN = os.environ["S3_ACCESS_POINT_ARN"]
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
HONEYCOMB_DATASET = os.environ.get("HONEYCOMB_DATASET", "fsxn-audit")
HONEYCOMB_API_URL = os.environ.get("HONEYCOMB_API_URL", "https://api.honeycomb.io")

# Honeycomb limit: 5MB per batch request body
# Implementation choice: batch in chunks of 100 events for reliability
MAX_BATCH_EVENTS = 100
MAX_RETRIES = 3

logger = logging.getLogger()
logger.setLevel(getattr(logging, LOG_LEVEL))

secrets_client = boto3.client("secretsmanager")
s3_client = boto3.client("s3")
http = urllib3.PoolManager(num_pools=4, maxsize=10)
_api_key_cache: Optional[str] = None


def get_api_key() -> str:
    global _api_key_cache
    if _api_key_cache is None:
        resp = secrets_client.get_secret_value(SecretId=API_KEY_SECRET_ARN)
        secret = resp["SecretString"]
        try:
            parsed = json.loads(secret)
            _api_key_cache = parsed.get("api_key", parsed.get("team_key", secret))
        except (json.JSONDecodeError, AttributeError):
            _api_key_cache = secret
    return _api_key_cache


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    logger.info("Processing event")
    api_key = get_api_key()
    records = _extract_s3_records(event)
    total_logs, total_shipped, errors = 0, 0, []

    for record in records:
        bucket, key = record["bucket"], record["key"]
        try:
            raw = s3_client.get_object(Bucket=S3_ACCESS_POINT_ARN, Key=key)["Body"].read()
            logs = _parse_logs(raw, key)
            total_logs += len(logs)
            shipped = _ship(logs, key, api_key)
            total_shipped += shipped
        except Exception as e:
            logger.error("Failed %s/%s: %s", bucket, key, str(e))
            errors.append({"bucket": bucket, "key": key, "error": str(e)})

    return {"statusCode": 200 if not errors else 207,
            "body": {"total_logs": total_logs, "total_shipped": total_shipped, "errors": errors}}


def _extract_s3_records(event):
    records = []
    if "Records" in event:
        for r in event["Records"]:
            s3 = r.get("s3", {})
            records.append({"bucket": s3.get("bucket", {}).get("name", ""), "key": s3.get("object", {}).get("key", "")})
    elif "detail" in event:
        d = event["detail"]
        records.append({"bucket": d.get("bucket", {}).get("name", ""), "key": d.get("object", {}).get("key", "")})
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


def _ship(logs: list[dict[str, Any]], source_key: str, api_key: str) -> int:
    """Ship logs to Honeycomb Events API in batches of 100."""
    if not logs:
        return 0
    shipped = 0

    for i in range(0, len(logs), MAX_BATCH_EVENTS):
        batch = logs[i : i + MAX_BATCH_EVENTS]
        events = [_format_event(log, source_key) for log in batch]
        if _send_batch(events, api_key):
            shipped += len(events)

    return shipped


def _format_event(log: dict[str, Any], source_key: str) -> dict[str, Any]:
    """Format a single event for Honeycomb batch API."""
    event: dict[str, Any] = {
        "data": {
            "source": "fsxn-ontap",
            "service": "ontap-audit",
            "event_type": log.get("EventID", log.get("event_type", "unknown")),
            "svm": log.get("SVMName", log.get("svm", "")),
            "user": log.get("UserName", log.get("user", "")),
            "client_ip": log.get("ClientIP", log.get("client_ip", "")),
            "operation": log.get("Operation", log.get("operation", "")),
            "path": log.get("ObjectName", log.get("path", "")),
            "result": log.get("Result", log.get("result", "")),
            "s3_key": source_key,
        }
    }
    ts = log.get("timestamp", log.get("Timestamp"))
    if ts:
        event["time"] = ts
    return event


def _send_batch(events: list[dict[str, Any]], api_key: str) -> bool:
    """Send batch to Honeycomb /1/batch/<dataset> endpoint."""
    body = json.dumps(events).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-Honeycomb-Team": api_key,
    }
    url = f"{HONEYCOMB_API_URL.rstrip('/')}/1/batch/{HONEYCOMB_DATASET}"

    for attempt in range(MAX_RETRIES):
        try:
            resp = http.request("POST", url, body=body, headers=headers, timeout=30.0)
            if resp.status == 200:
                return True
            if resp.status == 429 or resp.status >= 500:
                time.sleep(2 ** (attempt + 1))
                continue
            logger.error("Honeycomb %d: %s", resp.status, resp.data.decode("utf-8", errors="replace")[:300])
            return False
        except urllib3.exceptions.HTTPError as e:
            if attempt < MAX_RETRIES - 1:
                time.sleep(2 ** (attempt + 1))
            else:
                logger.error("HTTP error: %s", str(e))
    return False
