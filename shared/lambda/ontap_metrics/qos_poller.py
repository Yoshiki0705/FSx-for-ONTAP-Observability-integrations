"""QoS policy-group collector for the ONTAP metrics handler.

Reads live QoS performance from the ONTAP counter manager and the QoS
workload -> policy-group mapping, then builds CloudWatch datums in namespace
FSxONTAP/QoS. The counter manager is the only REST route to live QoS
performance: GET /api/storage/qos/workloads/{uuid} has no ``statistics``
field and /api/private/cli/qos/statistics returns 405.

Per policy group, for at most max_policy_groups of them in counter-manager
row order (dimensions FileSystemId, PolicyGroup; SvmName is added only when
the workload map resolves one for that policy group):
  - QosOps                 effective IOPS, from counter ``ops``
  - QosThroughputBytes     effective throughput, from counter ``total_data``
  - QosLatencyMicros       from counter ``latency``
  - QosReadOps/QosWriteOps read/write IOPS split, from ``read_ops``/``write_ops``
    (emitted only for the counters present on the row)

Per file system, once per run (dimension FileSystemId):
  - QosPolicyGroupsTruncated: 1 when the page cap stopped the read with a
    next link still present, or the per-policy-group cap dropped a group;
    else 0.

What this exposes is effective throughput/IOPS per policy group. There is no
burst-specific counter in the counter manager, so "pinned at the ceiling" is
visible from these series but "currently bursting" is inferred, not measured.

Counter honesty: the counter manager tags each counter with a ``type``
(raw / rate / delta / average). A rate-typed ``ops`` is already an IOPS value;
a raw-typed counter is a cumulative point sample, not a per-poll delta. This
collector keeps no state between invocations (like the qtree poller), so it
never differences raw counters across polls. When a counter it publishes is
raw-only for this cluster, it publishes the point sample the table returns and
sets ``point_sample`` in the summary so the reader knows the value is not a
per-poll rate.

This module is not part of a CloudFormation template on its own; the QoS CFN
template concatenates this source with the shared client.
"""
import logging
import os
from typing import Any

import urllib3

import qtree_quota_poller as ontap
from qtree_quota_poller import (
    MAX_PAGES,
    MAX_RECORDS,
    OntapAuthError,
    PUT_BATCH_SIZE,
    invalidate_credentials,
)

logger = logging.getLogger()

# Re-exported so the handler and tests can reach them through this module too.
__all__ = [
    "MAX_PAGES",
    "MAX_RECORDS",
    "OntapAuthError",
    "PUT_BATCH_SIZE",
    "QOS_NAMESPACE",
    "build_qos_metric_data",
    "fetch_counter_rows",
    "fetch_counter_schemas",
    "fetch_workloads",
    "invalidate_credentials",
    "lambda_handler",
]

QOS_NAMESPACE = "FSxONTAP/QoS"
DEFAULT_MAX_POLICY_GROUPS = 200  # per-run cap on policy groups that get series

# Counter name -> (metric name, CloudWatch unit). Only these counters are
# published; everything else on the row (concurrency, labels) is ignored.
_COUNTER_METRICS: dict[str, tuple[str, str]] = {
    "ops": ("QosOps", "Count/Second"),
    "total_data": ("QosThroughputBytes", "Bytes/Second"),
    "latency": ("QosLatencyMicros", "Microseconds"),
    "read_ops": ("QosReadOps", "Count/Second"),
    "write_ops": ("QosWriteOps", "Count/Second"),
}
# Counters whose point-sample publication is honest only when the schema marks
# them rate/average; a raw-only value here sets the point_sample summary flag.
_RATE_EXPECTED = frozenset({"ops", "total_data", "read_ops", "write_ops"})


def fetch_counter_schemas(
    *,
    retries: urllib3.util.Retry | None = None,
    credentials_ttl: float | None = None,
) -> dict[str, dict[str, str]]:
    """Read the QoS counter table schema: each counter's type and unit.

    Args:
        retries: Passed to qtree_quota_poller._ontap_get.
        credentials_ttl: Passed to qtree_quota_poller._ontap_get.

    Returns:
        Mapping of counter name to ``{"type": ..., "unit": ...}``. Empty when
        the table reports no counter_schemas.
    """
    data = ontap._ontap_get(
        "/api/cluster/counter/tables/qos?fields=counter_schemas",
        retries=retries,
        credentials_ttl=credentials_ttl,
    )
    schemas: dict[str, dict[str, str]] = {}
    for record in data.get("records", []):
        for schema in record.get("counter_schemas", []):
            name = schema.get("name")
            if isinstance(name, str):
                schemas[name] = {
                    "type": str(schema.get("type", "")),
                    "unit": str(schema.get("unit", "")),
                }
    # Tables may return a single record without a records wrapper.
    for schema in data.get("counter_schemas", []):
        name = schema.get("name")
        if isinstance(name, str):
            schemas[name] = {
                "type": str(schema.get("type", "")),
                "unit": str(schema.get("unit", "")),
            }
    return schemas


def _paginate(
    first_href: str,
    *,
    retries: urllib3.util.Retry | None,
    credentials_ttl: float | None,
) -> tuple[list[dict[str, Any]], int, bool]:
    """Read every record of a collection, following _links.next.href.

    Args:
        first_href: First request path (starts with ``/api/``).
        retries: Passed to qtree_quota_poller._ontap_get.
        credentials_ttl: Passed to qtree_quota_poller._ontap_get.

    Returns:
        (records, pages_read, page_truncated). page_truncated is True only
        when a next link is still present after MAX_PAGES pages.
    """
    next_href: str | None = first_href
    records: list[dict[str, Any]] = []
    pages = 0
    while next_href is not None and pages < MAX_PAGES:
        data = ontap._ontap_get(next_href, retries=retries, credentials_ttl=credentials_ttl)
        pages += 1
        records.extend(data.get("records", []))
        next_href = data.get("_links", {}).get("next", {}).get("href")
    return records, pages, next_href is not None


def fetch_workloads(
    *,
    retries: urllib3.util.Retry | None = None,
    credentials_ttl: float | None = None,
) -> list[dict[str, Any]]:
    """Read the QoS workload -> policy-group mapping.

    Args:
        retries: Passed to qtree_quota_poller._ontap_get.
        credentials_ttl: Passed to qtree_quota_poller._ontap_get.

    Returns:
        Workload records with ``name``, ``policy.name``, ``svm.name`` and
        ``volume`` where present.
    """
    records, pages, truncated = _paginate(
        f"/api/storage/qos/workloads?fields=*&max_records={MAX_RECORDS}",
        retries=retries,
        credentials_ttl=credentials_ttl,
    )
    if truncated:
        logger.warning(
            "QoS workload list truncated: stopped after %d pages (%d workloads) "
            "at the %d-page cap; some policy groups may not resolve an SvmName",
            pages, len(records), MAX_PAGES,
        )
    return records


def fetch_counter_rows(
    *,
    retries: urllib3.util.Retry | None = None,
    credentials_ttl: float | None = None,
) -> tuple[list[dict[str, Any]], int, bool]:
    """Read the QoS counter-manager rows (live per-policy-group performance).

    Args:
        retries: Passed to qtree_quota_poller._ontap_get.
        credentials_ttl: Passed to qtree_quota_poller._ontap_get.

    Returns:
        (rows, pages_read, page_truncated). Each row has an ``id`` of the form
        ``<policy>:<uuid>`` plus ``counters`` and ``properties`` lists.
    """
    rows, pages, truncated = _paginate(
        f"/api/cluster/counter/tables/qos/rows?fields=id,counters,properties&max_records={MAX_RECORDS}",
        retries=retries,
        credentials_ttl=credentials_ttl,
    )
    if truncated:
        logger.warning(
            "QoS counter rows truncated: stopped after %d pages (%d rows) at "
            "the %d-page cap; policy groups beyond it are not monitored this run",
            pages, len(rows), MAX_PAGES,
        )
    return rows, pages, truncated


def _named_values(items: Any) -> dict[str, Any]:
    """Collapse a counter-manager ``counters`` or ``properties`` list to a dict.

    Args:
        items: A list of ``{"name": ..., "value": ...}`` objects, or anything
            else (treated as empty).

    Returns:
        Mapping of name to value for every well-formed entry.
    """
    out: dict[str, Any] = {}
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                out[item["name"]] = item.get("value")
    return out


def _svm_name_map(workloads: list[dict[str, Any]]) -> dict[str, str]:
    """Build policy-group name -> SVM name from the workload records.

    Args:
        workloads: Records from fetch_workloads.

    Returns:
        Mapping of policy-group name to SVM name for every workload that names
        both. A policy group spanning SVMs keeps the first SVM seen.
    """
    mapping: dict[str, str] = {}
    for workload in workloads:
        policy = workload.get("policy", {})
        svm = workload.get("svm", {})
        policy_name = policy.get("name") if isinstance(policy, dict) else None
        svm_name = svm.get("name") if isinstance(svm, dict) else None
        if isinstance(policy_name, str) and isinstance(svm_name, str):
            mapping.setdefault(policy_name, svm_name)
    return mapping


def _policy_group_name(row: dict[str, Any], properties: dict[str, Any]) -> str:
    """Resolve a row's policy-group name from properties, then the row id.

    Args:
        row: A counter-manager row.
        properties: The row's collapsed ``properties`` dict.

    Returns:
        The policy-group name, or ``""`` when neither source carries one.
    """
    name = properties.get("policy_group_name")
    if isinstance(name, str) and name:
        return name
    row_id = row.get("id")
    if isinstance(row_id, str) and ":" in row_id:
        return row_id.rsplit(":", 1)[0]
    if isinstance(row_id, str):
        return row_id
    return ""


def build_qos_metric_data(
    rows: list[dict[str, Any]],
    workloads: list[dict[str, Any]],
    schemas: dict[str, dict[str, str]],
    file_system_id: str,
    max_policy_groups: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build the QoS datums described in the module docstring.

    Args:
        rows: Counter-manager rows from fetch_counter_rows.
        workloads: Workload records from fetch_workloads (SvmName resolution).
        schemas: Counter schema map from fetch_counter_schemas.
        file_system_id: Value of the FileSystemId dimension.
        max_policy_groups: Cap on policy groups that get per-group series.

    Returns:
        (metric_data, summary). summary has ``policy_groups``,
        ``series_policy_groups``, ``truncated`` and ``point_sample``.
    """
    fs_dimensions = [{"Name": "FileSystemId", "Value": file_system_id}]
    svm_map = _svm_name_map(workloads)
    metric_data: list[dict[str, Any]] = []
    series = 0
    capped = False
    nameless = 0
    point_sample = False

    for row in rows:
        counters = _named_values(row.get("counters"))
        properties = _named_values(row.get("properties"))
        policy_group = _policy_group_name(row, properties)
        if not policy_group:
            nameless += 1
            logger.warning(
                "QoS counter row %r has no resolvable policy-group name; skipped",
                row.get("id"),
            )
            continue
        if series >= max_policy_groups:
            capped = True
            continue

        dimensions = [
            {"Name": "FileSystemId", "Value": file_system_id},
            {"Name": "PolicyGroup", "Value": policy_group},
        ]
        svm_name = svm_map.get(policy_group)
        if isinstance(svm_name, str) and svm_name:
            dimensions.append({"Name": "SvmName", "Value": svm_name})

        row_datums: list[dict[str, Any]] = []
        for counter_name, (metric_name, unit) in _COUNTER_METRICS.items():
            if counter_name not in counters:
                continue
            value = counters[counter_name]
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            counter_type = schemas.get(counter_name, {}).get("type", "")
            if counter_name in _RATE_EXPECTED and counter_type not in ("rate", "average"):
                # Raw/delta where a rate was expected: the published value is a
                # cumulative point sample, not a per-poll rate. Flagged, never
                # differenced across polls (stateless per invocation).
                point_sample = True
            row_datums.append({
                "MetricName": metric_name,
                "Dimensions": dimensions,
                "Value": float(value),
                "Unit": unit,
            })
        if not row_datums:
            logger.warning(
                "QoS policy group %s has no publishable counters this run", policy_group
            )
            continue
        series += 1
        metric_data.extend(row_datums)

    truncated = capped or nameless > 0
    if capped:
        logger.warning(
            "QoS per-policy-group series capped at %d of %d rows; aggregates "
            "reported as truncated",
            max_policy_groups, len(rows),
        )

    metric_data.append({
        "MetricName": "QosPolicyGroupsTruncated",
        "Dimensions": fs_dimensions,
        "Value": 1.0 if truncated else 0.0,
        "Unit": "Count",
    })

    summary = {
        "policy_groups": len(rows),
        "series_policy_groups": series,
        "truncated": truncated,
        "point_sample": point_sample,
    }
    logger.info(
        "QoS on %s: %d counter row(s), %d with per-policy-group series, "
        "point_sample=%s, truncated=%s",
        file_system_id, len(rows), series, point_sample, truncated,
    )
    return metric_data, summary


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """CloudFormation entry point (index.lambda_handler): one file system's QoS.

    Mirrors qtree_quota_poller.lambda_handler: no urllib3 retries, credentials
    cached for the container lifetime, no heartbeat metric. Reads the file
    system id, namespace and the optional policy-group cap from the environment.

    Args:
        event: EventBridge scheduled event (unused).
        context: Lambda context (unused).

    Returns:
        A run summary.
    """
    del event, context
    file_system_id = os.environ["FILE_SYSTEM_ID"]
    namespace = os.environ.get("METRIC_NAMESPACE", QOS_NAMESPACE)
    max_policy_groups = int(
        os.environ.get("QOS_MAX_POLICY_GROUPS", str(DEFAULT_MAX_POLICY_GROUPS))
    )

    schemas = fetch_counter_schemas()
    workloads = fetch_workloads()
    rows, pages, _page_truncated = fetch_counter_rows()
    metric_data, summary = build_qos_metric_data(
        rows, workloads, schemas, file_system_id, max_policy_groups
    )
    published = ontap._put_metric_data(namespace, metric_data)

    logger.info("Published %d QoS metric data points", published)
    return {**summary, "pages_read": pages, "metrics_published": published}
