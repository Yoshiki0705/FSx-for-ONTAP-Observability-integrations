"""What the collectors emit must equal fixtures/metric_contract.json.

The same JSON is read by terraform/fsxn-ontap-custom-metrics/tests, which
asserts that every custom-metric alarm reads a (namespace, metric, dimension
names) tuple from it. Together the two checks tie the alarms to series the
Lambda actually publishes; CloudWatch treats a different dimension set as a
different metric, so a mismatch leaves an alarm reading nothing.
"""
from __future__ import annotations

import json

from ontap_test_support import FIXTURES, load_fixture, page, quota_record, route

Key = tuple[str, str, frozenset[str]]


def _contract() -> list[dict]:
    return json.loads((FIXTURES / "metric_contract.json").read_text(encoding="utf-8"))


def _keys(entries: list[dict]) -> set[Key]:
    return {(e["namespace"], e["metric"], frozenset(e["dimensions"])) for e in entries}


def _run_all(load_modules):
    lam = load_modules(route(
        quota=[page([quota_record("vol_a", "q1", 50, 100), quota_record("vol_a", "q2", 90, 100)])],
        snapmirror=[load_fixture("snapmirror_page.json")],
    ))
    lam.run()
    return lam


def test_emitted_series_equal_the_contract(load_modules) -> None:
    lam = _run_all(load_modules)
    emitted = lam.emitted()
    expected = _keys(_contract())
    assert emitted == expected, (
        f"only emitted: {sorted(map(str, emitted - expected))}; "
        f"only in contract: {sorted(map(str, expected - emitted))}"
    )


def test_units_match_the_contract(load_modules) -> None:
    lam = _run_all(load_modules)
    units = {(e["namespace"], e["metric"]): e["unit"] for e in _contract()}
    for namespace, datum in lam.calls():
        assert datum["Unit"] == units[(namespace, datum["MetricName"])], datum["MetricName"]


def test_every_alarm_entry_is_emitted(load_modules) -> None:
    lam = _run_all(load_modules)
    alarm_keys = _keys([e for e in _contract() if e["alarm"]])
    assert len(alarm_keys) == 5
    assert alarm_keys <= lam.emitted()


def test_negative_control_a_wrong_dimension_set_is_not_emitted(load_modules) -> None:
    """A lag alarm on the per-relationship metric with FileSystemId only reads nothing."""
    lam = _run_all(load_modules)
    wrong = ("FSxONTAP/SnapMirror", "SnapMirrorLagSeconds", frozenset({"FileSystemId"}))
    assert wrong not in lam.emitted()
    assert wrong not in _keys(_contract())
