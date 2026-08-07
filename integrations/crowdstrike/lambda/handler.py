"""FSx for ONTAP audit log shipper for CrowdStrike Falcon LogScale.

Ships audit logs to LogScale via HEC (HTTP Event Collector) compatible endpoint.
LogScale supports Splunk HEC format natively at /api/v1/ingest/hec.

Reference: https://library.humio.com/logscale-api/log-shippers-hec.html
"""

import gzip
import json
import logging
import os
import random
import time
from datetime import datetime, timezone
from typing import Any

import boto3
import urllib3
from botocore.exceptions import ClientError

# ─── ONTAP audit log parsing ────────────────────────────────────────────────
# The shared parser is the single tested implementation of ONTAP's audit
# formats. It handles namespaced Windows Event Log XML, EVTX detection, gzip,
# unsuffixed rotations and single-line documents (where a declaration and the
# content share one line — the local parsers below mishandled that case).
# Imported defensively so a packaging miss degrades to the local parser instead
# of breaking the function at import time.
try:
    from ontap_audit_parser import parse_audit_log as _parse_ontap_audit_log
except ImportError:  # pragma: no cover - packaging fallback
    _parse_ontap_audit_log = None


# ─── Configuration ──────────────────────────────────────────────────────────

LOGSCALE_URL = os.environ.get("LOGSCALE_URL", "https://cloud.us.humio.com")
INGEST_TOKEN_SECRET_ARN = os.environ.get("INGEST_TOKEN_SECRET_ARN", "")
S3_ACCESS_POINT_ARN = os.environ.get("S3_ACCESS_POINT_ARN", "")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
SOURCE = os.environ.get("SOURCE", "fsxn-ontap")
SOURCETYPE = os.environ.get("SOURCETYPE", "fsxn:audit")
INDEX = os.environ.get("INDEX", "fsxn_audit")
HEC_PATH = os.environ.get("HEC_PATH", "/api/v1/ingest/hec")

# Key prefix to scan within the FSx for ONTAP S3 Access Point (e.g. "audit/").
AUDIT_LOG_PREFIX = os.environ.get("AUDIT_LOG_PREFIX", "")

# SSM Parameter Store name holding the last processed S3 key.
# FSx for ONTAP S3 APs do not emit S3 Event Notifications, so the poller tracks
# progress with a checkpoint instead of relying on event delivery.
CHECKPOINT_PARAM_NAME = os.environ.get("CHECKPOINT_PARAM_NAME", "")

# Upper bound on files handled per scheduled invocation.
MAX_KEYS_PER_RUN = int(os.environ.get("MAX_KEYS_PER_RUN", "100"))

# Stop starting new files when less than this much execution time remains, so
# the checkpoint is always written before the runtime kills the invocation.
SAFETY_THRESHOLD_MS = int(os.environ.get("SAFETY_THRESHOLD_MS", "30000"))

MAX_BATCH_SIZE_BYTES = 5 * 1024 * 1024
MAX_RETRIES = 3

logger = logging.getLogger()
logger.setLevel(getattr(logging, LOG_LEVEL))

secrets_client = boto3.client("secretsmanager")
s3_client = boto3.client("s3")
ssm_client = boto3.client("ssm")
http = urllib3.PoolManager(num_pools=4, maxsize=10, retries=urllib3.Retry(total=0))

_token_cache = None


def get_ingest_token() -> str:
    """Retrieve LogScale ingest token from Secrets Manager."""
    global _token_cache
    if _token_cache is None:
        response = secrets_client.get_secret_value(SecretId=INGEST_TOKEN_SECRET_ARN)
        secret = response["SecretString"]
        try:
            parsed = json.loads(secret)
            _token_cache = parsed.get("ingest_token", parsed.get("token", secret))
        except (json.JSONDecodeError, AttributeError):
            _token_cache = secret
    return _token_cache


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda handler for shipping audit logs to CrowdStrike LogScale.

    Supports two invocation modes:

    1. **Scheduler polling** (production path) — ``event["source"] == "scheduler"``.
       FSx for ONTAP S3 Access Points do not support S3 Event Notifications, so
       EventBridge Scheduler invokes this function periodically. New files are
       discovered with ``ListObjectsV2`` and progress is tracked in an SSM
       Parameter Store checkpoint.
    2. **S3 event payload** (manual testing / backward compatibility) — the event
       contains ``Records`` or ``detail``.
    """
    logger.info("Processing event: %s", json.dumps(event, default=str))

    token = get_ingest_token()

    if event.get("source") == "scheduler":
        keys = _list_new_keys(
            event.get("prefix", AUDIT_LOG_PREFIX), _get_checkpoint()
        )
        return _process_keys(keys, token, context, advance_checkpoint=True)

    records = _extract_s3_records(event)
    return _process_keys([r["key"] for r in records], token, context,
                         advance_checkpoint=False)


def _process_keys(
    keys: list[str],
    token: str,
    context: Any = None,
    advance_checkpoint: bool = False,
) -> dict[str, Any]:
    """Read, parse and ship the given S3 Access Point keys.

    Processing stops at the first failing key when checkpointing, so the
    checkpoint never advances past a file whose events were not delivered.
    """
    invocation_start = time.time()

    if advance_checkpoint and len(keys) > MAX_KEYS_PER_RUN:
        logger.warning(
            "Backlog of %d files exceeds MAX_KEYS_PER_RUN=%d; processing the "
            "oldest %d this run (remainder drains on the next schedule)",
            len(keys), MAX_KEYS_PER_RUN, MAX_KEYS_PER_RUN,
        )
        keys = keys[:MAX_KEYS_PER_RUN]

    records = [{"bucket": S3_ACCESS_POINT_ARN, "key": k} for k in keys]

    total_logs = 0
    total_shipped = 0
    files_scanned = len(records)
    files_processed = 0
    hec_success = 0
    hec_failure = 0
    max_log_file_age_seconds = 0.0
    errors: list[dict[str, str]] = []

    last_processed_key = _get_checkpoint() if advance_checkpoint else ""
    last_successful_key = last_processed_key

    for idx, record in enumerate(records):
        bucket = record["bucket"]
        key = record["key"]

        # Leave enough headroom to persist the checkpoint before the timeout.
        if advance_checkpoint and context is not None and hasattr(
            context, "get_remaining_time_in_millis"
        ):
            remaining_ms = context.get_remaining_time_in_millis()
            if remaining_ms < SAFETY_THRESHOLD_MS:
                logger.warning(
                    "Stopping early: %dms remaining, %d file(s) deferred to next run",
                    remaining_ms, len(records) - idx,
                )
                break

        logger.info("Processing: s3://%s/%s", bucket, key)

        try:
            s3_response = s3_client.get_object(Bucket=S3_ACCESS_POINT_ARN, Key=key)
            data = s3_response["Body"].read()
            # Track file age for SLO measurement
            last_modified = s3_response.get("LastModified")
            if last_modified:
                file_age = time.time() - last_modified.timestamp()
                max_log_file_age_seconds = max(max_log_file_age_seconds, file_age)

            logs = _parse_audit_logs(data, key)
            total_logs += len(logs)

            hec_events = _format_for_logscale(logs, key)
            shipped = _ship_to_logscale(hec_events, token)
            total_shipped += shipped
            files_processed += 1
            if logs and shipped == 0:
                # Events existed but none reached LogScale — do not checkpoint
                # past this file.
                hec_failure += 1
                raise RuntimeError(
                    f"LogScale delivery failed for {key} ({len(logs)} events)"
                )
            hec_success += 1
            last_successful_key = key
        except Exception as e:
            logger.error("Failed: %s/%s: %s", bucket, key, str(e))
            errors.append({"bucket": bucket, "key": key, "error": str(e)})
            hec_failure += 1
            if advance_checkpoint:
                # Stop here: advancing past a failed file would drop its events.
                break

    if advance_checkpoint and last_successful_key != last_processed_key:
        _set_checkpoint(last_successful_key)

    delivery_latency_ms = (time.time() - invocation_start) * 1000

    # Emit pipeline metrics using CloudWatch EMF (Embedded Metric Format)
    _emit_pipeline_metrics(
        files_scanned=files_scanned,
        files_processed=files_processed,
        events_parsed=total_logs,
        events_sent=total_shipped,
        hec_success=hec_success,
        hec_failure=hec_failure,
        delivery_latency_ms=delivery_latency_ms,
        log_file_age_seconds=max_log_file_age_seconds,
    )

    body: dict[str, Any] = {
        "total_logs": total_logs,
        "total_shipped": total_shipped,
        "errors": errors,
    }
    if advance_checkpoint:
        body["new_files"] = files_scanned
        body["processed_files"] = files_processed
        body["checkpoint"] = last_successful_key

    result = {"statusCode": 200 if not errors else 207, "body": body}
    logger.info("Complete: %s", json.dumps(result))
    return result


# ─── Checkpoint management (SSM Parameter Store) ─────────────────────────────


def _get_checkpoint() -> str:
    """Retrieve the last processed S3 key from SSM Parameter Store.

    Returns an empty string when no checkpoint exists yet, when the parameter
    still holds the ``__INIT__`` sentinel written at stack creation, or when the
    lookup fails — all of which mean "start from the beginning of the prefix".
    """
    if not CHECKPOINT_PARAM_NAME:
        logger.warning(
            "CHECKPOINT_PARAM_NAME is not set; every run will re-process the "
            "whole prefix and duplicate events in LogScale"
        )
        return ""
    try:
        value = ssm_client.get_parameter(Name=CHECKPOINT_PARAM_NAME)["Parameter"]["Value"]
        return "" if value == "__INIT__" else value
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "ParameterNotFound":
            logger.info("No checkpoint yet; starting from the beginning of the prefix")
            return ""
        logger.warning("Failed to read checkpoint: %s", str(e))
        return ""
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("Failed to read checkpoint: %s", str(e))
        return ""


def _set_checkpoint(key: str) -> None:
    """Persist the last successfully processed S3 key.

    A failure here is logged but not raised: the events were already delivered,
    so failing the invocation would only re-ship them.
    """
    if not CHECKPOINT_PARAM_NAME:
        return
    try:
        ssm_client.put_parameter(
            Name=CHECKPOINT_PARAM_NAME, Value=key, Type="String", Overwrite=True
        )
        logger.info("Checkpoint updated: %s", key)
    except Exception as e:
        logger.error(
            "Failed to update checkpoint to %s: %s — these files may be "
            "re-shipped on the next run", key, str(e),
        )


def _list_new_keys(prefix: str, last_processed_key: str) -> list[str]:
    """List S3 Access Point objects newer than the checkpoint.

    ``ListObjectsV2`` returns keys in lexicographic order, so audit logs written
    under a date-based prefix (``YYYY/MM/DD/``) are naturally chronological and
    ``StartAfter`` can skip everything already processed server-side.
    """
    all_keys: list[str] = []
    continuation_token: str | None = None

    params: dict[str, Any] = {"Bucket": S3_ACCESS_POINT_ARN, "MaxKeys": 1000}
    if prefix:
        params["Prefix"] = prefix
    if last_processed_key:
        params["StartAfter"] = last_processed_key

    logger.info(
        "Scheduler mode: prefix=%s, checkpoint=%s", prefix, last_processed_key or "(none)"
    )

    while True:
        if continuation_token:
            params["ContinuationToken"] = continuation_token
        response = s3_client.list_objects_v2(**params)
        for obj in response.get("Contents", []):
            key = obj["Key"]
            if key.endswith("/"):
                continue  # Skip directory markers
            all_keys.append(key)
        if response.get("IsTruncated"):
            continuation_token = response.get("NextContinuationToken")
        else:
            break

    all_keys.sort()
    logger.info("Found %d new audit log file(s) to process", len(all_keys))
    return all_keys


def _emit_pipeline_metrics(
    files_scanned: int,
    files_processed: int,
    events_parsed: int,
    events_sent: int,
    hec_success: int,
    hec_failure: int,
    delivery_latency_ms: float,
    log_file_age_seconds: float,
) -> None:
    """Emit pipeline health metrics using CloudWatch Embedded Metric Format.

    EMF allows custom metrics to be extracted from CloudWatch Logs without
    a separate PutMetricData API call, reducing cost and latency.
    """
    emf_payload = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [{
                "Namespace": "FSxONTAP/CrowdStrike/Pipeline",
                "Dimensions": [["FunctionName"]],
                "Metrics": [
                    {"Name": "FilesScanned", "Unit": "Count"},
                    {"Name": "FilesProcessed", "Unit": "Count"},
                    {"Name": "EventsParsed", "Unit": "Count"},
                    {"Name": "EventsSent", "Unit": "Count"},
                    {"Name": "HecSuccess", "Unit": "Count"},
                    {"Name": "HecFailure", "Unit": "Count"},
                    {"Name": "DeliveryLatencyMs", "Unit": "Milliseconds"},
                    {"Name": "LogFileAgeSeconds", "Unit": "Seconds"},
                ],
            }],
        },
        "FunctionName": os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "unknown"),
        "FilesScanned": files_scanned,
        "FilesProcessed": files_processed,
        "EventsParsed": events_parsed,
        "EventsSent": events_sent,
        "HecSuccess": hec_success,
        "HecFailure": hec_failure,
        "DeliveryLatencyMs": round(delivery_latency_ms, 2),
        "LogFileAgeSeconds": round(log_file_age_seconds, 1),
    }
    # EMF: print as JSON to stdout — CloudWatch extracts metrics automatically
    print(json.dumps(emf_payload))


def _extract_s3_records(event: dict[str, Any]) -> list[dict[str, str]]:
    """Extract S3 bucket/key pairs from event."""
    records = []
    if "Records" in event:
        for record in event["Records"]:
            s3_info = record.get("s3", {})
            records.append({
                "bucket": s3_info.get("bucket", {}).get("name", ""),
                "key": s3_info.get("object", {}).get("key", ""),
            })
    elif "detail" in event:
        detail = event["detail"]
        records.append({
            "bucket": detail.get("bucket", {}).get("name", ""),
            "key": detail.get("object", {}).get("key", ""),
        })
    return [r for r in records if r["bucket"] and r["key"]]


def _parse_audit_logs(data: bytes, key: str) -> list[dict[str, Any]]:
    """Parse FSx for ONTAP audit logs (XML, JSON, or EVTX)."""
    if _parse_ontap_audit_log is not None:
        return _parse_ontap_audit_log(data, key)

    logger.warning(
        "ontap_audit_parser unavailable; using the local parser. Single-line XML "
        "documents and some edge cases are not handled."
    )
    if key.endswith(".xml") or (not key.endswith(".evtx") and data.strip()[:5] == b"<?xml"):
        return _parse_xml(data.decode("utf-8", errors="replace"))
    elif key.endswith(".json") or key.endswith(".json.gz"):
        if key.endswith(".gz"):
            data = gzip.decompress(data)
        return _parse_json(data.decode("utf-8"))
    elif data.startswith(b"ElfFile\x00"):
        logger.warning("EVTX format — limited parsing (timestamp only)")
        return [{"event_type": "audit", "source": SOURCE, "message": "EVTX record"}]
    else:
        text = data.decode("utf-8", errors="replace").strip()
        if text.startswith("<"):
            return _parse_xml(text)
        return _parse_json(text)


def _parse_xml(data: str) -> list[dict[str, Any]]:
    """Parse ONTAP XML audit logs with full field extraction."""
    import xml.etree.ElementTree as ET

    events = []
    try:
        if not data.strip().startswith("<?xml"):
            data = f"<AuditEvents>{data}</AuditEvents>"
        else:
            lines = data.strip().split("\n")
            if lines[0].startswith("<?xml"):
                data = f"<AuditEvents>{''.join(lines[1:])}</AuditEvents>"

        root = ET.fromstring(data)

        # Strip XML namespaces before matching tag names.
        #
        # ONTAP writes audit logs in the Windows Event Log XML schema, so every
        # element carries xmlns="http://schemas.microsoft.com/win/2004/08/events/event"
        # and ElementTree reports the tag as "{uri}Event". Without this the
        # iter("Event") below matches nothing and the file yields zero events.
        for elem in root.iter():
            if isinstance(elem.tag, str) and "}" in elem.tag:
                elem.tag = elem.tag.split("}", 1)[1]

        for event_elem in root.iter("Event"):
            event = {}
            for child in event_elem.iter():
                tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                if tag == "Data" and "Name" in child.attrib:
                    if child.text and child.text.strip():
                        event[child.attrib["Name"]] = child.text.strip()
                elif child.text and child.text.strip():
                    event[tag] = child.text.strip()
                for attr_name, attr_value in child.attrib.items():
                    if attr_name != "Name":
                        event[f"{tag}_{attr_name}"] = attr_value
            events.append(_normalize(event))
    except ET.ParseError as e:
        logger.warning("XML parse error: %s", e)

    return events


def _parse_json(data: str) -> list[dict[str, Any]]:
    """Parse JSON audit logs."""
    events = []
    data = data.strip()
    if data.startswith("["):
        try:
            return [_normalize(e) for e in json.loads(data)]
        except json.JSONDecodeError:
            pass
    for line in data.split("\n"):
        line = line.strip()
        if line:
            try:
                events.append(_normalize(json.loads(line)))
            except json.JSONDecodeError:
                continue
    return events


def _normalize(event: dict[str, Any]) -> dict[str, Any]:
    """Normalize to common schema."""
    return {
        "timestamp": event.get("TimeCreated_SystemTime", event.get("timestamp", "")),
        "event_type": event.get("EventID", event.get("event_type", "unknown")),
        "source": SOURCE,
        "svm": event.get("Computer", event.get("SVMName", event.get("svm", ""))),
        "user": event.get("SubjectUserName", event.get("UserName", event.get("user", ""))),
        "client_ip": event.get("IpAddress", event.get("ClientIP", event.get("client_ip", ""))),
        "operation": event.get("ObjectType", event.get("Operation", event.get("operation", ""))),
        "path": event.get("ObjectName", event.get("path", "")),
        "result": event.get("Keywords", event.get("Result", event.get("result", ""))),
    }


def _format_for_logscale(logs: list[dict[str, Any]], source_key: str) -> list[dict[str, Any]]:
    """Format logs as HEC events for LogScale.

    Per LogScale HEC docs, top-level `time` must be epoch seconds (float).
    This is translated to @timestamp on ingestion. If omitted, LogScale
    uses ingest time instead of event time.

    Searchable metadata (s3_key, log_format) is placed inside the `event`
    object rather than `fields`, because LogScale auto-parses JSON `event`
    objects into searchable fields. The `fields` behavior may differ between
    Splunk and LogScale implementations.
    """
    formatted = []
    for log in logs:
        epoch_time = _iso_to_epoch(log.get("timestamp", ""))
        # Put metadata inside event for consistent field extraction
        enriched_event = {
            **log,
            "s3_key": source_key,
            "log_format": "xml" if log.get("timestamp") else "unknown",
        }
        hec_event: dict[str, Any] = {
            "event": enriched_event,
            "source": SOURCE,
            "sourcetype": SOURCETYPE,
            "index": INDEX,
        }
        if epoch_time is not None:
            hec_event["time"] = epoch_time
        formatted.append(hec_event)
    return formatted


def _iso_to_epoch(iso_str: str) -> float | None:
    """Convert ISO 8601 timestamp to epoch seconds for HEC time field.

    Returns None if parsing fails (LogScale will use ingest time).
    """
    if not iso_str:
        return None
    try:
        # Handle both 'Z' suffix and '+00:00' timezone formats
        iso_str = iso_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso_str)
        return dt.timestamp()
    except (ValueError, AttributeError):
        return None


def _ship_to_logscale(events: list[dict[str, Any]], token: str) -> int:
    """Ship HEC events to LogScale with retry."""
    if not events:
        return 0

    payload = "\n".join(json.dumps(e) for e in events).encode("utf-8")
    url = f"{LOGSCALE_URL.rstrip('/')}{HEC_PATH}"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}

    for attempt in range(MAX_RETRIES):
        try:
            resp = http.request("POST", url, body=payload, headers=headers, timeout=30.0)
            if resp.status < 300:
                return len(events)
            if resp.status == 429:
                time.sleep(2 ** (attempt + 1))
                continue
            if resp.status >= 500:
                time.sleep(2 ** (attempt + 1) + random.uniform(0, 1))
                continue
            logger.error("LogScale error %d: %s", resp.status, resp.data.decode()[:500])
            return 0
        except urllib3.exceptions.HTTPError as e:
            if attempt < MAX_RETRIES - 1:
                time.sleep(2 ** (attempt + 1))
            logger.warning("HTTP error (attempt %d): %s", attempt + 1, e)

    raise RuntimeError(f"Failed to ship to LogScale after {MAX_RETRIES} retries")
