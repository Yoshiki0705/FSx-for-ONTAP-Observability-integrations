"""Every metric in the monitoring dashboard template uses a documented dimension set.

CloudWatch matches a metric only when the alarm or widget names exactly the
dimensions the series was published with. A selection with too few
dimensions matches nothing: the widget stays empty and the alarm never fires,
with no error at deploy time. ``StorageCapacityAlarm`` once selected
``StorageCapacityUtilization`` with ``FileSystemId`` alone, which AWS does not
document for that metric.

The table below is encoded from the AWS docs (raw HTML, retrieved 2026-10-05):

- F1 https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/file-system-metrics.html
- F2 https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/so-file-system-metrics.html
- F3 https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/monitoring-cloudwatch.html
  (namespace ``AWS/FSx``)

Volume-level dimension sets are not encoded; this template has no volume
metric. The Terraform module ``terraform/fsxn-monitoring-dashboard`` must use
the same capacity dimension set, which the last tests check.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
TEMPLATE = ROOT / "shared/templates/fsxn-monitoring-dashboard.yaml"
MAIN_TF = ROOT / "terraform/fsxn-monitoring-dashboard/main.tf"

_MIN_MAX_AVG = frozenset({"Average", "Minimum", "Maximum"})
_ALL_STATS = frozenset({"Sum", "Average", "Minimum", "Maximum"})
_FS = frozenset({"FileSystemId"})
_FS_SERVER = frozenset({"FileSystemId", "FileServer"})
_FS_TIER = frozenset({"FileSystemId", "StorageTier", "DataType"})
_FS_TIER_AGGR = frozenset({"FileSystemId", "StorageTier", "DataType", "Aggregate"})

# metric -> {dimension-key set -> valid statistics} (F1, F2; retrieved 2026-10-05)
DOCUMENTED: dict[str, dict[frozenset[str], frozenset[str]]] = {
    "DataReadBytes": {_FS: frozenset({"Sum"})},
    "DataWriteBytes": {_FS: frozenset({"Sum"})},
    "DataReadOperations": {_FS: frozenset({"Sum"})},
    "DataWriteOperations": {_FS: frozenset({"Sum"})},
    "NetworkSentBytes": {_FS: frozenset({"Sum"}), _FS_SERVER: _ALL_STATS},
    "NetworkReceivedBytes": {_FS: frozenset({"Sum"}), _FS_SERVER: _ALL_STATS},
    "NetworkThroughputUtilization": {_FS: _MIN_MAX_AVG, _FS_SERVER: _MIN_MAX_AVG},
    "StorageUsed": {_FS: _MIN_MAX_AVG, _FS_TIER: _MIN_MAX_AVG, _FS_TIER_AGGR: _MIN_MAX_AVG},
    # F1 lists Maximum only without Aggregate; F2 lists all three with it.
    "StorageCapacity": {_FS_TIER: frozenset({"Maximum"}), _FS_TIER_AGGR: _MIN_MAX_AVG},
    # File-system level. {FileSystemId} alone is NOT documented for this metric.
    "StorageCapacityUtilization": {_FS_TIER: _MIN_MAX_AVG, _FS_TIER_AGGR: _MIN_MAX_AVG},
}

DIMENSION_VALUES: dict[str, set[str]] = {
    "StorageTier": {"SSD", "StandardCapacityPool"},
    "DataType": {"All"},
}


class _CfnLoader(yaml.SafeLoader):
    """SafeLoader that turns any ``!Tag`` into ``{"Tag": value}``."""


def _cfn_multi(loader: yaml.SafeLoader, tag_suffix: str, node: yaml.Node) -> dict[str, Any]:
    if isinstance(node, yaml.ScalarNode):
        return {tag_suffix: loader.construct_scalar(node)}
    if isinstance(node, yaml.SequenceNode):
        return {tag_suffix: loader.construct_sequence(node, deep=True)}
    return {tag_suffix: loader.construct_mapping(node, deep=True)}  # type: ignore[arg-type]


_CfnLoader.add_multi_constructor("!", _cfn_multi)


def _load_template() -> dict[str, Any]:
    return yaml.load(TEMPLATE.read_text(encoding="utf-8"), Loader=_CfnLoader)  # noqa: S506


def undocumented(metric: str, dims: dict[str, Any], stat: str) -> str | None:
    """Return why (metric, dims, stat) is not documented, or None if it is."""
    sets = DOCUMENTED.get(metric)
    if sets is None:
        return f"{metric}: metric not in the documented table"
    keys = frozenset(dims)
    if keys not in sets:
        return f"{metric}: dimension keys {sorted(keys)} not documented; documented: {[sorted(s) for s in sets]}"
    if stat not in sets[keys]:
        return f"{metric}: statistic {stat} not documented for {sorted(keys)}"
    for name, value in dims.items():
        if name == "FileSystemId":
            continue
        allowed = DIMENSION_VALUES.get(name)
        if allowed is None:
            continue
        if not isinstance(value, str) or value not in allowed:
            return f"{metric}: {name}={value!r} not in documented values {sorted(allowed)}"
    return None


def _alarms(template: dict[str, Any]) -> list[tuple[str, str, dict[str, Any], str]]:
    out = []
    for logical_id, res in template["Resources"].items():
        if res["Type"] != "AWS::CloudWatch::Alarm":
            continue
        props = res["Properties"]
        assert props["Namespace"] == "AWS/FSx", logical_id
        dims = {d["Name"]: d["Value"] for d in props["Dimensions"]}
        out.append((logical_id, props["MetricName"], dims, props["Statistic"]))
    return out


def _widget_metrics(template: dict[str, Any]) -> list[tuple[str, str, dict[str, Any], str]]:
    body = template["Resources"]["MonitoringDashboard"]["Properties"]["DashboardBody"]["Sub"]
    # The only substitution outside a JSON string is the numeric annotation.
    body = body.replace("${CapacityThresholdPercent}", "80")
    body = re.sub(r"\$\{[^}]+\}", "PLACEHOLDER", body)
    rows = []
    for widget in json.loads(body)["widgets"]:
        if widget["type"] != "metric":
            continue
        title = widget["properties"]["title"]
        for row in widget["properties"]["metrics"]:
            if isinstance(row[0], dict):  # metric-math expression row
                continue
            assert "." not in row and "..." not in row, (
                f"{title}: shorthand rows are not parsed: {row}"
            )
            namespace, name, *rest = row
            assert namespace == "AWS/FSx", title
            options = rest.pop() if rest and isinstance(rest[-1], dict) else {}
            assert len(rest) % 2 == 0, f"{title}: odd dimension list {rest}"
            dims = dict(zip(rest[::2], rest[1::2], strict=True))
            rows.append((title, name, dims, options.get("stat", "Average")))
    return rows


_TEMPLATE = _load_template()
ALARMS = _alarms(_TEMPLATE)
WIDGET_METRICS = _widget_metrics(_TEMPLATE)


@pytest.mark.parametrize(
    ("logical_id", "metric", "dims", "stat"), ALARMS, ids=[a[0] for a in ALARMS]
)
def test_alarm_uses_documented_dimensions(
    logical_id: str, metric: str, dims: dict[str, Any], stat: str
) -> None:
    assert undocumented(metric, dims, stat) is None, logical_id


@pytest.mark.parametrize(
    ("title", "metric", "dims", "stat"),
    WIDGET_METRICS,
    ids=[f"{w[0]}::{w[1]}" for w in WIDGET_METRICS],
)
def test_widget_metric_uses_documented_dimensions(
    title: str, metric: str, dims: dict[str, Any], stat: str
) -> None:
    assert undocumented(metric, dims, stat) is None, title


def test_iteration_is_not_empty() -> None:
    assert len(ALARMS) == 2
    assert len(WIDGET_METRICS) >= 9


def test_checker_rejects_filesystemid_only_capacity() -> None:
    assert undocumented(
        "StorageCapacityUtilization", {"FileSystemId": {"Ref": "FileSystemId"}}, "Average"
    )
    assert (
        undocumented(
            "StorageCapacityUtilization",
            {"FileSystemId": {"Ref": "FileSystemId"}, "StorageTier": "SSD", "DataType": "All"},
            "Average",
        )
        is None
    )
    assert undocumented(
        "StorageCapacityUtilization",
        {"FileSystemId": {"Ref": "FileSystemId"}, "StorageTier": "Ssd", "DataType": "All"},
        "Average",
    )


def _terraform_capacity_dimensions() -> dict[str, str]:
    text = MAIN_TF.read_text(encoding="utf-8")
    match = re.search(r"capacity_dimensions\s*=\s*\{(.*?)\}", text, re.DOTALL)
    assert match, f"capacity_dimensions block not found in {MAIN_TF}"
    pairs = re.findall(r"^\s*(\w+)\s*=\s*(\"[^\"]*\"|[\w.]+)\s*$", match.group(1), re.MULTILINE)
    assert pairs, "capacity_dimensions block has no key = value lines"
    return {k: v.strip('"') for k, v in pairs}


def test_cfn_and_terraform_capacity_alarm_dimensions_match() -> None:
    cfn = next(dims for lid, _, dims, _ in ALARMS if lid == "StorageCapacityAlarm")
    tf = _terraform_capacity_dimensions()
    assert set(cfn) == set(tf)
    for key in ("StorageTier", "DataType"):
        assert cfn.get(key) == tf.get(key), key


def test_terraform_capacity_widget_uses_same_dimensions() -> None:
    text = MAIN_TF.read_text(encoding="utf-8")
    expected = (
        '"StorageCapacityUtilization", "FileSystemId", var.file_system_id, '
        '"StorageTier", "SSD", "DataType", "All"'
    )
    assert expected in text, "Terraform capacity widget row not found"
    cfn = next(dims for _, name, dims, _ in WIDGET_METRICS if name == "StorageCapacityUtilization")
    assert cfn == {"FileSystemId": "PLACEHOLDER", "StorageTier": "SSD", "DataType": "All"}
