"""FSx for ONTAP audit log shipper for Sumo Logic (HTTP Source)."""

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


# Sumo Logic HTTP Source URL (contains auth token embedded)
API_KEY_SECRET_ARN = os.environ["API_KEY_SECRET_ARN"]
S3_ACCESS_POINT_ARN = os.environ["S3_ACCESS_POINT_ARN"]
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
SOURCE_CATEGORY = os.environ.get("SOURCE_CATEGORY", "aws/fsxn/audit")
SOURCE_NAME = os.environ.get("SOURCE_NAME", "fsxn-ontap-audit")

# Sumo Logic limit: 1MB per request
MAX_PAYLOAD_BYTES = 1 * 1024 * 1024
MAX_RETRIES = 3

logger = logging.getLogger()
logger.setLevel(getattr(logging, LOG_LEVEL))

secrets_client = boto3.client("secretsmanager")
s3_client = boto3.client("s3")
http = urllib3.PoolManager(num_pools=4, maxsize=10)
_endpoint_cache: Optional[str] = None


def get_http_source_url() -> str:
    """Retrieve Sumo Logic HTTP Source URL from Secrets Manager."""
    global _endpoint_cache
    if _endpoint_cache is None:
        resp = secrets_client.get_secret_value(SecretId=API_KEY_SECRET_ARN)
        secret = resp["SecretString"]
        try:
            parsed = json.loads(secret)
            _endpoint_cache = parsed.get("url", parsed.get("endpoint", secret))
        except (json.JSONDecodeError, AttributeError):
            _endpoint_cache = secret
    return _endpoint_cache


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    logger.info("Processing event")
    endpoint = get_http_source_url()
    records = _extract_s3_records(event)
    total_logs, total_shipped, errors = 0, 0, []

    for record in records:
        bucket, key = record["bucket"], record["key"]
        try:
            raw = s3_client.get_object(Bucket=S3_ACCESS_POINT_ARN, Key=key)["Body"].read()
            logs = _parse_logs(raw, key)
            total_logs += len(logs)
            shipped = _ship(logs, key, endpoint)
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


def _ship(logs: list[dict[str, Any]], source_key: str, endpoint: str) -> int:
    """Ship logs as newline-delimited JSON to Sumo Logic HTTP Source."""
    if not logs:
        return 0
    shipped = 0
    batch_lines: list[str] = []
    batch_size = 0

    for log in logs:
        log["_source_key"] = source_key
        line = json.dumps(log, default=str)
        line_size = len(line.encode()) + 1

        if batch_size + line_size > MAX_PAYLOAD_BYTES and batch_lines:
            if _send_batch(batch_lines, endpoint):
                shipped += len(batch_lines)
            batch_lines, batch_size = [], 0

        batch_lines.append(line)
        batch_size += line_size

    if batch_lines and _send_batch(batch_lines, endpoint):
        shipped += len(batch_lines)
    return shipped


def _send_batch(lines: list[str], endpoint: str) -> bool:
    """Send batch to Sumo Logic HTTP Source."""
    body = "\n".join(lines)
    headers = {
        "Content-Type": "application/json",
        "X-Sumo-Category": SOURCE_CATEGORY,
        "X-Sumo-Name": SOURCE_NAME,
        "X-Sumo-Host": "fsxn-ontap",
    }

    for attempt in range(MAX_RETRIES):
        try:
            resp = http.request("POST", endpoint, body=body.encode("utf-8"),
                                headers=headers, timeout=30.0)
            if resp.status == 200:
                return True
            if resp.status == 429 or resp.status >= 500:
                time.sleep(2 ** (attempt + 1))
                continue
            logger.error("Sumo Logic %d: %s", resp.status, resp.data.decode("utf-8", errors="replace")[:300])
            return False
        except urllib3.exceptions.HTTPError as e:
            if attempt < MAX_RETRIES - 1:
                time.sleep(2 ** (attempt + 1))
            else:
                logger.error("HTTP error: %s", str(e))
    return False
