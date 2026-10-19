"""QoS policy-group collector for the ONTAP metrics handler.

Reads live QoS performance from the ONTAP counter manager and the QoS
workload -> policy-group mapping, then builds CloudWatch datums in namespace
FSxONTAP/QoS. The counter manager is the only REST route to live QoS
performance: GET /api/storage/qos/workloads/{uuid} has no ``statistics``
field and /api/private/cli/qos/statistics returns 405.

Per policy group, for at most max_policy_groups of them (dimensions
FileSystemId, PolicyGroup; SvmName is added only when the workload map
resolves one for that policy group):
  - QosOps                 effective IOPS, from counter ``ops``
  - QosThroughputBytes     effective throughput, from counter ``total_data``
  - QosLatencyMicros       interval-average latency (see below)
  - QosReadOps/QosWriteOps read/write IOPS split, from ``read_ops``/``write_ops``
    (emitted only for the counters present on the row)

Per file system, once per run (dimension FileSystemId):
  - QosPolicyGroupsTruncated: 1 when the page cap stopped the read with a
    next link still present, or the per-policy-group cap dropped a group;
    else 0.

What this exposes is effective throughput/IOPS per policy group. There is no
burst-specific counter in the counter manager, so "pinned at the ceiling" is
visible from these series but "currently bursting" is inferred, not measured.

Counter honesty (two-sample delta rate). The counter manager marks the
counters this collector publishes as ``type: rate`` (ops, total_data,
read_ops, write_ops) and ``latency`` as ``type: average`` with a denominator
of ``ops``. The ``value`` the table returns for such a counter is NOT a
per-second instantaneous rate: it is a CUMULATIVE RAW counter that increases
monotonically since boot. Publishing that value directly produced impossible
CloudWatch datapoints (e.g. QosOps in the hundreds of millions Count/Second),
confirmed on a live ONTAP 9.19.1P2 file system where _USERSPACE_APPS ``ops``
increased monotonically across 20 s samples.

So the collector samples the counter-manager rows TWICE within one invocation,
a short interval apart (QOS_SAMPLE_INTERVAL_SECONDS, default 10 s), and
publishes the per-second rate as the delta over the measured elapsed time:

  rate = (value_v2 - value_v1) / elapsed_seconds

For latency (an ``average`` counter accumulating total latency over ops), the
interval-average is the delta of the latency accumulator over the delta of
ops:

  latency = (latency_v2 - latency_v1) / (ops_v2 - ops_v1) microseconds

The elapsed time is measured with time.monotonic() across the real sleep, not
assumed equal to the requested interval. The collector keeps NO state ACROSS
invocations (like the qtree poller); it differences two samples WITHIN a
single invocation. A counter reset or rollover (value_v2 < value_v1), a
non-positive elapsed (clock anomaly), an ops delta of 0 for latency, or a
policy group present in only one of the two samples all cause that counter to
be skipped for that run rather than publishing a garbage value.

This is a short sample-window rate (default 10 s), not a 15-minute-interval
average, and bursting is still inferred, not measured: there is no
burst-specific counter.

This module is not part of a CloudFormation template on its own; the QoS CFN
template concatenates this source with the shared client.
"""
import logging
import os
import time
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
    "sample_counter_rows_twice",
]

QOS_NAMESPACE = "FSxONTAP/QoS"
DEFAULT_MAX_POLICY_GROUPS = 200  # per-run cap on policy groups that get series
DEFAULT_SAMPLE_INTERVAL_SECONDS = 10.0  # sleep between the two counter samples

# Counter name -> (metric name, CloudWatch unit). Only these counters are
# published; everything else on the row (concurrency, labels) is ignored.
_COUNTER_METRICS: dict[str, tuple[str, str]] = {
    "ops": ("QosOps", "Count/Second"),
    "total_data": ("QosThroughputBytes", "Bytes/Second"),
    "latency": ("QosLatencyMicros", "Microseconds"),
    "read_ops": ("QosReadOps", "Count/Second"),
    "write_ops": ("QosWriteOps", "Count/Second"),
}
# Cumulative rate-typed counters: published as (v2 - v1) / elapsed_seconds.
# latency is handled separately as an interval-average (delta/delta of ops).
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
        ``<policy>:<uuid>`` plus ``counters`` and ``properties`` lists. The
        counters are CUMULATIVE RAW values; a single read is not a rate (see
        the module docstring and sample_counter_rows_twice).
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


def sample_counter_rows_twice(
    interval_seconds: float,
    *,
    retries: urllib3.util.Retry | None = None,
    credentials_ttl: float | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], float, int, bool]:
    """Read the counter-manager rows twice, a short sleep apart.

    The counter values are cumulative raw counters, so a per-second rate needs
    two samples differenced over the elapsed time. This reads the rows, sleeps
    ``interval_seconds``, reads them again, and reports the ACTUAL elapsed time
    measured with time.monotonic() (the sleep is not assumed exact).

    Args:
        interval_seconds: Requested sleep between the two samples.
        retries: Passed to qtree_quota_poller._ontap_get.
        credentials_ttl: Passed to qtree_quota_poller._ontap_get.

    Returns:
        (rows_v1, rows_v2, elapsed_seconds, pages_read, page_truncated).
        pages_read is the total across both samples; page_truncated is True
        when either sample hit the page cap.
    """
    rows_v1, pages_v1, trunc_v1 = fetch_counter_rows(
        retries=retries, credentials_ttl=credentials_ttl
    )
    start = time.monotonic()
    time.sleep(interval_seconds)
    rows_v2, pages_v2, trunc_v2 = fetch_counter_rows(
        retries=retries, credentials_ttl=credentials_ttl
    )
    elapsed = time.monotonic() - start
    return rows_v1, rows_v2, elapsed, pages_v1 + pages_v2, trunc_v1 or trunc_v2


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


def _row_identity(row: dict[str, Any], properties: dict[str, Any]) -> str:
    """Return the stable per-policy-group identity used to match two samples.

    The row id (``<policy>:<uuid>``) is preferred because it carries the
    policy-group UUID; the policy-group name is the fallback. Matching by this
    identity, not by list position, is required because row order is not
    guaranteed stable across two counter-manager reads.

    Args:
        row: A counter-manager row.
        properties: The row's collapsed ``properties`` dict.

    Returns:
        The row id, else the policy_group_uuid/name from properties, else "".
    """
    row_id = row.get("id")
    if isinstance(row_id, str) and row_id:
        return row_id
    uuid = properties.get("policy_group_uuid")
    if isinstance(uuid, str) and uuid:
        return uuid
    return _policy_group_name(row, properties)


def _index_rows(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Index rows by their matching identity for sample-to-sample lookup.

    Args:
        rows: Counter-manager rows from one sample.

    Returns:
        Mapping of row identity to the row. A row with no resolvable identity
        is skipped (it cannot be matched to the other sample).
    """
    index: dict[str, dict[str, Any]] = {}
    for row in rows:
        properties = _named_values(row.get("properties"))
        identity = _row_identity(row, properties)
        if identity:
            index[identity] = row
    return index


def build_qos_metric_data(
    rows_v1: list[dict[str, Any]],
    rows_v2: list[dict[str, Any]],
    elapsed_seconds: float,
    workloads: list[dict[str, Any]],
    schemas: dict[str, dict[str, str]],
    file_system_id: str,
    max_policy_groups: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build the QoS datums described in the module docstring from two samples.

    Rate counters are published as (v2 - v1) / elapsed_seconds; latency is the
    interval-average (delta latency / delta ops). A counter reset (v2 < v1),
    an ops delta of 0 for latency, a non-positive elapsed, or a policy group
    present in only one sample is skipped rather than fabricated.

    Args:
        rows_v1: First counter-manager sample.
        rows_v2: Second counter-manager sample (same policy groups, later).
        elapsed_seconds: Measured seconds between the two samples.
        workloads: Workload records from fetch_workloads (SvmName resolution).
        schemas: Counter schema map from fetch_counter_schemas (unused for
            value math now; kept for the signature and future honesty checks).
        file_system_id: Value of the FileSystemId dimension.
        max_policy_groups: Cap on policy groups that get per-group series.

    Returns:
        (metric_data, summary). summary has ``policy_groups``,
        ``series_policy_groups``, ``rate_policy_groups``, ``truncated`` and
        ``sample_interval_seconds``.
    """
    del schemas  # counter type is known (rate/average); kept for signature
    fs_dimensions = [{"Name": "FileSystemId", "Value": file_system_id}]
    svm_map = _svm_name_map(workloads)
    index_v2 = _index_rows(rows_v2)
    metric_data: list[dict[str, Any]] = []
    series = 0
    rate_groups = 0
    capped = False
    nameless = 0

    elapsed_ok = elapsed_seconds > 0
    if not elapsed_ok:
        logger.warning(
            "QoS elapsed between samples is %.6fs (<= 0, clock anomaly); "
            "rate counters are skipped this run",
            elapsed_seconds,
        )

    for row in rows_v1:
        counters_v1 = _named_values(row.get("counters"))
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

        identity = _row_identity(row, properties)
        row_v2 = index_v2.get(identity)
        if row_v2 is None:
            logger.info(
                "QoS policy group %s present in sample 1 but missing in "
                "sample 2; skipped for rate counters this run",
                policy_group,
            )
            continue
        counters_v2 = _named_values(row_v2.get("counters"))

        dimensions = [
            {"Name": "FileSystemId", "Value": file_system_id},
            {"Name": "PolicyGroup", "Value": policy_group},
        ]
        svm_name = svm_map.get(policy_group)
        if isinstance(svm_name, str) and svm_name:
            dimensions.append({"Name": "SvmName", "Value": svm_name})

        row_datums: list[dict[str, Any]] = []
        ops_delta = _counter_delta(counters_v1, counters_v2, "ops")

        for counter_name in ("ops", "total_data", "read_ops", "write_ops"):
            metric_name, unit = _COUNTER_METRICS[counter_name]
            delta = _counter_delta(counters_v1, counters_v2, counter_name)
            if delta is None:
                continue  # counter absent or non-numeric in a sample
            if delta < 0:
                logger.warning(
                    "QoS counter %s for policy group %s decreased (%s -> %s); "
                    "counter reset or policy group recreated, skipped this run",
                    counter_name, policy_group,
                    counters_v1.get(counter_name), counters_v2.get(counter_name),
                )
                continue
            if not elapsed_ok:
                continue
            row_datums.append({
                "MetricName": metric_name,
                "Dimensions": dimensions,
                "Value": delta / elapsed_seconds,
                "Unit": unit,
            })

        # A clock anomaly (elapsed <= 0) makes this run's samples untrustworthy,
        # so no datum is published for the group this run, latency included.
        if elapsed_ok:
            latency_datum = _latency_datum(
                counters_v1, counters_v2, ops_delta, dimensions, policy_group
            )
            if latency_datum is not None:
                row_datums.append(latency_datum)

        if not row_datums:
            logger.info(
                "QoS policy group %s produced no publishable rate this run",
                policy_group,
            )
            continue
        series += 1
        rate_groups += 1
        metric_data.extend(row_datums)

    truncated = capped or nameless > 0
    if capped:
        logger.warning(
            "QoS per-policy-group series capped at %d of %d rows; aggregates "
            "reported as truncated",
            max_policy_groups, len(rows_v1),
        )

    metric_data.append({
        "MetricName": "QosPolicyGroupsTruncated",
        "Dimensions": fs_dimensions,
        "Value": 1.0 if truncated else 0.0,
        "Unit": "Count",
    })

    summary = {
        "policy_groups": len(rows_v1),
        "series_policy_groups": series,
        "rate_policy_groups": rate_groups,
        "truncated": truncated,
        "sample_interval_seconds": elapsed_seconds,
    }
    logger.info(
        "QoS on %s: %d counter row(s), %d with per-policy-group series, "
        "%d with a valid delta, sample_interval_seconds=%.3f, truncated=%s",
        file_system_id, len(rows_v1), series, rate_groups, elapsed_seconds, truncated,
    )
    return metric_data, summary


def _counter_delta(
    counters_v1: dict[str, Any],
    counters_v2: dict[str, Any],
    name: str,
) -> float | None:
    """Return v2 - v1 for one numeric counter, or None when not comparable.

    Args:
        counters_v1: First sample's collapsed counters.
        counters_v2: Second sample's collapsed counters.
        name: Counter name.

    Returns:
        The float delta, or None when the counter is absent or non-numeric in
        either sample. A negative delta (reset) is returned as-is for the
        caller to handle.
    """
    v1 = counters_v1.get(name)
    v2 = counters_v2.get(name)
    if not _is_number(v1) or not _is_number(v2):
        return None
    return float(v2) - float(v1)


def _latency_datum(
    counters_v1: dict[str, Any],
    counters_v2: dict[str, Any],
    ops_delta: float | None,
    dimensions: list[dict[str, str]],
    policy_group: str,
) -> dict[str, Any] | None:
    """Return the interval-average latency datum, or None when it is skipped.

    latency is an ``average`` counter accumulating total latency over ops, so
    the interval-average is (delta latency) / (delta ops) microseconds. When
    the ops delta is 0 (idle) or negative (reset), latency is skipped: an idle
    policy group has no meaningful latency, and a reset gives a garbage value.

    Args:
        counters_v1: First sample's collapsed counters.
        counters_v2: Second sample's collapsed counters.
        ops_delta: v2 - v1 for ``ops`` (the denominator), or None.
        dimensions: Dimension list for the datum.
        policy_group: Policy-group name (for logging).

    Returns:
        A QosLatencyMicros datum, or None when latency is skipped this run.
    """
    latency_delta = _counter_delta(counters_v1, counters_v2, "latency")
    if latency_delta is None:
        return None
    if ops_delta is None:
        return None
    if ops_delta <= 0:
        logger.debug(
            "QoS policy group %s has ops delta %s (idle or reset); latency "
            "skipped this run",
            policy_group, ops_delta,
        )
        return None
    if latency_delta < 0:
        logger.warning(
            "QoS latency accumulator for policy group %s decreased; counter "
            "reset, latency skipped this run",
            policy_group,
        )
        return None
    metric_name, unit = _COUNTER_METRICS["latency"]
    return {
        "MetricName": metric_name,
        "Dimensions": dimensions,
        "Value": latency_delta / ops_delta,
        "Unit": unit,
    }


def _is_number(value: Any) -> bool:
    """Return True for an int/float that is not a bool."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """CloudFormation entry point (index.lambda_handler): one file system's QoS.

    Mirrors qtree_quota_poller.lambda_handler: no urllib3 retries, credentials
    cached for the container lifetime, no heartbeat metric. Reads the file
    system id, namespace, the optional policy-group cap, and the sample
    interval from the environment, then samples the counter rows twice and
    publishes per-second deltas.

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
    sample_interval = float(
        os.environ.get("QOS_SAMPLE_INTERVAL_SECONDS", str(DEFAULT_SAMPLE_INTERVAL_SECONDS))
    )

    schemas = fetch_counter_schemas()
    workloads = fetch_workloads()
    rows_v1, rows_v2, elapsed, pages, _page_truncated = sample_counter_rows_twice(
        sample_interval
    )
    metric_data, summary = build_qos_metric_data(
        rows_v1, rows_v2, elapsed, workloads, schemas, file_system_id, max_policy_groups
    )
    published = ontap._put_metric_data(namespace, metric_data)

    logger.info("Published %d QoS metric data points", published)
    return {**summary, "pages_read": pages, "metrics_published": published}
