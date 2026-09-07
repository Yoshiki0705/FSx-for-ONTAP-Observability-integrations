"""Correlate FSx for ONTAP audit records with application-layer signals.

What this closes
----------------
``docs/en/s3ap-monitoring-coverage-implications.md`` (pattern 4) records the
measured fact that an operation arriving through an FSx for ONTAP S3 access
point is written to the ONTAP audit log with ``SubjectUserName`` and
``SubjectDomainName`` set to ``Not Present`` and ``SubjectIP`` set to an AWS
service-side address. The file and the operation are known; the requester is
not. That page's answer is CloudTrail data events on the access point, which
name the **IAM principal**.

For a web application that reads the access point on behalf of many people --
the Amplify Gen2 file portal in ``fsxn-s3ap-serverless-patterns`` is the case
this was written against -- every request uses the same Lambda execution role.
So CloudTrail's answer is identical for every user, and the question "which
person opened this file" stays unanswered by both logs.

This handler supplies the audit half of a join that can answer it. It reads
newly rotated audit log files, keeps only the records that arrived over the S3
access path, and re-emits each one as a structured record carrying the same
``s3ap_join_key`` that ``observability.emit_s3ap_app_signal`` puts on the
application side. The join itself is a CloudWatch Logs Insights query over the
two log groups -- see the README for the query.

Why the join key is not a trace ID
----------------------------------
The normalized audit schema has no free-form field, so nothing can be
propagated into it. The key is therefore derived independently on both sides
from what both sides already know: the object key and the operation. Time is
the third element and is matched as a window, because the two timestamps come
from different clocks.

Why the protocol filter is not optional
---------------------------------------
The same coverage page warns that correlating on file name alone is wrong: a
later read of the same file over SMB or NFS produces a record with that same
file name at that later time. ``is_s3_access_path`` is what makes the join
safe, by keeping only ``Source`` values ``HTTP`` (object operations) and ``S3``
(bucket-level operations such as LIST). Removing the filter reintroduces
exactly the false attribution the page describes.

Known gaps, carried deliberately
--------------------------------
* **HEAD is not audited.** The same page reports six HEAD calls producing zero
  audit records. An existence check made by the application has no audit side
  to join to, and will show up as an unmatched application signal rather than
  as a correlated pair.
* **No verified ONTAP ``EventName`` for an S3 GET is known.** The captured set
  covers create, write, list and unlink. ``observability.ONTAP_EVENT_TO_S3_VERB``
  therefore has no GET row, and an unrecognised name is normalized rather than
  guessed at, so a GET correlates only if both sides spell it the same way.
  ``UnmappedOperations`` is emitted so the gap is visible rather than silent.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError

from observability import (
    EmfMetrics,
    audit_path_volume,
    instrument_handler,
    is_audit_management_event,
    is_joinable_audit_event,
    is_s3_access_path,
    join_key_from_audit_event,
    ONTAP_EVENT_TO_S3_VERB,
)
from ontap_audit_parser import parse_audit_log

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

# How far back of a file's modification time to still consider, covering the
# gap between a record being written and the object's LastModified settling.
GRACE_SECONDS = 300

# Metric names. Kept here rather than in the shared module because they describe
# this correlator's own behaviour, not a shape every handler shares.
METRIC_AUDIT_FILES_READ = "AuditFilesRead"
METRIC_AUDIT_RECORDS_PARSED = "AuditRecordsParsed"
METRIC_S3_PATH_RECORDS = "S3PathRecords"
METRIC_FILE_PROTOCOL_RECORDS_SKIPPED = "FileProtocolRecordsSkipped"
METRIC_AUDIT_MANAGEMENT_RECORDS_SKIPPED = "AuditManagementRecordsSkipped"
METRIC_PATHLESS_RECORDS_SKIPPED = "PathlessRecordsSkipped"
METRIC_ALREADY_SEEN_RECORDS_SKIPPED = "AlreadySeenRecordsSkipped"
METRIC_UNDATED_RECORDS = "UndatedRecords"
METRIC_JOIN_KEYS_EMITTED = "JoinKeysEmitted"
METRIC_UNMAPPED_OPERATIONS = "UnmappedOperations"
METRIC_UNPARSEABLE_FILES = "UnparseableFiles"

# The log line marker that the Logs Insights join filters on. A marker rather
# than a bare field name so the query cannot accidentally match an application
# signal record, which carries the same join key field.
AUDIT_SIDE_MARKER = "fsxn.audit.correlation"


def _s3_client():
    return boto3.client("s3")


def _ssm_client():
    return boto3.client("ssm")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _read_checkpoint(param_name: str) -> int:
    """Return the watermark as epoch nanoseconds, or 0 on first run.

    A missing parameter is treated as a first run rather than as an error: the
    stack creates it, but a hand-rolled deployment may not have. So is the old
    key-shaped sentinel, so a stack deployed before the watermark changed shape
    starts over rather than crashing.
    """
    if not param_name:
        return 0
    try:
        value = _ssm_client().get_parameter(Name=param_name)["Parameter"]["Value"]
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ParameterNotFound":
            logger.warning("Checkpoint %s absent; starting from the beginning", param_name)
            return 0
        raise
    value = (value or "").strip()
    if not value or not value.isdigit():
        # Either the sentinel or a leftover key from the previous scheme.
        logger.info("Checkpoint %r is not a watermark; starting from the beginning", value)
        return 0
    return int(value)


def _write_checkpoint(param_name: str, watermark_ns: int) -> None:
    if not param_name or watermark_ns <= 0:
        return
    _ssm_client().put_parameter(
        Name=param_name, Value=str(watermark_ns), Type="String", Overwrite=True
    )


def _list_candidate_audit_keys(
    bucket: str, prefix: str, watermark_ns: int, max_keys: int
) -> list[str]:
    """List audit files that may hold records newer than the watermark.

    **Why this does not use a key high-water mark.** ONTAP appends to a single
    active file whose key is fixed -- ``audit_<svm>_last.xml`` -- and rotates it
    into ``audit_<svm>_D<timestamp>_<n>.xml``. Two things follow, both measured
    on ONTAP 9.18.1P3D1:

    1. The active file's key never changes while its content grows. A checkpoint
       that records "I have read up to this key" and passes it to ``StartAfter``
       stops reading the active file the moment it first reads it. Observed: the
       file grew from 907 to 12,754 bytes and the next run listed zero files.
    2. A rotated key sorts **before** ``_last``, because ``D`` < ``l``. So once
       the checkpoint holds ``..._last.xml``, every file rotated afterwards is
       skipped permanently and the pipeline never recovers.

    So the watermark is the timestamp of the newest audit *record* already
    emitted, and files are selected by modification time instead. A file whose
    last modification precedes the watermark cannot contain a newer record; one
    that does not is read and filtered record by record.

    ``GRACE_SECONDS`` covers the gap between a record being written and the
    object's modification time settling.
    """
    watermark_dt = (
        datetime.fromtimestamp(watermark_ns / 1_000_000_000, tz=timezone.utc)
        if watermark_ns
        else None
    )
    paginator = _s3_client().get_paginator("list_objects_v2")

    candidates: list[tuple[datetime, str]] = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for item in page.get("Contents", []):
            key = item["Key"]
            if key.endswith("/"):
                # A "directory" placeholder has nothing to parse.
                continue
            modified = item.get("LastModified")
            if watermark_dt and modified is not None:
                if modified < watermark_dt - timedelta(seconds=GRACE_SECONDS):
                    continue
            candidates.append((modified or datetime.min.replace(tzinfo=timezone.utc), key))

    # Oldest first, so records are emitted in roughly the order they happened and
    # a mid-run failure leaves the watermark behind rather than ahead.
    candidates.sort()
    return [key for _, key in candidates[:max_keys]]


def _read_object(bucket: str, key: str) -> bytes:
    return _s3_client().get_object(Bucket=bucket, Key=key)["Body"].read()


def _audit_timestamp_ns(event: dict[str, Any]) -> int | None:
    """Parse the audit timestamp into epoch nanoseconds.

    Nanoseconds rather than milliseconds because this value is the deduplication
    watermark, not just a display field. ONTAP writes nanosecond precision
    (``2026-08-25T23:32:21.670728420Z``); rounding to milliseconds would make
    records written inside the same millisecond indistinguishable, and the
    watermark would then either skip or repeat them.

    The fractional digits are read directly rather than through
    ``datetime.fromisoformat``, which accepts at most microseconds.
    """
    raw = str(event.get("timestamp", "")).strip()
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


def _audit_timestamp_ms(event: dict[str, Any]) -> int | None:
    """Epoch milliseconds, for the emitted record's readable timestamp field."""
    ns = _audit_timestamp_ns(event)
    return None if ns is None else ns // 1_000_000





def build_correlation_record(event: dict[str, Any], source_key: str) -> dict[str, Any]:
    """Shape one normalized audit event into the audit side of the join.

    The record carries only what the join and the follow-up investigation need.
    ``raw`` is deliberately dropped: it is the whole original event, it can
    contain a full path, and it would multiply CloudWatch Logs ingestion cost
    for a field the join never reads.
    """
    return {
        "marker": AUDIT_SIDE_MARKER,
        "s3ap_join_key": join_key_from_audit_event(event),
        "audit_operation": event.get("operation", ""),
        "audit_path": event.get("path", ""),
        "audit_volume": audit_path_volume(event.get("path", "")),
        "audit_timestamp": event.get("timestamp", ""),
        "audit_timestamp_ms": _audit_timestamp_ms(event),
        "audit_access_protocol": event.get("access_protocol", ""),
        "audit_svm": event.get("svm", ""),
        "audit_result": event.get("result", ""),
        "audit_source_key": source_key,
    }


@instrument_handler(namespace=_env("EMF_NAMESPACE", "FSxONTAPAppSignals"), service="audit-correlator")
def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Read new audit log files and emit the audit side of the correlation.

    Returns a summary rather than the records themselves: the records go to
    CloudWatch Logs, which is where the join reads them from.
    """
    bucket = _env("FSX_S3_ACCESS_POINT_ARN")
    prefix = _env("AUDIT_LOG_PREFIX", "")
    checkpoint_param = _env("CHECKPOINT_PARAM_NAME")
    max_keys = int(_env("MAX_KEYS_PER_RUN", "50"))

    if not bucket:
        raise ValueError("FSX_S3_ACCESS_POINT_ARN is not set")

    metrics = EmfMetrics(
        namespace=_env("EMF_NAMESPACE", "FSxONTAPAppSignals"),
        service="audit-correlator",
    )

    watermark_ns = _read_checkpoint(checkpoint_param)
    keys = _list_candidate_audit_keys(bucket, prefix, watermark_ns, max_keys)

    files_read = 0
    records_parsed = 0
    s3_path_records = 0
    skipped_file_protocol = 0
    skipped_management = 0
    skipped_pathless = 0
    skipped_already_seen = 0
    undated_records = 0
    unparseable_files = 0
    unmapped_operations = 0
    emitted = 0
    highest_ns = watermark_ns

    for key in keys:
        try:
            payload = _read_object(bucket, key)
        except ClientError:
            logger.exception("Could not read %s; stopping rather than advancing past it", key)
            break

        try:
            events = parse_audit_log(payload, key)
        except Exception:
            # A file that cannot be parsed must not stall progress forever, but
            # it must be visible. Counted and skipped. The watermark is not
            # affected: it advances on records, not on files.
            logger.exception("Unparseable audit file %s", key)
            unparseable_files += 1
            files_read += 1
            continue

        files_read += 1
        records_parsed += len(events)

        for audit_event in events:
            # The active file is re-read on every run, so records already
            # emitted have to be filtered out here rather than by not reading
            # the file. Strictly greater than: the watermark is a record that
            # was emitted.
            record_ns = _audit_timestamp_ns(audit_event)
            if record_ns is None:
                # No timestamp means no way to tell whether it was emitted
                # before. Emitting it would duplicate on every run, so it is
                # dropped and counted.
                undated_records += 1
                continue
            if watermark_ns and record_ns <= watermark_ns:
                skipped_already_seen += 1
                continue

            if is_audit_management_event(audit_event):
                # "Audit Enabled" arrives with Source=http, so the access-path
                # filter alone lets it through. It describes the audit subsystem,
                # not a file, and has no path to join on.
                skipped_management += 1
                continue

            if not is_s3_access_path(audit_event):
                # A later SMB/NFS read of the same file would otherwise be
                # attributed to whoever last opened it through the portal.
                skipped_file_protocol += 1
                continue

            if not is_joinable_audit_event(audit_event):
                # Reaching here means the record is on the S3 path and is not an
                # audit-management record, so what remains is a missing path. Its
                # key would match every other pathless record, so it is counted
                # under its own name rather than being folded into the
                # file-protocol count, which would misreport why it was dropped.
                skipped_pathless += 1
                continue

            s3_path_records += 1
            operation = str(audit_event.get("operation", "")).strip()
            if operation and operation not in ONTAP_EVENT_TO_S3_VERB:
                unmapped_operations += 1

            record = build_correlation_record(audit_event, key)
            print(json.dumps(record, default=str))
            emitted += 1
            highest_ns = max(highest_ns, record_ns)

    if highest_ns > watermark_ns:
        _write_checkpoint(checkpoint_param, highest_ns)

    metrics.put_metric(METRIC_AUDIT_FILES_READ, files_read, "Count")
    metrics.put_metric(METRIC_AUDIT_RECORDS_PARSED, records_parsed, "Count")
    metrics.put_metric(METRIC_S3_PATH_RECORDS, s3_path_records, "Count")
    metrics.put_metric(METRIC_FILE_PROTOCOL_RECORDS_SKIPPED, skipped_file_protocol, "Count")
    metrics.put_metric(
        METRIC_AUDIT_MANAGEMENT_RECORDS_SKIPPED, skipped_management, "Count"
    )
    metrics.put_metric(METRIC_PATHLESS_RECORDS_SKIPPED, skipped_pathless, "Count")
    metrics.put_metric(METRIC_JOIN_KEYS_EMITTED, emitted, "Count")
    metrics.put_metric(METRIC_UNMAPPED_OPERATIONS, unmapped_operations, "Count")
    metrics.put_metric(METRIC_UNPARSEABLE_FILES, unparseable_files, "Count")
    metrics.put_metric(
        METRIC_ALREADY_SEEN_RECORDS_SKIPPED, skipped_already_seen, "Count"
    )
    metrics.put_metric(METRIC_UNDATED_RECORDS, undated_records, "Count")
    metrics.set_property("watermark_before_ns", watermark_ns)
    metrics.set_property("watermark_after_ns", highest_ns)
    metrics.flush()

    return {
        "statusCode": 200,
        "files_read": files_read,
        "records_parsed": records_parsed,
        "s3_path_records": s3_path_records,
        "file_protocol_records_skipped": skipped_file_protocol,
        "audit_management_records_skipped": skipped_management,
        "pathless_records_skipped": skipped_pathless,
        "join_keys_emitted": emitted,
        "unmapped_operations": unmapped_operations,
        "unparseable_files": unparseable_files,
        "already_seen_records_skipped": skipped_already_seen,
        "undated_records": undated_records,
        "watermark_ns": highest_ns,
    }
