"""SnapMirror health and lag collector for the Terraform ONTAP metrics handler.

Reads GET /api/snapmirror/relationships on the DESTINATION file system and
builds CloudWatch datums in namespace FSxONTAP/SnapMirror.

Field reference: ONTAP 9.18.1 REST API, GET /api/snapmirror/relationships
https://docs.netapp.com/us-en/ontap-restapi-9181/get-snapmirror-relationships.html
The reference runs its lag example on "the cluster containing the destination
endpoint", which is why this collector does not use list_destinations_only.

Per relationship, for at most max_relationships of them in ONTAP response
order (dimensions FileSystemId, SourcePath, DestinationPath):
  - SnapMirrorRelationshipHealthy: 1 when ``healthy`` is true, else 0. A
    missing or non-boolean ``healthy`` counts as 0 (fail toward alerting).
  - SnapMirrorLagSeconds: ``lag_time`` (ISO 8601 duration) in seconds. No
    datum when ``lag_time`` is missing (for example ``uninitialized``) or
    cannot be parsed.

Per file system, once per run, over every fetched relationship (dimension
FileSystemId):
  - SnapMirrorUnhealthyCount: relationships counted as 0 above. Always
    published, also as 0 with no relationships; the log line records the
    relationship count so "none visible" can be told apart from "all healthy".
  - SnapMirrorLagSecondsMax: maximum parsed lag. Not published when no lag was
    parsed, so the lag alarm goes to INSUFFICIENT_DATA instead of reading 0.
  - SnapMirrorRelationshipsTruncated: 1 when any fetched relationship got no
    per-relationship series (the relationship cap applied, or a path was
    empty), or the page cap stopped the read with a next link still present;
    else 0.

A relationship with an empty source or destination path gets no
per-relationship series (it would have no usable dimension value) and a
warning; it is still counted in the per-file-system aggregates, and it sets
SnapMirrorRelationshipsTruncated to 1 so the drill-down set is not reported
as complete.

This module is not part of the CloudFormation template; SnapMirror parity in
CloudFormation is a recorded follow-up.
"""
import logging
import re
from typing import Any

import urllib3

import qtree_quota_poller as ontap

logger = logging.getLogger()

SNAPMIRROR_NAMESPACE = "FSxONTAP/SnapMirror"
SNAPMIRROR_FIELDS = (
    "uuid",
    "healthy",
    "unhealthy_reason",
    "lag_time",
    "state",
    "source.path",
    "destination.path",
)
MAX_RECORDS = 200  # relationships per ONTAP page
MAX_PAGES = 50  # hard cap: 50 x 200 = 10,000 relationships per run
REASON_MESSAGE_LIMIT = 256  # characters kept per unhealthy_reason message

# P[nW][nD][T[nH][nM][n[.n]S]]. Year and month designators are rejected
# because their length in seconds is ambiguous. ``P2DT`` (a bare T) is
# accepted because the ONTAP reference uses it in a lag_time filter example.
_DURATION = re.compile(
    r"^P(?:(?P<weeks>\d+)W)?(?:(?P<days>\d+)D)?"
    r"(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+(?:\.\d+)?)S)?)?$"
)
_SECONDS_PER = {"weeks": 604800, "days": 86400, "hours": 3600, "minutes": 60, "seconds": 1}


def parse_iso8601_duration(value: str) -> float | None:
    """Convert an ISO 8601 duration such as ``PT8H35M42S`` to seconds.

    Args:
        value: Duration string from ``lag_time``.

    Returns:
        Seconds as a float, or None for an empty string, a year or month
        designator, or any other unparseable value.
    """
    if not isinstance(value, str):
        return None
    match = _DURATION.match(value)
    if match is None:
        return None
    parts = match.groupdict()
    if all(v is None for v in parts.values()):
        return None  # "P" or "PT" alone carries no quantity
    return float(sum(float(v) * _SECONDS_PER[k] for k, v in parts.items() if v is not None))


def _first_page_path() -> str:
    """Return the first request path with the fields this collector reads."""
    return (
        "/api/snapmirror/relationships"
        f"?fields={','.join(SNAPMIRROR_FIELDS)}"
        f"&max_records={MAX_RECORDS}"
    )


def fetch_relationships(
    *,
    retries: urllib3.util.Retry | None = None,
    credentials_ttl: float | None = None,
) -> tuple[list[dict[str, Any]], int, bool]:
    """Read every SnapMirror relationship, following _links.next.href.

    Args:
        retries: Passed to qtree_quota_poller._ontap_get.
        credentials_ttl: Passed to qtree_quota_poller._ontap_get.

    Returns:
        (records, pages_read, page_truncated). page_truncated is True only
        when a next link is still present after MAX_PAGES pages.
    """
    next_href: str | None = _first_page_path()
    records: list[dict[str, Any]] = []
    pages = 0
    while next_href is not None and pages < MAX_PAGES:
        data = ontap._ontap_get(next_href, retries=retries, credentials_ttl=credentials_ttl)
        pages += 1
        records.extend(data.get("records", []))
        next_href = data.get("_links", {}).get("next", {}).get("href")
    page_truncated = next_href is not None
    if page_truncated:
        logger.warning(
            "SnapMirror relationship list truncated: stopped after %d pages "
            "(%d relationships) at the %d-page cap",
            pages, len(records), MAX_PAGES,
        )
    return records, pages, page_truncated


def _path(record: dict[str, Any], side: str) -> str:
    endpoint = record.get(side)
    if isinstance(endpoint, dict):
        value = endpoint.get("path")
        if isinstance(value, str):
            return value
    return ""


def _reasons(record: dict[str, Any]) -> list[str]:
    reasons = record.get("unhealthy_reason")
    if not isinstance(reasons, list):
        return []
    out: list[str] = []
    for reason in reasons:
        if isinstance(reason, dict):
            message = str(reason.get("message", ""))[:REASON_MESSAGE_LIMIT]
            out.append(f"{reason.get('code', '')}: {message}")
    return out


def build_snapmirror_metric_data(
    records: list[dict[str, Any]],
    file_system_id: str,
    max_relationships: int,
    page_truncated: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build the SnapMirror datums described in the module docstring.

    Args:
        records: Relationship records from fetch_relationships.
        file_system_id: Value of the FileSystemId dimension (the destination).
        max_relationships: Cap on relationships that get per-relationship series.
        page_truncated: Whether the page cap stopped the read.

    Returns:
        (metric_data, summary). summary has ``relationships``, ``unhealthy``,
        ``lag_max_seconds``, ``truncated`` and ``series_relationships``.
    """
    fs_dimensions = [{"Name": "FileSystemId", "Value": file_system_id}]
    metric_data: list[dict[str, Any]] = []
    unhealthy = 0
    lags: list[float] = []
    series = 0
    pathless = 0
    capped = False

    for record in records:
        healthy = record.get("healthy")
        source_path = _path(record, "source")
        destination_path = _path(record, "destination")
        if healthy is True:
            healthy_value = 1.0
        else:
            healthy_value = 0.0
            unhealthy += 1
            if not isinstance(healthy, bool):
                logger.warning(
                    "SnapMirror relationship %s (%s -> %s) has no boolean healthy "
                    "field (%r); counted as unhealthy",
                    record.get("uuid"), source_path, destination_path, healthy,
                )
            else:
                logger.warning(
                    "SnapMirror relationship %s unhealthy: state=%s %s -> %s reasons=%s",
                    record.get("uuid"), record.get("state"), source_path,
                    destination_path, _reasons(record),
                )

        lag_seconds: float | None = None
        raw_lag = record.get("lag_time")
        if raw_lag is not None:
            lag_seconds = parse_iso8601_duration(raw_lag)
            if lag_seconds is None:
                logger.warning(
                    "SnapMirror relationship %s has an unparseable lag_time %r",
                    record.get("uuid"), raw_lag,
                )
            else:
                lags.append(lag_seconds)

        if not source_path or not destination_path:
            pathless += 1
            logger.warning(
                "SnapMirror relationship %s has an empty source or destination "
                "path; no per-relationship series published for it",
                record.get("uuid"),
            )
            continue
        if series >= max_relationships:
            capped = True
            continue
        series += 1
        dimensions = [
            {"Name": "FileSystemId", "Value": file_system_id},
            {"Name": "SourcePath", "Value": source_path},
            {"Name": "DestinationPath", "Value": destination_path},
        ]
        metric_data.append({
            "MetricName": "SnapMirrorRelationshipHealthy",
            "Dimensions": dimensions,
            "Value": healthy_value,
            "Unit": "Count",
        })
        if lag_seconds is not None:
            metric_data.append({
                "MetricName": "SnapMirrorLagSeconds",
                "Dimensions": dimensions,
                "Value": lag_seconds,
                "Unit": "Seconds",
            })

    truncated = capped or page_truncated or pathless > 0
    if pathless:
        logger.warning(
            "SnapMirror: %d relationship(s) with an empty path have no "
            "per-relationship series; truncation reported",
            pathless,
        )
    if capped:
        logger.warning(
            "SnapMirror per-relationship series capped at %d of %d relationships; "
            "aggregates still cover all of them",
            max_relationships, len(records),
        )
    lag_max = max(lags) if lags else None

    metric_data.append({
        "MetricName": "SnapMirrorUnhealthyCount",
        "Dimensions": fs_dimensions,
        "Value": float(unhealthy),
        "Unit": "Count",
    })
    if lag_max is not None:
        metric_data.append({
            "MetricName": "SnapMirrorLagSecondsMax",
            "Dimensions": fs_dimensions,
            "Value": lag_max,
            "Unit": "Seconds",
        })
    metric_data.append({
        "MetricName": "SnapMirrorRelationshipsTruncated",
        "Dimensions": fs_dimensions,
        "Value": 1.0 if truncated else 0.0,
        "Unit": "Count",
    })

    summary = {
        "relationships": len(records),
        "unhealthy": unhealthy,
        "lag_max_seconds": lag_max,
        "truncated": truncated,
        "series_relationships": series,
    }
    logger.info(
        "SnapMirror on %s: %d relationship(s), %d unhealthy, max lag %s s, "
        "%d with per-relationship series, truncated=%s",
        file_system_id, len(records), unhealthy, lag_max, series, truncated,
    )
    return metric_data, summary
