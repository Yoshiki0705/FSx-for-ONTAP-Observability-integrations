"""SnapMirror collector: value rules, pagination, caps, and request shape.

Runs through the Terraform handler with COLLECTORS=snapmirror, so every test
also exercises the real request path (qtree_quota_poller._ontap_get) against a
recording urllib3 fake. Fixture records follow the documented response shape
of GET /api/snapmirror/relationships (ONTAP 9.18.1 REST API reference).
"""
from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from ontap_test_support import (
    FILE_SYSTEM_ID,
    MGMT_IP,
    Resp,
    load_fixture,
    page,
    relationship,
    route,
)

NS = "FSxONTAP/SnapMirror"
SNAP_ENV = {"COLLECTORS": "snapmirror", "SVM_NAME": None}
REL_DIMS = ["FileSystemId", "SourcePath", "DestinationPath"]


def _load(load_modules, pages: list[dict[str, Any]] | Any, **env: str | None):
    return load_modules(route(snapmirror=pages), env={**SNAP_ENV, **env})


def _value(datums: list[dict[str, Any]], source_path: str) -> float:
    [match] = [
        d for d in datums
        if {"Name": "SourcePath", "Value": source_path} in d["Dimensions"]
    ]
    return match["Value"]


# --------------------------------------------------------------------------
# Value rules on the documented example shape
# --------------------------------------------------------------------------


def test_documented_example_records(load_modules, caplog) -> None:
    lam = _load(load_modules, [load_fixture("snapmirror_page.json")])
    with caplog.at_level("WARNING"):
        result = lam.run()

    healthy = lam.by_name("SnapMirrorRelationshipHealthy", NS)
    assert _value(healthy, "svm1:volume1") == 1.0
    assert _value(healthy, "svm1:volume2") == 0.0
    assert _value(healthy, "svm1:volume3") == 0.0

    lags = lam.by_name("SnapMirrorLagSeconds", NS)
    assert _value(lags, "svm1:volume1") == 30942.0  # PT8H35M42S
    assert _value(lags, "svm1:volume2") == 93784.0  # P1DT2H3M4S
    # uninitialized, no lag_time: no lag datum for it
    assert len(lags) == 2

    [count] = lam.by_name("SnapMirrorUnhealthyCount", NS)
    assert count["Value"] == 2.0
    [lag_max] = lam.by_name("SnapMirrorLagSecondsMax", NS)
    assert lag_max["Value"] == 93784.0
    [trunc] = lam.by_name("SnapMirrorRelationshipsTruncated", NS)
    assert trunc["Value"] == 0.0

    messages = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any("6621444" in m and "6621445" in m and "Group Update failed" in m
               for m in messages), messages
    summary = result["collectors"]["snapmirror"]
    assert summary["succeeded"] is True
    assert summary["relationships"] == 3
    assert summary["unhealthy"] == 2


def test_dimension_lists_and_units(load_modules) -> None:
    lam = _load(load_modules, [load_fixture("snapmirror_page.json")])
    lam.run()
    for d in lam.by_name("SnapMirrorRelationshipHealthy", NS):
        assert [x["Name"] for x in d["Dimensions"]] == REL_DIMS
        assert d["Dimensions"][0]["Value"] == FILE_SYSTEM_ID
        assert d["Unit"] == "Count"
    for d in lam.by_name("SnapMirrorLagSeconds", NS):
        assert [x["Name"] for x in d["Dimensions"]] == REL_DIMS
        assert d["Unit"] == "Seconds"
    [path] = [
        d for d in lam.by_name("SnapMirrorRelationshipHealthy", NS)
        if d["Dimensions"][1]["Value"] == "svm1:volume1"
    ]
    assert path["Dimensions"][2] == {"Name": "DestinationPath", "Value": "svm1_dr:volume1_dst"}
    for name, unit in (
        ("SnapMirrorUnhealthyCount", "Count"),
        ("SnapMirrorLagSecondsMax", "Seconds"),
        ("SnapMirrorRelationshipsTruncated", "Count"),
    ):
        [d] = lam.by_name(name, NS)
        assert d["Dimensions"] == [{"Name": "FileSystemId", "Value": FILE_SYSTEM_ID}], name
        assert d["Unit"] == unit, name


def test_all_healthy_publishes_zero_unhealthy(load_modules) -> None:
    lam = _load(load_modules, [page([relationship(1), relationship(2, lag="PT30M")])])
    lam.run()
    [count] = lam.by_name("SnapMirrorUnhealthyCount", NS)
    assert count["Value"] == 0.0
    [lag_max] = lam.by_name("SnapMirrorLagSecondsMax", NS)
    assert lag_max["Value"] == 3600.0


def test_no_parseable_lag_publishes_no_max(load_modules) -> None:
    lam = _load(load_modules, [page([
        relationship(1, lag=None, state="uninitialized", healthy=False),
        relationship(2, lag=None, state="uninitialized", healthy=False),
    ])])
    lam.run()
    assert lam.by_name("SnapMirrorLagSecondsMax", NS) == []
    assert lam.by_name("SnapMirrorLagSeconds", NS) == []
    [count] = lam.by_name("SnapMirrorUnhealthyCount", NS)
    assert count["Value"] == 2.0


def test_unparseable_lag_is_skipped_with_a_warning(load_modules, caplog) -> None:
    lam = _load(load_modules, [page([relationship(1, lag="P1Y"), relationship(2, lag="PT10S")])])
    with caplog.at_level("WARNING"):
        lam.run()
    lags = lam.by_name("SnapMirrorLagSeconds", NS)
    assert [d["Value"] for d in lags] == [10.0]
    assert any("unparseable lag_time" in r.getMessage() for r in caplog.records)


def test_missing_healthy_counts_as_unhealthy_with_warning(load_modules, caplog) -> None:
    lam = _load(load_modules, [page([relationship(1, healthy=None), relationship(2, healthy="yes")])])
    with caplog.at_level("WARNING"):
        lam.run()
    healthy = lam.by_name("SnapMirrorRelationshipHealthy", NS)
    assert [d["Value"] for d in healthy] == [0.0, 0.0]
    [count] = lam.by_name("SnapMirrorUnhealthyCount", NS)
    assert count["Value"] == 2.0
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert sum("no boolean healthy" in w for w in warnings) == 2, warnings


def test_zero_relationships_still_publishes_unhealthy_count(load_modules, caplog) -> None:
    lam = _load(load_modules, [page([])])
    with caplog.at_level("INFO"):
        lam.run()
    [count] = lam.by_name("SnapMirrorUnhealthyCount", NS)
    assert count["Value"] == 0.0
    assert lam.by_name("SnapMirrorLagSecondsMax", NS) == []
    assert any("0 relationship(s)" in r.getMessage() for r in caplog.records)


def test_empty_path_gets_no_series_counts_in_aggregates_and_sets_truncation(
    load_modules,
) -> None:
    rec = relationship(2, healthy=False, lag="PT2H")
    rec["destination"] = {"path": ""}
    lam = _load(load_modules, [page([relationship(1), rec])])
    result = lam.run()
    assert len(lam.by_name("SnapMirrorRelationshipHealthy", NS)) == 1
    [count] = lam.by_name("SnapMirrorUnhealthyCount", NS)
    assert count["Value"] == 1.0
    [lag_max] = lam.by_name("SnapMirrorLagSecondsMax", NS)
    assert lag_max["Value"] == 7200.0
    # The unhealthy relationship is in the count but not in the drill-down
    # series, so the drill-down set must not be reported as complete.
    [trunc] = lam.by_name("SnapMirrorRelationshipsTruncated", NS)
    assert trunc["Value"] == 1.0
    assert result["collectors"]["snapmirror"]["truncated"] is True


def test_all_paths_present_and_under_cap_reports_no_truncation(load_modules) -> None:
    lam = _load(load_modules, [page([relationship(1), relationship(2)])])
    lam.run()
    [trunc] = lam.by_name("SnapMirrorRelationshipsTruncated", NS)
    assert trunc["Value"] == 0.0


# --------------------------------------------------------------------------
# Pagination, caps, request shape
# --------------------------------------------------------------------------


def test_pagination_follows_next_links(load_modules) -> None:
    href2 = "/api/snapmirror/relationships?start.uuid=p2&max_records=200"
    href3 = "/api/snapmirror/relationships?start.uuid=p3&max_records=200"
    lam = _load(load_modules, [
        page([relationship(1)], href2),
        page([relationship(2)], href3),
        page([relationship(3)]),
    ])
    result = lam.run()
    assert lam.urls[1:] == [f"https://{MGMT_IP}{href2}", f"https://{MGMT_IP}{href3}"]
    assert len(lam.by_name("SnapMirrorRelationshipHealthy", NS)) == 3
    assert result["collectors"]["snapmirror"]["pages_read"] == 3


def test_page_cap_with_next_link_reports_truncation(load_modules) -> None:
    def always_next(n: int) -> Resp:
        return Resp(200, page(
            [relationship(n)], f"/api/snapmirror/relationships?start.uuid=p{n + 1}"
        ))

    lam = _load(load_modules, always_next, SNAPMIRROR_MAX_RELATIONSHIPS="1000")
    lam.run()
    assert len(lam.urls) == 50
    [trunc] = lam.by_name("SnapMirrorRelationshipsTruncated", NS)
    assert trunc["Value"] == 1.0
    assert len(lam.by_name("SnapMirrorRelationshipHealthy", NS)) == 50


def test_relationship_cap_limits_series_not_aggregates(load_modules) -> None:
    records = [relationship(i, healthy=(i % 2 == 0), lag=f"PT{i}H") for i in range(1, 6)]
    lam = _load(load_modules, [page(records)], SNAPMIRROR_MAX_RELATIONSHIPS="2")
    result = lam.run()
    assert len(lam.by_name("SnapMirrorRelationshipHealthy", NS)) == 2
    assert len(lam.by_name("SnapMirrorLagSeconds", NS)) == 2
    [count] = lam.by_name("SnapMirrorUnhealthyCount", NS)
    assert count["Value"] == 3.0  # 1, 3, 5
    [lag_max] = lam.by_name("SnapMirrorLagSecondsMax", NS)
    assert lag_max["Value"] == 5 * 3600.0
    [trunc] = lam.by_name("SnapMirrorRelationshipsTruncated", NS)
    assert trunc["Value"] == 1.0
    summary = result["collectors"]["snapmirror"]
    assert summary["series_relationships"] == 2
    assert summary["relationships"] == 5


def test_next_link_to_another_host_is_refused(load_modules) -> None:
    lam = _load(load_modules, [
        page([relationship(1)], "https://198.51.100.99/api/snapmirror/relationships?x=1"),
    ])
    result = lam.run()
    summary = result["collectors"]["snapmirror"]
    assert summary["succeeded"] is False
    assert "Refusing non-ONTAP API path" in summary["error"]
    assert all("198.51.100.99" not in u for u in lam.urls)


def test_request_asks_for_every_field_and_polls_the_destination(load_modules) -> None:
    lam = _load(load_modules, [page([])])
    lam.run()
    [url] = lam.urls
    parts = urlsplit(url)
    assert parts.netloc == MGMT_IP
    assert parts.path == "/api/snapmirror/relationships"
    query = parse_qs(parts.query)
    assert set(query["fields"][0].split(",")) == {
        "uuid", "healthy", "unhealthy_reason", "lag_time", "state",
        "source.path", "destination.path",
    }
    assert query["max_records"] == ["200"]
    assert "list_destinations_only" not in query


# --------------------------------------------------------------------------
# ISO 8601 duration parser
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("PT8H35M42S", 30942.0),
        ("P2DT", 172800.0),
        ("P1DT2H3M4S", 93784.0),
        ("PT0.5S", 0.5),
        ("PT0S", 0.0),
        ("P1W", 604800.0),
        ("P1Y", None),
        ("P1M", None),
        ("", None),
        ("8h", None),
        ("P", None),
        ("PT", None),
        (None, None),
    ],
)
def test_parse_iso8601_duration(load_modules, value, expected) -> None:
    lam = _load(load_modules, [page([])])
    assert lam.snapmirror.parse_iso8601_duration(value) == expected
