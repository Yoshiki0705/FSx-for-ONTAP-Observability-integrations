"""FSx for ONTAP audit log shipper for Grafana Cloud.

Ships audit logs to Grafana Cloud via either:
- OTLP Gateway (/otlp/v1/logs) — preferred, uses standard OTLP format
- Loki Push API (/loki/api/v1/push) — fallback

Trigger modes:
- EventBridge Scheduler (polling): Lists new files via S3 Access Point,
  tracks progress with SSM Parameter Store checkpoint.
- S3 event (manual/testing): Accepts S3 event format for direct invocation.

Authentication via Basic Auth (Instance ID + API Key).
The endpoint mode is auto-detected from the LOKI_ENDPOINT environment variable:
- If URL contains 'otlp-gateway', uses OTLP format
- Otherwise, uses Loki Push API format
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import time
from base64 import b64encode
from datetime import datetime
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
LOKI_ENDPOINT = os.environ.get("LOKI_ENDPOINT", "")
API_KEY_SECRET_ARN = os.environ.get("API_KEY_SECRET_ARN", "")
S3_ACCESS_POINT_ARN = os.environ.get("S3_ACCESS_POINT_ARN", "")
S3_KEY_PREFIX = os.environ.get("S3_KEY_PREFIX", "")
# The checkpoint is a RECORD TIMESTAMP (epoch nanoseconds), not a last-processed
# key. ONTAP appends to a single active audit file whose key is fixed and sorts
# AFTER every rotated file's key, so a key high-water mark passed to
# ListObjectsV2 StartAfter stops returning newly rotated files permanently once
# it reaches the active file (ROADMAP L80-L86). amplify-portal established the
# timestamp approach that this mirrors.
CHECKPOINT_PARAM_NAME = os.environ.get("CHECKPOINT_PARAM_NAME", "")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
LOKI_TENANT_ID = os.environ.get("LOKI_TENANT_ID", "")

# How far back of a file's modification time to still consider, covering the gap
# between a record being written and the object's LastModified settling. Mirrors
# amplify-portal's GRACE_SECONDS.
GRACE_SECONDS = int(os.environ.get("CHECKPOINT_GRACE_SECONDS", "300"))

# Auto-detect endpoint mode
def _is_otlp_endpoint(endpoint: str) -> bool:
    """Detect whether the endpoint is OTLP-compatible.

    Supports Grafana Cloud OTLP Gateway, self-hosted Loki OTLP endpoint,
    and any endpoint with /v1/logs path.
    """
    ep = endpoint.rstrip("/")
    return (
        "otlp-gateway" in ep
        or ep.endswith("/otlp")
        or ep.endswith("/otlp/v1/logs")
        or ep.endswith("/v1/logs")
    )


USE_OTLP = _is_otlp_endpoint(LOKI_ENDPOINT)

# Loki recommended max ~4MB per push request
MAX_BATCH_BYTES = 3 * 1024 * 1024  # 3MB to stay safe
MAX_RETRIES = 3

logger = logging.getLogger()
logger.setLevel(getattr(logging, LOG_LEVEL))

secrets_client = boto3.client("secretsmanager")
s3_client = boto3.client("s3")
ssm_client = boto3.client("ssm")
http = urllib3.PoolManager(num_pools=4, maxsize=10)

_auth_header_cache: str | None = None


def get_auth_header() -> str:
    """Retrieve Grafana Cloud credentials and build Basic Auth header."""
    global _auth_header_cache
    if _auth_header_cache is None:
        response = secrets_client.get_secret_value(SecretId=API_KEY_SECRET_ARN)
        secret = response["SecretString"]
        try:
            parsed = json.loads(secret)
            instance_id = parsed.get("instance_id", parsed.get("user", ""))
            api_key = parsed.get("api_key", parsed.get("password", ""))
            credentials = f"{instance_id}:{api_key}"
        except (json.JSONDecodeError, AttributeError):
            credentials = secret  # Assume pre-formatted "user:pass"
        _auth_header_cache = f"Basic {b64encode(credentials.encode()).decode()}"
    return _auth_header_cache


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda handler for Grafana Loki log shipping.

    Supports two invocation modes:
    - Scheduler polling: event.get("source") == "scheduler"
    - S3 event (manual/testing): event contains "Records" or "detail"
    """
    logger.info("Processing event: source=%s", event.get("source", "s3-event"))

    auth_header = get_auth_header()

    # Determine invocation mode
    if event.get("source") == "scheduler":
        return _handle_scheduler_event(event, auth_header, context)
    else:
        return _handle_s3_event(event, auth_header)


def _handle_scheduler_event(
    event: dict[str, Any], auth_header: str, context: Any = None
) -> dict[str, Any]:
    """Handle EventBridge Scheduler invocation (polling mode).

    Lists audit files by modification time, re-reads any file that could hold a
    record newer than the timestamp watermark (including the active file, whose
    key never changes), ships only records strictly newer than the watermark,
    and advances the watermark to the newest record it shipped.

    This replaces a key high-water mark: ONTAP's active audit file has a fixed
    key that sorts after every rotated file, so a key passed to ``StartAfter``
    stops listing rotated files for good once it reaches the active one
    (ROADMAP L80-L86). The timestamp watermark cannot stall that way. See the
    module-level note on ``CHECKPOINT_PARAM_NAME`` and integrations/amplify-portal.
    """
    s3_ap_arn = event.get("s3_access_point_arn", S3_ACCESS_POINT_ARN)
    prefix = event.get("prefix", S3_KEY_PREFIX)

    watermark_ns = _get_checkpoint()
    logger.info(
        "Scheduler mode: prefix=%s, watermark_ns=%s", prefix, watermark_ns or "(none)"
    )

    candidate_keys = _list_candidate_keys(s3_ap_arn, prefix, watermark_ns)

    if not candidate_keys:
        logger.info("No candidate files to process")
        return {
            "statusCode": 200,
            "body": {"total_logs": 0, "total_shipped": 0, "new_files": 0},
        }

    # Bound work per invocation
    max_keys = int(os.environ.get("MAX_KEYS_PER_RUN", "100"))
    if len(candidate_keys) > max_keys:
        logger.warning("Capping to %d of %d keys", max_keys, len(candidate_keys))
        candidate_keys = candidate_keys[:max_keys]

    logger.info("Found %d candidate files to process", len(candidate_keys))

    total_logs = 0
    total_shipped = 0
    errors = []
    highest_ns = watermark_ns

    safety_threshold_ms = int(os.environ.get("SAFETY_THRESHOLD_MS", "30000"))

    for idx, key in enumerate(candidate_keys):
        # Stop early if Lambda is about to timeout
        if context and hasattr(context, "get_remaining_time_in_millis"):
            if context.get_remaining_time_in_millis() < safety_threshold_ms:
                logger.warning("Stopping early: %dms remaining, %d files left",
                    context.get_remaining_time_in_millis(), len(candidate_keys) - idx)
                break

        try:
            data = s3_client.get_object(Bucket=s3_ap_arn, Key=key)
            raw = data["Body"].read()

            logs = _parse_logs(raw, key)

            # Keep only records strictly newer than the watermark. The active
            # file is re-read every run, so already-shipped records are filtered
            # out here rather than by not reading the file.
            fresh_logs, file_max_ns = _filter_new_records(logs, watermark_ns)
            total_logs += len(fresh_logs)

            shipped = _ship_logs(fresh_logs, key, auth_header)
            if len(fresh_logs) > 0 and shipped == 0:
                # Delivery failed — fresh records existed but none were shipped
                raise RuntimeError(
                    f"Grafana delivery failed for {key} ({len(fresh_logs)} logs)"
                )
            # Note: files with 0 fresh records are treated as successfully
            # processed and do not move the watermark (not a delivery failure).
            total_shipped += shipped

            # Advance only after the file's fresh records were shipped.
            if file_max_ns > highest_ns:
                highest_ns = file_max_ns

        except Exception as e:
            logger.error("Failed to process %s: %s", key, str(e))
            errors.append({"key": key, "error": str(e)})
            # Stop processing on error to avoid gaps in the watermark
            break

    # Advance the watermark to the newest record shipped this run
    if highest_ns > watermark_ns:
        _set_checkpoint(highest_ns)

    return {
        "statusCode": 200 if not errors else 207,
        "body": {
            "total_logs": total_logs,
            "total_shipped": total_shipped,
            "new_files": len(candidate_keys),
            "processed_files": len(candidate_keys) - len(errors),
            "errors": errors,
        },
    }


def _handle_s3_event(
    event: dict[str, Any], auth_header: str
) -> dict[str, Any]:
    """Handle S3 event format (for manual testing via aws lambda invoke)."""
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

            shipped = _ship_logs(logs, key, auth_header)
            total_shipped += shipped

        except Exception as e:
            logger.error("Failed to process %s/%s: %s", bucket, key, str(e))
            errors.append({"bucket": bucket, "key": key, "error": str(e)})

    return {
        "statusCode": 200 if not errors else 207,
        "body": {"total_logs": total_logs, "total_shipped": total_shipped, "errors": errors},
    }


# --- Checkpoint management (SSM Parameter Store) ---


def _get_checkpoint() -> int:
    """Retrieve the audit-record timestamp watermark (epoch nanoseconds).

    Returns 0 — meaning "start from the beginning of the prefix" — when no
    checkpoint exists yet, when the parameter still holds the ``__INIT__``
    sentinel written at stack creation, when it holds a legacy key-shaped value
    from the previous key-based scheme, or when the lookup fails. Treating a
    legacy key as a cold start is what makes the upgrade non-crashing.
    """
    param_name = os.environ.get("CHECKPOINT_PARAM_NAME", "")
    if not param_name:
        return 0
    try:
        response = ssm_client.get_parameter(Name=param_name)
        value = response["Parameter"]["Value"]
    except ClientError as e:
        if e.response["Error"]["Code"] == "ParameterNotFound":
            return 0
        logger.warning("Failed to get checkpoint: %s", str(e))
        return 0
    except Exception as e:
        logger.warning("Failed to get checkpoint: %s", str(e))
        return 0

    value = (value or "").strip()
    if not value or value == "__INIT__" or not value.isdigit():
        # Empty, the init sentinel, or a leftover key from the previous scheme.
        logger.info(
            "Checkpoint %r is not a timestamp watermark; starting from the "
            "beginning of the prefix", value
        )
        return 0
    return int(value)


def _set_checkpoint(watermark_ns: int) -> None:
    """Persist the audit-record timestamp watermark to SSM Parameter Store."""
    param_name = os.environ.get("CHECKPOINT_PARAM_NAME", "")
    if not param_name or watermark_ns <= 0:
        return
    try:
        ssm_client.put_parameter(
            Name=param_name,
            Value=str(watermark_ns),
            Type="String",
            Overwrite=True,
        )
        logger.info("Checkpoint updated: %s", watermark_ns)
    except Exception as e:
        logger.error("Failed to update checkpoint: %s", str(e))


# --- Audit-record timestamp parsing ---


def _audit_timestamp_ns(log: dict[str, Any]) -> int | None:
    """Parse a parsed audit record's timestamp into epoch nanoseconds.

    Nanoseconds rather than milliseconds because this value is the dedup
    watermark, not just a display field: rounding would make records written in
    the same millisecond indistinguishable. The fractional digits are read
    directly rather than through ``datetime.fromisoformat`` (microseconds max).
    Mirrors amplify-portal's ``_audit_timestamp_ns``.
    """
    import re
    from datetime import timezone

    raw = str(log.get("timestamp", log.get("Timestamp", ""))).strip()
    if not raw:
        return None

    fraction_ns = 0
    text = raw
    match = re.search(r"\.(\d+)", text)
    if match:
        digits = match.group(1)
        fraction_ns = int(digits.ljust(9, "0")[:9])
        text = text[: match.start()] + text[match.end() :]

    text = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        logger.warning("Unparseable audit timestamp: %r", raw)
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp()) * 1_000_000_000 + fraction_ns


def _filter_new_records(
    logs: list[dict[str, Any]], watermark_ns: int
) -> tuple[list[dict[str, Any]], int]:
    """Keep records strictly newer than the watermark; report the newest kept.

    An undated record is kept rather than dropped (at-least-once) and does not
    move the watermark. Returns the filtered records and the maximum record
    timestamp (ns) among the kept dated records, or ``watermark_ns`` if none.
    """
    fresh: list[dict[str, Any]] = []
    file_max_ns = watermark_ns
    for log in logs:
        record_ns = _audit_timestamp_ns(log)
        if record_ns is None:
            fresh.append(log)
            continue
        if watermark_ns and record_ns <= watermark_ns:
            continue
        fresh.append(log)
        if record_ns > file_max_ns:
            file_max_ns = record_ns
    return fresh, file_max_ns


# --- S3 listing and filtering ---


def _list_candidate_keys(
    s3_ap_arn: str, prefix: str, watermark_ns: int
) -> list[str]:
    """List audit files that may hold records newer than the watermark.

    Selection is by object modification time, never by a key high-water mark:
    ONTAP's active audit file has a fixed key that sorts after every rotated
    file, so ``StartAfter`` on a key would skip rotated files for good once the
    checkpoint reached the active one (ROADMAP L80-L86). A file whose last
    modification precedes the watermark by more than ``GRACE_SECONDS`` cannot
    hold a newer record and is skipped. Returns keys sorted oldest-modified
    first so a mid-run failure leaves the watermark behind, not ahead. Mirrors
    amplify-portal's ``_list_candidate_audit_keys``.
    """
    from datetime import timedelta, timezone

    watermark_dt = (
        datetime.fromtimestamp(watermark_ns / 1_000_000_000, tz=timezone.utc)
        if watermark_ns
        else None
    )

    candidates: list[tuple[datetime, str]] = []
    continuation_token = None

    params: dict[str, Any] = {
        "Bucket": s3_ap_arn,
        "Prefix": prefix,
        "MaxKeys": 1000,
    }

    while True:
        if continuation_token:
            params["ContinuationToken"] = continuation_token

        response = s3_client.list_objects_v2(**params)

        for obj in response.get("Contents", []):
            key = obj["Key"]
            # Skip directory markers
            if key.endswith("/"):
                continue
            modified = obj.get("LastModified")
            if watermark_dt and modified is not None:
                if modified < watermark_dt - timedelta(seconds=GRACE_SECONDS):
                    continue
            candidates.append(
                (modified or datetime.min.replace(tzinfo=timezone.utc), key)
            )

        if response.get("IsTruncated"):
            continuation_token = response.get("NextContinuationToken")
        else:
            break

    # Oldest-modified first for deterministic, chronological processing.
    candidates.sort()
    return [key for _, key in candidates]


# --- S3 event extraction (backward compatibility) ---


def _extract_s3_records(event: dict[str, Any]) -> list[dict[str, str]]:
    """Extract S3 records from S3 event or EventBridge event format."""
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


# --- Log parsing ---


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


# --- Shipping (primary entry point) ---


def _ship_logs(logs: list[dict], source_key: str, auth_header: str) -> int:
    """Ship logs to Grafana Cloud (OTLP primary, Loki Push fallback).

    The canonical internal model is normalized records (list of dicts).
    OTLP payload is generated directly from normalized records (primary path).
    Loki Push format is generated only for the fallback path.
    """
    if not logs:
        return 0

    if USE_OTLP:
        payload = _format_for_otlp_direct(logs, source_key)
        if _send_otlp(payload, auth_header):
            return len(logs)
    else:
        loki_payload = _format_for_loki(logs, source_key)
        if _send_loki_push(loki_payload, auth_header):
            return len(logs)
    return 0


# --- OTLP formatting (primary path) ---


def _format_for_otlp_direct(
    logs: list[dict[str, Any]], source_key: str
) -> dict[str, Any]:
    """Build OTLP Log payload directly from normalized records.

    Groups logs by SVM and builds resource/scope/log structure per the
    OpenTelemetry Log Data Model specification.
    """
    # Group by SVM
    logs_by_svm: dict[str, list[dict[str, Any]]] = {}
    for log in logs:
        svm = log.get("SVMName", log.get("svm", "unknown"))
        if svm not in logs_by_svm:
            logs_by_svm[svm] = []
        logs_by_svm[svm].append(log)

    resource_logs = []
    for svm, svm_logs in logs_by_svm.items():
        # Resource attributes identify the source
        resource_attrs = [
            {"key": "service.name", "value": {"stringValue": "fsxn-audit"}},
            {"key": "source", "value": {"stringValue": "fsxn-ontap"}},
            {"key": "svm", "value": {"stringValue": svm}},
            {"key": "s3_key", "value": {"stringValue": source_key}},
        ]

        # Build log records directly from parsed log dicts
        log_records = []
        for log in svm_logs:
            timestamp = log.get("timestamp", log.get("Timestamp", ""))
            ts_ns = _iso_to_ns(timestamp) if timestamp else str(int(time.time() * 1e9))

            # Build per-log attributes from log fields
            log_attrs = []
            for k, v in log.items():
                if k in ("timestamp", "Timestamp"):
                    continue
                log_attrs.append(
                    {"key": k, "value": {"stringValue": str(v)}}
                )

            log_records.append({
                "timeUnixNano": ts_ns,
                "body": {"stringValue": json.dumps(log, default=str)},
                "attributes": log_attrs,
            })

        resource_logs.append({
            "resource": {"attributes": resource_attrs},
            "scopeLogs": [{"logRecords": log_records}],
        })

    return {"resourceLogs": resource_logs}


# --- Loki formatting (fallback path) ---


def _format_for_loki(
    logs: list[dict[str, Any]], source_key: str
) -> dict[str, Any]:
    """Format logs for Loki Push API (fallback mode only).

    Loki expects streams with labels and arrays of [timestamp_ns, log_line] values.
    """
    # Group by SVM for separate streams
    streams_by_svm: dict[str, list[list[str]]] = {}

    for log in logs:
        svm = log.get("SVMName", log.get("svm", "unknown"))
        timestamp = log.get("timestamp", log.get("Timestamp", ""))
        ts_ns = _iso_to_ns(timestamp) if timestamp else str(int(time.time() * 1e9))

        log_line = json.dumps(log, default=str)

        if svm not in streams_by_svm:
            streams_by_svm[svm] = []
        streams_by_svm[svm].append([ts_ns, log_line])

    # Build Loki push payload
    streams = []
    for svm, values in streams_by_svm.items():
        # Sort by timestamp (Loki requires ordered entries)
        values.sort(key=lambda x: x[0])
        streams.append({
            "stream": {
                "job": "fsxn-audit",
                "source": "fsxn-ontap",
                "svm": svm,
                "s3_key": source_key,
            },
            "values": values,
        })

    return {"streams": streams}


# --- Sending ---


def _normalize_otlp_url(endpoint: str) -> str:
    """Normalize OTLP endpoint URL to ensure it ends with /v1/logs.

    Handles both cases:
    - https://otlp-gateway-prod-<region>.grafana.net/otlp → appends /v1/logs
    - https://otlp-gateway-prod-<region>.grafana.net/otlp/v1/logs → returns as-is
    """
    endpoint = endpoint.rstrip("/")
    if endpoint.endswith("/v1/logs"):
        return endpoint
    if endpoint.endswith("/otlp"):
        return f"{endpoint}/v1/logs"
    return f"{endpoint}/v1/logs"


def _send_otlp(payload: dict[str, Any], auth_header: str) -> bool:
    """Send logs via OTLP HTTP endpoint with retry."""
    body = json.dumps(payload).encode("utf-8")

    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "Authorization": auth_header,
    }

    url = _normalize_otlp_url(LOKI_ENDPOINT)

    for attempt in range(MAX_RETRIES):
        try:
            response = http.request(
                "POST", url, body=body, headers=headers, timeout=30.0
            )
            if response.status in (200, 204):
                logger.info("OTLP push success: %d", response.status)
                return True
            if response.status == 429 or response.status >= 500:
                logger.warning("OTLP retry %d: HTTP %d", attempt + 1, response.status)
                time.sleep(2 ** (attempt + 1))
                continue
            logger.error("OTLP error %d: %s", response.status,
                         response.data.decode("utf-8", errors="replace")[:300])
            return False
        except urllib3.exceptions.HTTPError as e:
            if attempt < MAX_RETRIES - 1:
                time.sleep(2 ** (attempt + 1))
            else:
                logger.error("OTLP HTTP error: %s", str(e))

    return False


def _send_loki_push(payload: dict[str, Any], auth_header: str) -> bool:
    """Send push request to Loki with retry."""
    body = gzip.compress(json.dumps(payload).encode("utf-8"))

    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "Content-Encoding": "gzip",
        "Authorization": auth_header,
    }
    if LOKI_TENANT_ID:
        headers["X-Scope-OrgID"] = LOKI_TENANT_ID

    url = f"{LOKI_ENDPOINT.rstrip('/')}/loki/api/v1/push"

    for attempt in range(MAX_RETRIES):
        try:
            response = http.request(
                "POST", url, body=body, headers=headers, timeout=30.0
            )
            if response.status == 204 or response.status == 200:
                return True
            if response.status == 429 or response.status >= 500:
                time.sleep(2 ** (attempt + 1))
                continue
            logger.error("Loki error %d: %s", response.status,
                         response.data.decode("utf-8", errors="replace")[:300])
            return False
        except urllib3.exceptions.HTTPError as e:
            if attempt < MAX_RETRIES - 1:
                time.sleep(2 ** (attempt + 1))
            else:
                logger.error("HTTP error: %s", str(e))

    return False


# --- Utilities ---


def _iso_to_ns(timestamp: str) -> str:
    """Convert ISO timestamp to nanosecond string for Loki."""
    try:
        if timestamp.endswith("Z"):
            timestamp = timestamp[:-1] + "+00:00"
        dt = datetime.fromisoformat(timestamp)
        return str(int(dt.timestamp() * 1e9))
    except (ValueError, AttributeError):
        return str(int(time.time() * 1e9))
