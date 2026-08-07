"""FSx for ONTAP audit log shipper for Datadog.

Reads audit logs from S3 Access Point, parses EVTX/JSON format,
and ships to Datadog Logs Intake API v2.

Supports all Datadog sites (US1, US3, US5, EU1, AP1, US1-FED, AP2).
See: https://docs.datadoghq.com/getting_started/site/
"""

import gzip
import json
import logging
import os
import random
import time
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


# ─── Configuration from environment ────────────────────────────────────────
# All configuration is driven by environment variables for multi-region support.
# No hardcoded values — each deployment can target any Datadog site.

DATADOG_SITE = os.environ.get("DATADOG_SITE", "datadoghq.com")
API_KEY_SECRET_ARN = os.environ.get("API_KEY_SECRET_ARN", "")
S3_ACCESS_POINT_ARN = os.environ.get("FSX_S3_ACCESS_POINT_ARN", os.environ.get("S3_ACCESS_POINT_ARN", ""))
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

# Key prefix to scan within the FSx for ONTAP S3 Access Point (e.g. "audit/").
AUDIT_LOG_PREFIX = os.environ.get("AUDIT_LOG_PREFIX", "")

# SSM Parameter Store name holding the last processed S3 key.
# FSx for ONTAP S3 APs do not emit S3 Event Notifications, so the poller
# tracks progress with a checkpoint instead of relying on event delivery.
CHECKPOINT_PARAM_NAME = os.environ.get("CHECKPOINT_PARAM_NAME", "")

# Upper bound on files handled per scheduled invocation. Prevents a large
# backlog from exhausting the Lambda timeout mid-file.
MAX_KEYS_PER_RUN = int(os.environ.get("MAX_KEYS_PER_RUN", "100"))

# Stop starting new files when less than this much execution time remains,
# so the checkpoint is always written before the runtime kills the invocation.
SAFETY_THRESHOLD_MS = int(os.environ.get("SAFETY_THRESHOLD_MS", "30000"))
DD_SOURCE = os.environ.get("DD_SOURCE", "fsxn")
DD_SERVICE = os.environ.get("DD_SERVICE", "ontap-audit")
DD_ENV = os.environ.get("DD_ENV", os.environ.get("ENV", "production"))

# Whether to use gzip compression for log payloads.
# Datadog officially supports gzip (Content-Encoding: gzip) and recommends it.
# However, during E2E testing (2026-05-16) on AP1 site, gzip payloads were
# accepted (HTTP 202) but not indexed. Root cause: urllib3's PoolManager in
# Lambda runtime may not correctly handle pre-compressed body bytes in some
# versions. The fix is to use urllib3.request() with preload_content=False
# and pass the gzip bytes directly. Set to "true" to enable.
ENABLE_GZIP = os.environ.get("ENABLE_GZIP", "false").lower() == "true"

# ─── Constants ──────────────────────────────────────────────────────────────

MAX_BATCH_SIZE_BYTES = 5 * 1024 * 1024  # 5MB per request (Datadog limit)
MAX_BATCH_ITEMS = 1000  # Max items per batch (Datadog limit)
MAX_RETRIES = 3
MAX_LOG_AGE_HOURS = 18  # Datadog rejects logs older than 18 hours

# Datadog Logs Intake URL — constructed from DATADOG_SITE env var.
# Supports all Datadog sites:
#   US1:     datadoghq.com        → http-intake.logs.datadoghq.com
#   US3:     us3.datadoghq.com    → http-intake.logs.us3.datadoghq.com
#   US5:     us5.datadoghq.com    → http-intake.logs.us5.datadoghq.com
#   EU1:     datadoghq.eu         → http-intake.logs.datadoghq.eu
#   AP1:     ap1.datadoghq.com    → http-intake.logs.ap1.datadoghq.com
#   AP2:     ap2.datadoghq.com    → http-intake.logs.ap2.datadoghq.com
#   US1-FED: ddog-gov.com         → http-intake.logs.ddog-gov.com
INTAKE_URL = f"https://http-intake.logs.{DATADOG_SITE}/api/v2/logs"

# ─── Logger setup ──────────────────────────────────────────────────────────

logger = logging.getLogger()
logger.setLevel(getattr(logging, LOG_LEVEL))

# ─── AWS clients (initialized outside handler for connection reuse) ─────────

secrets_client = boto3.client("secretsmanager")
s3_client = boto3.client("s3")
ssm_client = boto3.client("ssm")

# HTTP client with connection pooling
http = urllib3.PoolManager(
    num_pools=4,
    maxsize=10,
    retries=urllib3.Retry(total=0),  # We handle retries ourselves
)

# Cache for API key (Lambda execution context reuse)
_api_key_cache = None  # type: str | None


def get_api_key() -> str:
    """Retrieve Datadog API key from Secrets Manager with caching.

    Supports both plain string and JSON format secrets:
    - Plain string: "your-api-key"
    - JSON: {"api_key": "your-api-key"} or {"DD_API_KEY": "your-api-key"}
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
    """Lambda handler for FSx for ONTAP audit log shipping to Datadog.

    Supports two invocation modes:

    1. **Scheduler polling** (production path) — ``event["source"] == "scheduler"``.
       FSx for ONTAP S3 Access Points do not support S3 Event Notifications or
       EventBridge object-level events, so EventBridge Scheduler invokes this
       function periodically. New files are discovered with ``ListObjectsV2``
       and progress is tracked in an SSM Parameter Store checkpoint.
    2. **S3 event payload** (manual testing / backward compatibility) — the
       event contains ``Records`` or ``detail``.

    Args:
        event: Scheduler payload, S3 event notification, or EventBridge event.
        context: Lambda context object.

    Returns:
        Response with status code and processing summary.
    """
    logger.info("Processing event: %s", json.dumps(event, default=str))

    api_key = get_api_key()

    if event.get("source") == "scheduler":
        return _handle_scheduler_event(event, api_key, context)

    return _handle_s3_event(event, api_key)


def _handle_scheduler_event(
    event: dict[str, Any], api_key: str, context: Any = None
) -> dict[str, Any]:
    """Handle EventBridge Scheduler invocation (polling mode).

    Lists objects under the audit prefix via the S3 Access Point, processes
    only keys lexicographically greater than the checkpoint, and advances the
    checkpoint after each successfully shipped file.

    Processing stops at the first failing file so the checkpoint never skips
    over an unshipped file (at-least-once delivery, no silent gaps).

    Args:
        event: Scheduler payload. May override ``prefix`` and
            ``s3_access_point_arn``.
        api_key: Datadog API key.
        context: Lambda context, used to stop before the timeout.

    Returns:
        Response with status code and processing summary.
    """
    s3_ap_arn = event.get("s3_access_point_arn", S3_ACCESS_POINT_ARN)
    prefix = event.get("prefix", AUDIT_LOG_PREFIX)

    last_processed_key = _get_checkpoint()
    logger.info(
        "Scheduler mode: prefix=%s, checkpoint=%s", prefix, last_processed_key or "(none)"
    )

    new_keys = _list_new_keys(s3_ap_arn, prefix, last_processed_key)

    if not new_keys:
        logger.info("No new audit log files to process")
        return {
            "statusCode": 200,
            "body": {"total_logs": 0, "total_shipped": 0, "new_files": 0, "errors": []},
        }

    # Bound the work per invocation so a large backlog drains over several runs
    # instead of timing out mid-file.
    if len(new_keys) > MAX_KEYS_PER_RUN:
        logger.warning(
            "Backlog of %d files exceeds MAX_KEYS_PER_RUN=%d; processing the "
            "oldest %d this run (remainder drains on the next schedule)",
            len(new_keys), MAX_KEYS_PER_RUN, MAX_KEYS_PER_RUN,
        )
        new_keys = new_keys[:MAX_KEYS_PER_RUN]

    logger.info("Found %d new audit log file(s) to process", len(new_keys))

    total_logs = 0
    total_shipped = 0
    errors: list[dict[str, str]] = []
    last_successful_key = last_processed_key

    for idx, key in enumerate(new_keys):
        # Leave enough headroom to persist the checkpoint before the timeout.
        if context is not None and hasattr(context, "get_remaining_time_in_millis"):
            remaining_ms = context.get_remaining_time_in_millis()
            if remaining_ms < SAFETY_THRESHOLD_MS:
                logger.warning(
                    "Stopping early: %dms remaining, %d file(s) deferred to next run",
                    remaining_ms, len(new_keys) - idx,
                )
                break

        try:
            data = _read_s3_object(s3_ap_arn, key)
            logs = _parse_audit_logs(data, key)
            total_logs += len(logs)

            dd_logs = _format_for_datadog(logs, key)
            shipped = _ship_to_datadog(dd_logs, api_key)
            total_shipped += shipped

            # A file with no parseable records is a legitimately empty rotation
            # and is checkpointed; a file whose logs all failed to ship is not
            # (_ship_to_datadog raises in that case).
            last_successful_key = key

        except Exception as e:
            logger.error("Failed to process %s: %s", key, str(e))
            errors.append({"key": key, "error": str(e)})
            # Stop on first error: advancing past a failed file would drop it.
            break

    if last_successful_key != last_processed_key:
        _set_checkpoint(last_successful_key)

    result = {
        "statusCode": 200 if not errors else 207,
        "body": {
            "total_logs": total_logs,
            "total_shipped": total_shipped,
            "new_files": len(new_keys),
            "processed_files": len(new_keys) - len(errors),
            "checkpoint": last_successful_key,
            "errors": errors,
        },
    }
    logger.info("Scheduler run complete: %s", json.dumps(result))
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
            "whole prefix and duplicate logs in Datadog"
        )
        return ""
    try:
        response = ssm_client.get_parameter(Name=CHECKPOINT_PARAM_NAME)
        value = response["Parameter"]["Value"]
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
    """Persist the last successfully processed S3 key to SSM Parameter Store.

    A failure here is logged but not raised: the files were already delivered,
    so failing the invocation would re-ship them on the next run.
    """
    if not CHECKPOINT_PARAM_NAME:
        return
    try:
        ssm_client.put_parameter(
            Name=CHECKPOINT_PARAM_NAME,
            Value=key,
            Type="String",
            Overwrite=True,
        )
        logger.info("Checkpoint updated: %s", key)
    except Exception as e:
        logger.error(
            "Failed to update checkpoint to %s: %s — these files may be "
            "re-shipped on the next run",
            key, str(e),
        )


# ─── S3 listing ─────────────────────────────────────────────────────────────


def _list_new_keys(s3_ap_arn: str, prefix: str, last_processed_key: str) -> list[str]:
    """List S3 Access Point objects newer than the checkpoint.

    ``ListObjectsV2`` returns keys in lexicographic order, so audit logs written
    under a date-based prefix (``YYYY/MM/DD/``) are naturally chronological and
    ``StartAfter`` can skip everything already processed server-side.

    Args:
        s3_ap_arn: FSx for ONTAP S3 Access Point ARN (used as ``Bucket``).
        prefix: Key prefix to scan.
        last_processed_key: Checkpoint; keys <= this value are skipped.

    Returns:
        Sorted list of object keys to process (directory markers excluded).
    """
    all_keys: list[str] = []
    continuation_token: str | None = None

    params: dict[str, Any] = {"Bucket": s3_ap_arn, "MaxKeys": 1000}
    if prefix:
        params["Prefix"] = prefix
    if last_processed_key:
        params["StartAfter"] = last_processed_key

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
    return all_keys


def _handle_s3_event(event: dict[str, Any], api_key: str) -> dict[str, Any]:
    """Handle an S3 event payload (manual testing / backward compatibility)."""
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

            # Format for Datadog
            dd_logs = _format_for_datadog(logs, key)

            # Ship in batches
            shipped = _ship_to_datadog(dd_logs, api_key)
            total_shipped += shipped

        except Exception as e:
            logger.error("Failed to process %s/%s: %s", bucket, key, str(e))
            errors.append({"bucket": bucket, "key": key, "error": str(e)})

    result = {
        "statusCode": 200 if not errors else 207,
        "body": {
            "total_logs": total_logs,
            "total_shipped": total_shipped,
            "errors": errors,
        },
    }
    logger.info("Processing complete: %s", json.dumps(result))
    return result


def _extract_s3_records(event: dict[str, Any]) -> list[dict[str, str]]:
    """Extract S3 bucket/key pairs from event.

    Handles the non-scheduled invocation patterns:
    - Direct invocation with an S3 event payload (testing / backward compat)
    - EventBridge S3 Object Created event (legacy / other vendor stacks)

    Scheduler invocations never reach here — ``lambda_handler`` routes them to
    ``_handle_scheduler_event``, which discovers keys via ``ListObjectsV2``.
    """
    records = []

    # S3 event notification format (backward compat / testing)
    if "Records" in event:
        for record in event["Records"]:
            s3_info = record.get("s3", {})
            records.append(
                {
                    "bucket": s3_info.get("bucket", {}).get("name", ""),
                    "key": s3_info.get("object", {}).get("key", ""),
                }
            )

    # EventBridge S3 Object Created format (other vendor stacks)
    elif "detail" in event:
        detail = event["detail"]
        records.append(
            {
                "bucket": detail.get("bucket", {}).get("name", ""),
                "key": detail.get("object", {}).get("key", ""),
            }
        )

    return [r for r in records if r["bucket"] and r["key"]]


def _read_s3_object(bucket: str, key: str) -> bytes:
    """Read object from S3 Access Point.

    Note: S3_ACCESS_POINT_ARN is used as the Bucket parameter.
    This is the correct usage for FSx for ONTAP S3 Access Points —
    the ARN replaces the bucket name in all S3 API calls.
    """
    response = s3_client.get_object(Bucket=S3_ACCESS_POINT_ARN, Key=key)
    return response["Body"].read()


def _parse_audit_logs(data: bytes, key: str) -> list[dict[str, Any]]:
    """Parse FSx for ONTAP audit logs based on file extension.

    Supports:
    - .evtx: Windows Event Log binary format
    - .xml: XML format (ONTAP -format xml)
    - .json: Newline-delimited JSON or JSON array (fallback)
    - .json.gz: Gzip-compressed JSON

    Args:
        data: Raw file content.
        key: S3 object key (used to determine format).

    Returns:
        List of parsed log events.
    """
    if _parse_ontap_audit_log is not None:
        return _parse_ontap_audit_log(data, key)

    logger.warning(
        "ontap_audit_parser unavailable; using the local parser. Single-line XML "
        "documents and some edge cases are not handled."
    )
    if key.endswith(".evtx"):
        return _parse_evtx(data)
    elif key.endswith(".xml"):
        return _parse_xml_logs(data.decode("utf-8", errors="replace"))
    elif key.endswith(".json") or key.endswith(".json.gz"):
        if key.endswith(".gz"):
            data = gzip.decompress(data)
        return _parse_json_logs(data.decode("utf-8"))
    else:
        # Detect format by content
        if data.startswith(b"ElfFile\x00"):
            return _parse_evtx(data)
        text = data.decode("utf-8", errors="replace").strip()
        if text.startswith("<?xml") or text.startswith("<"):
            return _parse_xml_logs(text)
        # Fall back to JSON
        try:
            return _parse_json_logs(text)
        except Exception:
            logger.warning("Unknown format for %s, treating as raw text", key)
            return [{"message": text, "raw": True}]


def _parse_evtx(data: bytes) -> list[dict[str, Any]]:
    """Parse EVTX format audit logs.

    Simplified parser for common FSx for ONTAP audit event structures.
    """
    import struct
    from datetime import datetime, timezone

    events = []

    # Validate EVTX header
    if not data.startswith(b"ElfFile\x00"):
        logger.warning("Invalid EVTX header, attempting JSON parse")
        return _parse_json_logs(data.decode("utf-8", errors="replace"))

    # Parse EVTX records
    offset = 4096  # Skip file header
    while offset < len(data) - 28:  # Minimum record size
        try:
            if data[offset : offset + 4] == b"\x2a\x2a\x00\x00":
                record_size = struct.unpack_from("<I", data, offset + 4)[0]
                if record_size < 28 or offset + record_size > len(data):
                    offset += 1
                    continue

                # Extract timestamp
                timestamp_raw = struct.unpack_from("<Q", data, offset + 16)[0]
                if timestamp_raw > 0:
                    epoch_diff = 116444736000000000
                    ts_seconds = (timestamp_raw - epoch_diff) / 10_000_000
                    try:
                        timestamp = datetime.fromtimestamp(
                            ts_seconds, tz=timezone.utc
                        ).isoformat()
                    except (ValueError, OSError):
                        timestamp = datetime.now(timezone.utc).isoformat()
                else:
                    timestamp = datetime.now(timezone.utc).isoformat()

                events.append(
                    {
                        "timestamp": timestamp,
                        "event_type": "audit",
                        "source": "fsxn-ontap",
                    }
                )
                offset += record_size
            else:
                offset += 1
        except (struct.error, IndexError):
            break

    return events


def _parse_json_logs(data: str) -> list[dict[str, Any]]:
    """Parse JSON format audit logs (newline-delimited or array)."""
    events = []
    data = data.strip()

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


def _parse_xml_logs(data: str) -> list[dict[str, Any]]:
    """Parse XML format audit logs (ONTAP -format xml output).

    ONTAP XML audit logs contain Event elements with fields like
    EventID, TimeCreated, Computer, UserName, ObjectName, etc.

    Args:
        data: XML string content.

    Returns:
        List of parsed log event dictionaries.
    """
    import xml.etree.ElementTree as ET

    events = []

    try:
        # Handle multiple root elements by wrapping in a container
        if not data.strip().startswith("<?xml"):
            data = f"<AuditEvents>{data}</AuditEvents>"
        else:
            # Remove XML declaration and wrap
            lines = data.strip().split("\n")
            if lines[0].startswith("<?xml"):
                data = f"<AuditEvents>{''.join(lines[1:])}</AuditEvents>"

        root = ET.fromstring(data)

        # Strip XML namespaces before matching tag names.
        #
        # ONTAP writes audit logs in the Windows Event Log XML schema, so every
        # element carries xmlns="http://schemas.microsoft.com/win/2004/08/events/event".
        # ElementTree then reports the tag as "{uri}Event", and a plain
        # iter("Event") matches nothing — every event in the file falls through
        # to the flat-record fallback below and gets merged into a single record,
        # silently discarding all but the last event.
        for elem in root.iter():
            if isinstance(elem.tag, str) and "}" in elem.tag:
                elem.tag = elem.tag.split("}", 1)[1]

        # Find all Event elements (handle various ONTAP XML structures)
        for event_elem in root.iter("Event"):
            event = _xml_element_to_dict(event_elem)
            events.append(event)

        # If no Event elements found, try parsing as flat records
        if not events:
            children = list(root)
            # Descend through a single wrapper element (e.g. <Events>), otherwise
            # every record below it would be flattened into one dict.
            while len(children) == 1 and len(list(children[0])) > 0:
                children = list(children[0])
            for child in children:
                event = _xml_element_to_dict(child)
                if event:
                    events.append(event)

    except ET.ParseError as e:
        logger.warning("XML parse error: %s, attempting line-by-line", e)
        # Try parsing individual XML fragments
        for line in data.split("\n"):
            line = line.strip()
            if line.startswith("<Event") and line.endswith("</Event>"):
                try:
                    elem = ET.fromstring(line)
                    events.append(_xml_element_to_dict(elem))
                except ET.ParseError:
                    continue

    return events


def _xml_element_to_dict(elem) -> dict[str, Any]:
    """Convert an XML element to a flat dictionary.

    Extracts common ONTAP audit fields from XML event structure.
    Handles both namespaced and non-namespaced elements, and
    ONTAP's <Data Name="key">value</Data> pattern.

    Args:
        elem: XML Element object.

    Returns:
        Dictionary with extracted fields.
    """
    result: dict[str, Any] = {}

    # Extract text from all child elements recursively
    for child in elem.iter():
        tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag

        # Handle <Data Name="key">value</Data> pattern (ONTAP EventData)
        if tag == "Data" and "Name" in child.attrib:
            key = child.attrib["Name"]
            if child.text and child.text.strip():
                result[key] = child.text.strip()
        elif child.text and child.text.strip():
            result[tag] = child.text.strip()

        # Capture attributes (e.g., TimeCreated SystemTime="...")
        for attr_name, attr_value in child.attrib.items():
            if attr_name != "Name":  # Skip the "Name" attr from Data elements
                result[f"{tag}_{attr_name}"] = attr_value

    # Map common ONTAP XML fields to normalized schema
    return {
        "timestamp": result.get("TimeCreated_SystemTime", result.get("TimeCreated", "")),
        "event_type": result.get("EventID", result.get("EventType", "audit")),
        "source": "fsxn-ontap",
        "svm": result.get("Computer", result.get("SVMName", "")),
        "user": result.get("SubjectUserName", result.get("UserName", "")),
        "client_ip": result.get("IpAddress", result.get("ClientIP", "")),
        "operation": result.get("ObjectType", result.get("Operation", "")),
        "path": result.get("ObjectName", result.get("HandleID", "")),
        "result": result.get("Keywords", result.get("Result", "")),
        "raw": result,
    }


def _format_for_datadog(
    logs: list[dict[str, Any]], source_key: str
) -> list[dict[str, Any]]:
    """Format parsed logs for Datadog Logs Intake API v2.

    Datadog log format reference:
    - ddsource: Integration name (used for automatic pipeline matching)
    - ddtags: Comma-separated tags
    - hostname: Originating host
    - service: Application/service name
    - message: Log body (highlighted in Log Explorer)
    - date: Timestamp (must be within 18 hours of current time)

    Args:
        logs: Parsed log events.
        source_key: S3 object key for tagging.

    Returns:
        List of Datadog-formatted log entries.
    """
    dd_logs = []
    for log in logs:
        dd_log: dict[str, Any] = {
            "ddsource": DD_SOURCE,
            "ddtags": (
                f"source:{DD_SOURCE},"
                f"service:{DD_SERVICE},"
                f"env:{DD_ENV},"
                f"s3_key:{source_key}"
            ),
            "hostname": log.get("svm", log.get("SVMName", "fsxn-ontap")),
            "service": DD_SERVICE,
        }

        # Set message
        if "message" in log:
            dd_log["message"] = log["message"]
        else:
            dd_log["message"] = json.dumps(log, default=str)

        # Set timestamp if available.
        # NOTE: Datadog only accepts logs with timestamps up to 18 hours in
        # the past. Logs with older timestamps are silently dropped.
        timestamp = log.get("timestamp", log.get("Timestamp"))
        if timestamp:
            dd_log["date"] = timestamp

        # Add structured attributes for Facet-based searching
        dd_log["attributes"] = {
            "event_type": log.get("EventID", log.get("event_type", "unknown")),
            "user": log.get("UserName", log.get("user", "")),
            "client_ip": log.get("ClientIP", log.get("client_ip", "")),
            "operation": log.get("Operation", log.get("operation", "")),
            "path": log.get("ObjectName", log.get("path", "")),
            "result": log.get("Result", log.get("result", "")),
            "svm": log.get("SVMName", log.get("svm", "")),
        }

        # Pipeline observability: add processing metadata
        # Helps operators understand pipeline lag and throughput
        from datetime import datetime, timezone

        dd_log["attributes"]["_pipeline"] = {
            "processed_at": datetime.now(timezone.utc).isoformat(),
            "source_file": source_key,
        }

        dd_logs.append(dd_log)

    return dd_logs


def _ship_to_datadog(logs: list[dict[str, Any]], api_key: str) -> int:
    """Ship logs to Datadog Logs Intake API v2 in batches.

    If any batch fails after retries, raises RuntimeError so the Lambda
    invocation is treated as failed and the checkpoint is not advanced.

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
    """
    batches = []
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
    Datadog recommends gzip for large payloads but it's disabled by default
    due to a known issue with some Lambda runtime urllib3 versions.

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
                # Rate limited - respect Retry-After header
                retry_after = int(
                    response.headers.get("Retry-After", 2 ** (attempt + 1))
                )
                logger.warning(
                    "Rate limited by Datadog, retrying in %ds", retry_after
                )
                time.sleep(retry_after)
                continue

            if response.status >= 500:
                # Server error - retry with backoff + jitter
                wait_time = 2 ** (attempt + 1) + random.uniform(0, 1)
                logger.warning(
                    "Datadog server error %d, retrying in %.1fs",
                    response.status,
                    wait_time,
                )
                time.sleep(wait_time)
                continue

            # Client error (4xx except 429) - don't retry
            logger.error(
                "Datadog API error %d: %s",
                response.status,
                response.data.decode("utf-8", errors="replace")[:500],
            )
            return False

        except urllib3.exceptions.HTTPError as e:
            wait_time = 2 ** (attempt + 1) + random.uniform(0, 1)
            logger.warning(
                "HTTP error shipping to Datadog (attempt %d/%d): %s",
                attempt + 1,
                MAX_RETRIES,
                str(e),
            )
            if attempt < MAX_RETRIES - 1:
                time.sleep(wait_time)

    return False
