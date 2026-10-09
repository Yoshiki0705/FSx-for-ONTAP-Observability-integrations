"""SSD utilization for the report values, read with ``cloudwatch:GetMetricData``.

The design (docs/en/capacity-automation-t4-design.md, "IAM permissions") gives
the function ``cloudwatch:GetMetricData`` for the report values. The trigger
alarm's own evaluation decides whether to act (the alarm-state guard reads
``DescribeAlarms``); this module only reads the value that the report, the
decision log and the archive carry. It is a report input, not a guard, so a
missing value is classified and reported and does not stop the evaluation.

The query reads the same series as the trigger alarms in
terraform/fsxn-ssd-auto-increase/main.tf: ``AWS/FSx``
``StorageCapacityUtilization`` with ``FileSystemId`` + ``StorageTier=SSD`` +
``DataType=All`` for the file system, plus one series per configured
``Aggregate`` (the second-generation per-aggregate alarms) with the
``Aggregate`` dimension added. Statistic and period match the alarms
(``Average``, 300 seconds).

The result is never a bare null. Each series carries a ``status``:

- ``ok``: a datapoint was returned; ``value`` and ``timestamp`` are the latest.
- ``no_datapoints``: the series returned no datapoint in the look-back window.
- ``series_error``: CloudWatch returned the series with ``StatusCode``
  ``InternalError`` or ``Forbidden``.
- ``missing_result``: the response had no result for the query.
- ``query_failed``: ``GetMetricData`` itself raised (a service error or a
  transport error), so no series was read.

The overall ``status`` is ``ok`` when every series is ``ok``, ``incomplete``
when at least one is not, and ``query_failed`` when the call raised. Anything
other than ``ok`` also carries a ``detail`` naming the series and the cause.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

NAMESPACE = "AWS/FSx"
METRIC_NAME = "StorageCapacityUtilization"
# Must match statistic and period on aws_cloudwatch_metric_alarm.trigger and
# trigger_aggregate in terraform/fsxn-ssd-auto-increase/main.tf, so the report
# value is the number the alarm evaluates.
STATISTIC = "Average"
PERIOD_SECONDS = 300
# Six alarm periods, so the latest datapoint is still found when it lands a
# few minutes late. ScanBy=TimestampDescending puts the latest one first.
LOOKBACK_SECONDS = 6 * PERIOD_SECONDS

FILE_SYSTEM_QUERY_ID = "fs"
_SERIES_ERROR_CODES = frozenset({"InternalError", "Forbidden"})


def series_dimensions(file_system_id: str, aggregate: str | None = None) -> list[dict[str, str]]:
    """Return the dimensions of one utilization series.

    Args:
        file_system_id: The managed file system ID.
        aggregate: An Aggregate name for a per-aggregate series, or ``None``
            for the file-system series.

    Returns:
        The ``Dimensions`` list for ``MetricStat.Metric``.
    """
    dimensions = [
        {"Name": "FileSystemId", "Value": file_system_id},
        {"Name": "StorageTier", "Value": "SSD"},
        {"Name": "DataType", "Value": "All"},
    ]
    if aggregate is not None:
        dimensions.append({"Name": "Aggregate", "Value": aggregate})
    return dimensions


def _queries(
    file_system_id: str, aggregate_names: tuple[str, ...]
) -> list[tuple[str, str | None, dict[str, Any]]]:
    """Build (query ID, aggregate name or None, MetricDataQuery) triples.

    Query IDs must start with a lowercase letter and hold only letters, digits
    and underscores, so aggregates are numbered (``agg0``, ``agg1``) rather
    than named after the aggregate.
    """
    entries: list[tuple[str, str | None]] = [(FILE_SYSTEM_QUERY_ID, None)]
    entries += [(f"agg{index}", name) for index, name in enumerate(aggregate_names)]
    return [
        (
            query_id,
            aggregate,
            {
                "Id": query_id,
                "MetricStat": {
                    "Metric": {
                        "Namespace": NAMESPACE,
                        "MetricName": METRIC_NAME,
                        "Dimensions": series_dimensions(file_system_id, aggregate),
                    },
                    "Period": PERIOD_SECONDS,
                    "Stat": STATISTIC,
                },
                "ReturnData": True,
            },
        )
        for query_id, aggregate in entries
    ]


def _series(result: dict[str, Any] | None) -> dict[str, Any]:
    """Classify one MetricDataResult."""
    if result is None:
        return {"status": "missing_result"}
    status_code = result.get("StatusCode")
    values = result.get("Values") or []
    timestamps = result.get("Timestamps") or []
    if values:
        series: dict[str, Any] = {"status": "ok", "value": float(values[0])}
        if timestamps and isinstance(timestamps[0], datetime):
            series["timestamp"] = timestamps[0].isoformat()
        if status_code is not None:
            series["status_code"] = status_code
        return series
    if status_code in _SERIES_ERROR_CODES:
        return {"status": "series_error", "status_code": status_code}
    series = {"status": "no_datapoints"}
    if status_code is not None:
        series["status_code"] = status_code
    return series


def _label(aggregate: str | None) -> str:
    return "file_system" if aggregate is None else f"aggregate {aggregate}"


def read_utilization(
    cloudwatch: Any,
    file_system_id: str,
    aggregate_names: tuple[str, ...],
    now: datetime,
) -> dict[str, Any]:
    """Read the latest SSD utilization of the file system and each aggregate.

    Args:
        cloudwatch: A boto3 CloudWatch client.
        file_system_id: The managed file system ID.
        aggregate_names: Configured second-generation Aggregate names.
        now: The evaluation time; the window ends here.

    Returns:
        A dictionary with ``status``, ``metric``, ``statistic``,
        ``period_seconds``, ``file_system`` (one classified series),
        ``aggregates`` (aggregate name to classified series) and, when
        ``status`` is not ``ok``, ``detail``.
    """
    queries = _queries(file_system_id, aggregate_names)
    base: dict[str, Any] = {
        "metric": f"{NAMESPACE} {METRIC_NAME}",
        "statistic": STATISTIC,
        "period_seconds": PERIOD_SECONDS,
    }
    try:
        response = cloudwatch.get_metric_data(
            MetricDataQueries=[query for _, _, query in queries],
            StartTime=now - timedelta(seconds=LOOKBACK_SECONDS),
            EndTime=now,
            ScanBy="TimestampDescending",
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "unknown")
        return _failed(base, aggregate_names, f"GetMetricData failed: {code}")
    except BotoCoreError as exc:
        return _failed(base, aggregate_names, f"GetMetricData failed: {type(exc).__name__}")

    results = {r.get("Id"): r for r in response.get("MetricDataResults", [])}
    file_system = _series(results.get(FILE_SYSTEM_QUERY_ID))
    aggregates = {
        aggregate: _series(results.get(query_id))
        for query_id, aggregate, _ in queries
        if aggregate is not None
    }
    gaps = [
        f"{_label(aggregate)}: {series['status']}"
        + (f" ({series['status_code']})" if "status_code" in series else "")
        for aggregate, series in [(None, file_system), *aggregates.items()]
        if series["status"] != "ok"
    ]
    reading: dict[str, Any] = {
        **base,
        "status": "ok" if not gaps else "incomplete",
        "file_system": file_system,
        "aggregates": aggregates,
    }
    if gaps:
        reading["detail"] = "; ".join(gaps)
    return reading


def _failed(
    base: dict[str, Any], aggregate_names: tuple[str, ...], detail: str
) -> dict[str, Any]:
    return {
        **base,
        "status": "query_failed",
        "detail": detail,
        "file_system": {"status": "query_failed"},
        "aggregates": {name: {"status": "query_failed"} for name in aggregate_names},
    }
