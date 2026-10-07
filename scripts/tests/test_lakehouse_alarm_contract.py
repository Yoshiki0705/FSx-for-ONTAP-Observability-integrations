"""The lakehouse alarms read series that the template text and the doc samples publish.

CloudWatch identifies a custom metric by namespace, name and the complete
dimension set. An alarm that names no dimensions matches only a datum that was
published with no dimensions; a per-entity datum with dimensions is a different
metric and leaves the alarm in INSUFFICIENT_DATA forever, with no error at
deploy time
(https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/AlarmThatSendsEmail.html).
The qtree alarm had this defect once (``QtreeQuotaUsedPercent`` with
``SvmName`` alone); this file guards the same class in
``shared/templates/lakehouse-monitoring.yaml``.

Two defects were found there on 2026-10-07: the doc sample published
``SnapMirrorLagSeconds`` only with ``Source`` + ``Relationship`` dimensions
while the alarm reads it dimensionless, and the template text asked for
``FlexCacheHitRate`` while the alarm reads ``FlexCacheHitRatePercent``.

The template ships no collector, so the contract is checked against what a
reader is told to build: the placeholder text in the template, the deployment
note in the EN doc, and the EN doc's Python samples (the JA blocks are kept
byte-identical by ``shared/scripts/sync-code-blocks.py``).
"""

from __future__ import annotations

import ast
import json
import logging
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "shared/templates/lakehouse-monitoring.yaml"
DOC_EN = ROOT / "docs/en/lakehouse-monitoring-patterns.md"
NAMESPACE = "FSxONTAP/Lakehouse"


class _CfnLoader(yaml.SafeLoader):
    """SafeLoader that turns any ``!Tag`` into ``{"Tag": value}``."""


def _cfn_multi(loader: yaml.SafeLoader, tag_suffix: str, node: yaml.Node) -> dict[str, Any]:
    if isinstance(node, yaml.ScalarNode):
        return {tag_suffix: loader.construct_scalar(node)}
    if isinstance(node, yaml.SequenceNode):
        return {tag_suffix: loader.construct_sequence(node, deep=True)}
    return {tag_suffix: loader.construct_mapping(node, deep=True)}


_CfnLoader.add_multi_constructor("!", _cfn_multi)


def _template() -> dict[str, Any]:
    return yaml.load(TEMPLATE.read_text(encoding="utf-8"), Loader=_CfnLoader)  # noqa: S506


def _lakehouse_alarms() -> dict[str, dict[str, Any]]:
    resources = _template()["Resources"]
    return {
        name: res["Properties"]
        for name, res in resources.items()
        if res["Type"] == "AWS::CloudWatch::Alarm"
        and res["Properties"].get("Namespace") == NAMESPACE
    }


def _alarmed_metrics() -> set[str]:
    return {props["MetricName"] for props in _lakehouse_alarms().values()}


def _python_blocks(markdown: str) -> list[str]:
    return re.findall(r"^```python\n(.*?)^```", markdown, flags=re.MULTILINE | re.DOTALL)


def dimensionless_metric_names(source: str) -> set[str]:
    """Metric names that some dict literal in ``source`` publishes without ``Dimensions``."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Dict):
            continue
        keys = {k.value: v for k, v in zip(node.keys, node.values) if isinstance(k, ast.Constant)}
        metric = keys.get("MetricName")
        if isinstance(metric, ast.Constant) and "Dimensions" not in keys:
            names.add(metric.value)
    return names


def _deployment_note() -> str:
    text = DOC_EN.read_text(encoding="utf-8")
    start = text.index("## Deployment")
    end = text.index("## Prerequisites", start)
    return text[start:end]


def test_template_has_lakehouse_alarms() -> None:
    # Guards the other tests against passing by matching nothing.
    assert _alarmed_metrics() == {
        "SnapMirrorLagSeconds",
        "S3APLatencyP99Ms",
        "FlexCacheHitRatePercent",
        "AccessAnomalyCount",
    }


def test_lakehouse_alarms_have_no_dimensions() -> None:
    with_dims = [name for name, props in _lakehouse_alarms().items() if props.get("Dimensions")]
    assert with_dims == []


def test_template_text_names_every_alarmed_metric() -> None:
    zip_file = _template()["Resources"]["MetricsCollectorFunction"]["Properties"]["Code"]["ZipFile"]
    missing = sorted(m for m in _alarmed_metrics() if m not in zip_file)
    assert missing == []


def test_template_does_not_ask_for_the_wrong_flexcache_name() -> None:
    # The logical ID FlexCacheHitRateAlarm is a resource name, not a metric name.
    text = TEMPLATE.read_text(encoding="utf-8")
    assert re.findall(r"FlexCacheHitRate(?!Percent|Alarm)", text) == []


def test_doc_deployment_note_names_every_alarmed_metric() -> None:
    note = _deployment_note()
    missing = sorted(m for m in _alarmed_metrics() if f"`{m}`" not in note)
    assert missing == []
    assert re.findall(r"FlexCacheHitRate(?!Percent)", note) == []


def test_doc_samples_publish_every_alarmed_metric_dimensionless() -> None:
    published: set[str] = set()
    for block in _python_blocks(DOC_EN.read_text(encoding="utf-8")):
        published |= dimensionless_metric_names(block)
    missing = sorted(_alarmed_metrics() - published)
    assert missing == []


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.status = 200
        self.data = json.dumps(payload).encode()


class _FakeHttp:
    """Answers the list call, then one detail call per cache."""

    def __init__(self, details: dict[str, dict[str, Any]]) -> None:
        self._details = details

    def request(self, method: str, url: str, **_: Any) -> _FakeResponse:
        for uuid, detail in self._details.items():
            if f"/flexcaches/{uuid}" in url:
                return _FakeResponse(detail)
        return _FakeResponse({"records": [{"uuid": uuid} for uuid in self._details]})


def _flexcache_block() -> str:
    blocks = [
        b
        for b in _python_blocks(DOC_EN.read_text(encoding="utf-8"))
        if "collect_flexcache_metrics.py" in b
    ]
    assert len(blocks) == 1
    return blocks[0]


def _run_flexcache_sample(source: str, details: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Execute the sample as a module would run, with fake ONTAP REST responses.

    Executing the block, not parsing it, is what catches a name that is never
    defined: the missing-field branch once called ``logger`` without defining it.
    """
    namespace: dict[str, Any] = {"__name__": "collect_flexcache_metrics"}
    exec(compile(source, "collect_flexcache_metrics.py", "exec"), namespace)  # noqa: S102
    namespace["http"] = _FakeHttp(details)
    namespace["_get_ontap_credentials"] = lambda: {"username": "u", "password": "p"}
    return namespace["_collect_flexcache_metrics"]()


def test_flexcache_sample_skips_a_cache_without_the_miss_field(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ONTAP_MGMT_ENDPOINT", "management.example.invalid")
    details = {
        "uuid-a": {"name": "cache_a", "svm": {"name": "svm1"}},
        "uuid-b": {"name": "cache_b", "svm": {"name": "svm1"}, "cache_miss_percent": 20.0},
    }
    with caplog.at_level(logging.WARNING):
        metrics = _run_flexcache_sample(_flexcache_block(), details)
    assert "uuid-a" in caplog.text
    # Only cache_b is published, per cache and as the dimensionless minimum.
    assert [m["Value"] for m in metrics] == [80.0, 80.0]
    assert "Dimensions" not in metrics[-1]


def test_flexcache_runner_catches_an_undefined_logger(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ONTAP_MGMT_ENDPOINT", "management.example.invalid")
    # Negative control: the earlier sample defined no logger.
    source = _flexcache_block().replace("logger = logging.getLogger(__name__)\n", "")
    with pytest.raises(NameError, match="logger"):
        _run_flexcache_sample(source, {"uuid-a": {"name": "cache_a"}})


def test_checker_rejects_a_sample_with_only_dimensioned_datums() -> None:
    # Negative control: the earlier sample shape must not satisfy the checker.
    snippet = (
        "metrics.append({\n"
        '    "MetricName": "SnapMirrorLagSeconds",\n'
        '    "Value": 1.0,\n'
        '    "Dimensions": [{"Name": "Relationship", "Value": "svm:vol"}],\n'
        "})\n"
    )
    assert "SnapMirrorLagSeconds" not in dimensionless_metric_names(snippet)
    assert "SnapMirrorLagSeconds" in dimensionless_metric_names(
        'metrics.append({"MetricName": "SnapMirrorLagSeconds", "Value": 1.0})\n'
    )
