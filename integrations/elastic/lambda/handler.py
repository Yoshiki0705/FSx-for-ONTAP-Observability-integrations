"""FSx for ONTAP audit log shipper for Elastic (Elasticsearch Bulk API).

Ships audit logs to Elasticsearch or Elastic Cloud using the _bulk API.
"""

import gzip
import json
import logging
import os
import time
from datetime import datetime, timezone
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
ELASTIC_ENDPOINT = os.environ.get("ELASTIC_ENDPOINT", "")
API_KEY_SECRET_ARN = os.environ["API_KEY_SECRET_ARN"]
S3_ACCESS_POINT_ARN = os.environ["S3_ACCESS_POINT_ARN"]
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
INDEX_PREFIX = os.environ.get("INDEX_PREFIX", "fsxn-audit")

# Recommended ~10MB per bulk request; stay conservative
MAX_BULK_BYTES = 5 * 1024 * 1024
MAX_RETRIES = 3

logger = logging.getLogger()
logger.setLevel(getattr(logging, LOG_LEVEL))

secrets_client = boto3.client("secretsmanager")
s3_client = boto3.client("s3")
http = urllib3.PoolManager(num_pools=4, maxsize=10)

_api_key_cache: Optional[str] = None


def get_api_key() -> str:
    """Retrieve Elasticsearch API key from Secrets Manager."""
    global _api_key_cache
    if _api_key_cache is None:
        response = secrets_client.get_secret_value(SecretId=API_KEY_SECRET_ARN)
        secret = response["SecretString"]
        try:
            parsed = json.loads(secret)
            _api_key_cache = parsed.get("api_key", parsed.get("encoded", secret))
        except (json.JSONDecodeError, AttributeError):
            _api_key_cache = secret
    return _api_key_cache


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda handler for Elasticsearch bulk indexing."""
    logger.info("Processing event")
    api_key = get_api_key()
    records = _extract_s3_records(event)

    total_logs = 0
    total_shipped = 0
    errors = []

    for record in records:
        bucket, key = record["bucket"], record["key"]
        try:
            data = s3_client.get_object(Bucket=S3_ACCESS_POINT_ARN, Key=key)
            raw = data["Body"].read()
            logs = _parse_logs(raw, key)
            total_logs += len(logs)
            shipped = _bulk_index(logs, key, api_key)
            total_shipped += shipped
        except Exception as e:
            logger.error("Failed %s/%s: %s", bucket, key, str(e))
            errors.append({"bucket": bucket, "key": key, "error": str(e)})

    return {"statusCode": 200 if not errors else 207,
            "body": {"total_logs": total_logs, "total_shipped": total_shipped, "errors": errors}}


def _extract_s3_records(event: dict[str, Any]) -> list[dict[str, str]]:
    records = []
    if "Records" in event:
        for r in event["Records"]:
            s3 = r.get("s3", {})
            records.append({"bucket": s3.get("bucket", {}).get("name", ""),
                            "key": s3.get("object", {}).get("key", "")})
    elif "detail" in event:
        d = event["detail"]
        records.append({"bucket": d.get("bucket", {}).get("name", ""),
                        "key": d.get("object", {}).get("key", "")})
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


def _bulk_index(logs: list[dict[str, Any]], source_key: str, api_key: str) -> int:
    """Index logs using Elasticsearch Bulk API."""
    if not logs:
        return 0

    shipped = 0
    bulk_lines: list[str] = []
    bulk_size = 0

    today = datetime.now(timezone.utc).strftime("%Y.%m.%d")
    index_name = f"{INDEX_PREFIX}-{today}"

    for log in logs:
        doc = _format_document(log, source_key)
        action = json.dumps({"index": {"_index": index_name}})
        doc_str = json.dumps(doc)
        entry_size = len(action.encode()) + len(doc_str.encode()) + 2  # newlines

        if bulk_size + entry_size > MAX_BULK_BYTES and bulk_lines:
            if _send_bulk(bulk_lines, api_key):
                shipped += len(bulk_lines) // 2
            bulk_lines = []
            bulk_size = 0

        bulk_lines.append(action)
        bulk_lines.append(doc_str)
        bulk_size += entry_size

    if bulk_lines:
        if _send_bulk(bulk_lines, api_key):
            shipped += len(bulk_lines) // 2

    return shipped


def _format_document(log: dict[str, Any], source_key: str) -> dict[str, Any]:
    """Format a single document for Elasticsearch."""
    timestamp = log.get("timestamp", log.get("Timestamp", datetime.now(timezone.utc).isoformat()))
    return {
        "@timestamp": timestamp,
        "event": {
            "code": log.get("EventID", log.get("event_type", "unknown")),
            "type": ["access"],
            "category": ["file"],
        },
        "user": {"name": log.get("UserName", log.get("user", ""))},
        "source": {"ip": log.get("ClientIP", log.get("client_ip", ""))},
        "fsxn": {
            "operation": log.get("Operation", log.get("operation", "")),
            "path": log.get("ObjectName", log.get("path", "")),
            "result": log.get("Result", log.get("result", "")),
            "svm": log.get("SVMName", log.get("svm", "")),
        },
        "cloud": {"provider": "aws", "service": {"name": "fsx-ontap"}},
        "labels": {"source": "fsxn-ontap", "s3_key": source_key},
        "message": json.dumps(log, default=str),
    }


def _send_bulk(lines: list[str], api_key: str) -> bool:
    """Send bulk request with retry."""
    # Bulk API requires newline-terminated NDJSON
    body = "\n".join(lines) + "\n"

    headers = {
        "Content-Type": "application/x-ndjson",
        "Authorization": f"ApiKey {api_key}",
    }

    url = f"{ELASTIC_ENDPOINT.rstrip('/')}/_bulk"

    for attempt in range(MAX_RETRIES):
        try:
            response = http.request(
                "POST", url, body=body.encode("utf-8"), headers=headers, timeout=30.0
            )
            if response.status == 200:
                resp_body = json.loads(response.data.decode("utf-8"))
                if not resp_body.get("errors", False):
                    return True
                # Partial failure - log but consider success
                logger.warning("Bulk had partial errors")
                return True
            if response.status == 429 or response.status >= 500:
                time.sleep(2 ** (attempt + 1))
                continue
            logger.error("Elastic error %d: %s", response.status,
                         response.data.decode("utf-8", errors="replace")[:300])
            return False
        except urllib3.exceptions.HTTPError as e:
            if attempt < MAX_RETRIES - 1:
                time.sleep(2 ** (attempt + 1))
            else:
                logger.error("HTTP error: %s", str(e))
    return False
