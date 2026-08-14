"""Kinesis Data Firehose transformation handler for Splunk HEC delivery.

Converts FSx for ONTAP audit log records from Firehose into Splunk HEC
event JSON format. Each record is base64-decoded, parsed as JSON, transformed
into HEC event structure, and re-encoded as base64 for Firehose output.

This Lambda is invoked by Firehose as a data transformation function.
Records that fail parsing or transformation are marked as ProcessingFailed
and routed to the S3 backup bucket by Firehose.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from datetime import datetime
from typing import Any

from models import FirehoseTransformResult

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
SPLUNK_SOURCETYPE = os.environ.get("SPLUNK_SOURCETYPE", "fsxn:ontap:audit")
SPLUNK_SOURCE = os.environ.get("SPLUNK_SOURCE", "fsxn-observability")

logger = logging.getLogger()
logger.setLevel(getattr(logging, LOG_LEVEL))


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Firehose transformation handler.

    Converts each record from FSx for ONTAP audit log format to Splunk HEC
    event JSON. Records that cannot be parsed or transformed are marked
    as ProcessingFailed and will be routed to the S3 backup bucket by
    Firehose.

    Args:
        event: Firehose transformation event containing a list of records.
            Each record has a "recordId" and "data" (base64-encoded audit
            log JSON).
        context: Lambda execution context.

    Returns:
        Firehose transformation response with transformed records. Each
        record contains recordId, result ("Ok" or "ProcessingFailed"),
        and data (base64-encoded HEC event JSON).
    """
    logger.info(
        "Processing Firehose transformation event with %d records",
        len(event.get("records", [])),
    )

    output_records: list[dict[str, str]] = []

    for record in event.get("records", []):
        record_id = record["recordId"]
        raw_data = record["data"]

        try:
            # Decode base64 record data
            decoded_data = base64.b64decode(raw_data).decode("utf-8")

            # Transform the record to HEC event format
            hec_event = _transform_record(decoded_data)

            # Encode the HEC event as base64
            hec_json = json.dumps(hec_event, separators=(",", ":"))
            encoded_data = base64.b64encode(hec_json.encode("utf-8")).decode("utf-8")

            result = FirehoseTransformResult(
                recordId=record_id,
                result="Ok",
                data=encoded_data,
            )

        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.error(
                "Failed to parse record %s: %s", record_id, str(e)
            )
            result = FirehoseTransformResult(
                recordId=record_id,
                result="ProcessingFailed",
                data=raw_data,
            )

        except (KeyError, ValueError) as e:
            logger.error(
                "Failed to transform record %s: %s", record_id, str(e)
            )
            result = FirehoseTransformResult(
                recordId=record_id,
                result="ProcessingFailed",
                data=raw_data,
            )

        output_records.append(result.to_firehose_response())

    return {"records": output_records}


def _transform_record(record_data: str) -> dict[str, Any]:
    """Transform a single audit log record to HEC event format.

    Parses the raw audit log JSON and converts it into a Splunk HEC event
    structure with the required fields: time, host, source, sourcetype,
    and event.

    Args:
        record_data: Raw audit log entry as a JSON string.

    Returns:
        Dict with HEC event fields: time (epoch seconds), host (SVM name),
        source, sourcetype ("fsxn:ontap:audit"), and event (structured dict).

    Raises:
        json.JSONDecodeError: If record_data is not valid JSON.
        ValueError: If required fields are missing from the audit log.
    """
    log_entry = json.loads(record_data)

    # Validate that we have a dict (not a list, string, etc.)
    if not isinstance(log_entry, dict):
        raise ValueError(f"Expected JSON object, got {type(log_entry).__name__}")

    # Extract timestamp and convert to epoch seconds
    timestamp = log_entry.get("timestamp", log_entry.get("Timestamp", ""))
    epoch_time = _to_epoch(timestamp) if timestamp else None

    # Extract host (SVM name)
    host = log_entry.get("SVMName", log_entry.get("svm", "fsxn-ontap"))

    # Build structured event sub-object
    event_data: dict[str, Any] = {
        "event_type": log_entry.get("EventID", log_entry.get("event_type", "unknown")),
        "user": log_entry.get("UserName", log_entry.get("user", "")),
        "client_ip": log_entry.get("ClientIP", log_entry.get("client_ip", "")),
        "operation": log_entry.get("Operation", log_entry.get("operation", "")),
        "path": log_entry.get("ObjectName", log_entry.get("path", "")),
        "result": log_entry.get("Result", log_entry.get("result", "")),
        "svm": log_entry.get("SVMName", log_entry.get("svm", "")),
    }

    # Build HEC event structure
    hec_event: dict[str, Any] = {
        "host": host,
        "source": SPLUNK_SOURCE,
        "sourcetype": SPLUNK_SOURCETYPE,
        "event": event_data,
    }

    if epoch_time is not None:
        hec_event["time"] = epoch_time

    return hec_event


def _to_epoch(timestamp: str) -> float | None:
    """Convert ISO timestamp to epoch seconds.

    Handles ISO 8601 timestamps with timezone info, including the 'Z'
    suffix for UTC. Returns None if the timestamp cannot be parsed.

    Args:
        timestamp: ISO 8601 timestamp string (e.g., "2024-01-15T10:30:00Z"
            or "2024-01-15T10:30:00+09:00").

    Returns:
        Epoch seconds as a float, or None if parsing fails.
    """
    try:
        if timestamp.endswith("Z"):
            timestamp = timestamp[:-1] + "+00:00"
        dt = datetime.fromisoformat(timestamp)
        return dt.timestamp()
    except (ValueError, AttributeError):
        return None
