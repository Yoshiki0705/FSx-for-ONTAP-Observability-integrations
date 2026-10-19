"""QoS collector: two-sample delta rates, SvmName resolution, caps, request shape.

Runs through the Terraform handler with COLLECTORS=qos, so every test also
exercises the real request path (qtree_quota_poller._ontap_get) against a
recording urllib3 fake. Fixture records follow the counter-manager response
shapes verified on ONTAP 9.19.1.

The counter manager returns CUMULATIVE RAW values for the rate-typed counters
(ops, total_data, read_ops, write_ops) and an accumulating latency counter,
not per-second rates (confirmed on a live ONTAP 9.19.1P2 file system). So the
collector samples the rows TWICE within one invocation and publishes
(v2 - v1) / elapsed as the rate, and (delta latency) / (delta ops) as the
interval-average latency. These tests drive that path with two distinct
samples and a controlled elapsed time (time.sleep patched to a no-op,
time.monotonic driven by a fake clock), so they are deterministic and fast
and never actually sleep.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit, parse_qs

import pytest

from ontap_test_support import (
    FILE_SYSTEM_ID,
    MGMT_IP,
    Loaded,
    Resp,
    load_fixture,
    page,
    qos_row,
    qos_schema_page,
    qos_workload,
    route,
)

NS = "FSxONTAP/QoS"
QOS_ENV = {"COLLECTORS": "qos", "SVM_NAME": None}
PG_DIMS = ["FileSystemId", "PolicyGroup"]
PG_SVM_DIMS = ["FileSystemId", "PolicyGroup", "SvmName"]

# The controlled sample window the tests assert against (seconds).
ELAPSED = 10.0


class _FakeClock:
    """A monotonic clock that only advances when the collector "sleeps".

    sample_counter_rows_twice reads monotonic(), calls sleep(interval), then
    reads monotonic() again, so the measured elapsed equals the total slept.
    Patching sleep to advance this clock by ``advance`` (default: the requested
    interval) makes the window deterministic and instant. Credential caching
    also reads monotonic() between the two window reads, so a fixed two-value
    sequence cannot be used — this clock answers every read with the current
    accumulated time.
    """

    def __init__(self, advance: float | None = None) -> None:
        self._now = 1000.0
        self._advance = advance

    def monotonic(self) -> float:
        return self._now

    def sleep(self, requested: float) -> None:
        self._now += self._advance if self._advance is not None else requested


def _patch_clock(
    monkeypatch: pytest.MonkeyPatch, lam: Loaded, *, elapsed: float = ELAPSED
) -> None:
    """Patch the collector's time so it never sleeps and elapsed is controlled.

    The default requested interval is QOS_SAMPLE_INTERVAL_SECONDS (10 s, unset
    here), so with advance=None the measured elapsed is exactly ELAPSED. Pass
    elapsed=0.0 to drive the clock-anomaly path (no advance on sleep).
    """
    advance = None if elapsed == ELAPSED else elapsed
    clock = _FakeClock(advance=advance)
    monkeypatch.setattr(lam.qos.time, "sleep", clock.sleep)
    monkeypatch.setattr(lam.qos.time, "monotonic", clock.monotonic)


def _load(
    load_modules,
    *,
    schema: Any = None,
    workloads: Any = None,
    rows: Any = None,
    **env: str | None,
) -> Loaded:
    return load_modules(
        route(qos_schema=schema, qos_workloads=workloads, qos_rows=rows),
        env={**QOS_ENV, **env},
    )


def _two_sample_rows(
    sample1: list[dict[str, Any]], sample2: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Return the qos_rows page list that serves sample 1 then sample 2.

    ``route`` serves qos_rows pages in order, so the first paginated read
    consumes sample 1 (one page, no next link) and the second read consumes
    sample 2. Each sample here is a single page.
    """
    return [page(sample1), page(sample2)]


def _for_group(datums: list[dict[str, Any]], policy_group: str) -> dict[str, Any]:
    [match] = [
        d for d in datums
        if {"Name": "PolicyGroup", "Value": policy_group} in d["Dimensions"]
    ]
    return match


# --------------------------------------------------------------------------
# Two-sample delta rates
# --------------------------------------------------------------------------


def test_rate_counters_are_the_delta_over_elapsed(load_modules, monkeypatch) -> None:
    # ops/read_ops/write_ops/total_data published as (v2 - v1) / elapsed.
    v1 = {"ops": 1000.0, "read_ops": 600.0, "write_ops": 400.0, "total_data": 1_000_000.0}
    v2 = {"ops": 4000.0, "read_ops": 2100.0, "write_ops": 1900.0, "total_data": 6_000_000.0}
    rows = _two_sample_rows(
        [qos_row("pg_oltp", counters=v1)],
        [qos_row("pg_oltp", counters=v2)],
    )
    lam = _load(load_modules, schema=[qos_schema_page()], workloads=[page([])], rows=rows)
    _patch_clock(monkeypatch, lam)
    result = lam.run()

    assert _for_group(lam.by_name("QosOps", NS), "pg_oltp")["Value"] == (4000.0 - 1000.0) / ELAPSED
    assert _for_group(lam.by_name("QosReadOps", NS), "pg_oltp")["Value"] == (2100.0 - 600.0) / ELAPSED
    assert _for_group(lam.by_name("QosWriteOps", NS), "pg_oltp")["Value"] == (1900.0 - 400.0) / ELAPSED
    assert _for_group(lam.by_name("QosThroughputBytes", NS), "pg_oltp")["Value"] == (6_000_000.0 - 1_000_000.0) / ELAPSED

    summary = result["collectors"]["qos"]
    assert summary["succeeded"] is True
    assert summary["policy_groups"] == 1
    assert summary["series_policy_groups"] == 1
    assert summary["rate_policy_groups"] == 1
    assert summary["sample_interval_seconds"] == ELAPSED
    # The stale point_sample flag is gone; every rate is a computed delta.
    assert "point_sample" not in summary


def test_latency_is_interval_average_delta_over_delta_ops(load_modules, monkeypatch) -> None:
    # latency = (latency_v2 - latency_v1) / (ops_v2 - ops_v1) microseconds.
    v1 = {"ops": 1000.0, "latency": 500_000.0}
    v2 = {"ops": 3000.0, "latency": 2_100_000.0}
    rows = _two_sample_rows(
        [qos_row("pg_lat", counters=v1)],
        [qos_row("pg_lat", counters=v2)],
    )
    lam = _load(load_modules, schema=[qos_schema_page()], workloads=[page([])], rows=rows)
    _patch_clock(monkeypatch, lam)
    lam.run()

    expected = (2_100_000.0 - 500_000.0) / (3000.0 - 1000.0)
    assert _for_group(lam.by_name("QosLatencyMicros", NS), "pg_lat")["Value"] == expected
    # ops itself is still the per-second rate over elapsed.
    assert _for_group(lam.by_name("QosOps", NS), "pg_lat")["Value"] == (3000.0 - 1000.0) / ELAPSED


def test_idle_policy_group_skips_latency_and_reports_zero_ops(load_modules, monkeypatch) -> None:
    # No activity between samples: ops delta is 0, so latency is skipped, and
    # the ops rate is 0 (a valid, non-negative delta).
    v1 = {"ops": 5000.0, "latency": 4_000_000.0}
    v2 = {"ops": 5000.0, "latency": 4_000_000.0}
    rows = _two_sample_rows(
        [qos_row("pg_idle", counters=v1)],
        [qos_row("pg_idle", counters=v2)],
    )
    lam = _load(load_modules, schema=[qos_schema_page()], workloads=[page([])], rows=rows)
    _patch_clock(monkeypatch, lam)
    lam.run()

    assert _for_group(lam.by_name("QosOps", NS), "pg_idle")["Value"] == 0.0
    assert lam.by_name("QosLatencyMicros", NS) == []


def test_counter_reset_skips_that_counter_and_warns(load_modules, monkeypatch, caplog) -> None:
    # v2 < v1 for ops (counter reset / policy group recreated): ops skipped,
    # warning logged. total_data increased normally and is still published.
    v1 = {"ops": 9_000_000.0, "total_data": 1_000_000.0}
    v2 = {"ops": 100.0, "total_data": 3_000_000.0}
    rows = _two_sample_rows(
        [qos_row("pg_reset", counters=v1)],
        [qos_row("pg_reset", counters=v2)],
    )
    lam = _load(load_modules, schema=[qos_schema_page()], workloads=[page([])], rows=rows)
    _patch_clock(monkeypatch, lam)
    with caplog.at_level("WARNING"):
        lam.run()

    assert lam.by_name("QosOps", NS) == []
    assert _for_group(lam.by_name("QosThroughputBytes", NS), "pg_reset")["Value"] == (3_000_000.0 - 1_000_000.0) / ELAPSED
    assert any(
        "counter reset" in r.getMessage().lower() or "decreased" in r.getMessage().lower()
        for r in caplog.records
    )


def test_policy_group_missing_in_second_sample_is_skipped(load_modules, monkeypatch, caplog) -> None:
    # pg_gone is in sample 1 only; it cannot be differenced, so it is skipped
    # for all rate counters. pg_stay is in both and is published.
    rows = _two_sample_rows(
        [qos_row("pg_gone"), qos_row("pg_stay")],
        [qos_row("pg_stay")],
    )
    lam = _load(load_modules, schema=[qos_schema_page()], workloads=[page([])], rows=rows)
    _patch_clock(monkeypatch, lam)
    with caplog.at_level("INFO"):
        result = lam.run()

    assert lam.by_name("QosOps", NS) == lam.by_name("QosOps", NS)  # readable grouping
    groups = {
        dim["Value"]
        for d in lam.by_name("QosOps", NS)
        for dim in d["Dimensions"]
        if dim["Name"] == "PolicyGroup"
    }
    assert groups == {"pg_stay"}
    assert result["collectors"]["qos"]["rate_policy_groups"] == 1
    assert any("missing in" in r.getMessage() for r in caplog.records)


def test_non_positive_elapsed_skips_rate_publication(load_modules, monkeypatch, caplog) -> None:
    # A clock anomaly (elapsed <= 0) must not publish rate counters this run.
    v1 = {"ops": 1000.0, "latency": 500_000.0}
    v2 = {"ops": 4000.0, "latency": 2_000_000.0}
    rows = _two_sample_rows(
        [qos_row("pg_clock", counters=v1)],
        [qos_row("pg_clock", counters=v2)],
    )
    lam = _load(load_modules, schema=[qos_schema_page()], workloads=[page([])], rows=rows)
    _patch_clock(monkeypatch, lam, elapsed=0.0)
    with caplog.at_level("WARNING"):
        result = lam.run()

    # A clock anomaly makes the samples untrustworthy, so nothing is published
    # for the group this run: no rate counters and no latency.
    assert lam.by_name("QosOps", NS) == []
    assert lam.by_name("QosThroughputBytes", NS) == []
    assert lam.by_name("QosLatencyMicros", NS) == []
    assert result["collectors"]["qos"]["rate_policy_groups"] == 0
    assert any("clock anomaly" in r.getMessage().lower() for r in caplog.records)


# --------------------------------------------------------------------------
# Dimensions, units, SvmName resolution (two-sample)
# --------------------------------------------------------------------------


def test_dimension_lists_and_units(load_modules, monkeypatch) -> None:
    rows = _two_sample_rows(
        [qos_row("pg_oltp"), qos_row("pg_batch", "22222222-2222-2222-2222-222222222222")],
        [
            qos_row("pg_oltp", counters={
                "ops": 3000.0, "total_data": 10_000_000.0, "latency": 1_640_000.0,
                "read_ops": 2000.0, "write_ops": 1000.0,
            }),
            qos_row("pg_batch", "22222222-2222-2222-2222-222222222222", counters={
                "ops": 2000.0, "total_data": 7_000_000.0, "latency": 1_640_000.0,
                "read_ops": 1500.0, "write_ops": 500.0,
            }),
        ],
    )
    workloads = [page([qos_workload("pg_oltp", "svm-prod-01")])]
    lam = _load(load_modules, schema=[qos_schema_page()], workloads=workloads, rows=rows)
    _patch_clock(monkeypatch, lam)
    lam.run()

    assert [x["Name"] for x in _for_group(lam.by_name("QosOps", NS), "pg_oltp")["Dimensions"]] \
        == PG_SVM_DIMS
    assert [x["Name"] for x in _for_group(lam.by_name("QosOps", NS), "pg_batch")["Dimensions"]] \
        == PG_DIMS
    svm = _for_group(lam.by_name("QosOps", NS), "pg_oltp")["Dimensions"][2]
    assert svm == {"Name": "SvmName", "Value": "svm-prod-01"}

    for metric, unit in (
        ("QosOps", "Count/Second"),
        ("QosThroughputBytes", "Bytes/Second"),
        ("QosLatencyMicros", "Microseconds"),
        ("QosReadOps", "Count/Second"),
        ("QosWriteOps", "Count/Second"),
    ):
        for d in lam.by_name(metric, NS):
            assert d["Unit"] == unit, metric
            assert d["Dimensions"][0] == {"Name": "FileSystemId", "Value": FILE_SYSTEM_ID}

    [trunc] = lam.by_name("QosPolicyGroupsTruncated", NS)
    assert trunc["Dimensions"] == [{"Name": "FileSystemId", "Value": FILE_SYSTEM_ID}]
    assert trunc["Unit"] == "Count"
    assert trunc["Value"] == 0.0


def test_svm_is_added_only_when_resolvable(load_modules, monkeypatch) -> None:
    rows = _two_sample_rows(
        [
            qos_row("pg_known", counters={"ops": 1000.0}),
            qos_row("pg_unknown", "33333333-3333-3333-3333-333333333333", counters={"ops": 1000.0}),
        ],
        [
            qos_row("pg_known", counters={"ops": 3000.0}),
            qos_row("pg_unknown", "33333333-3333-3333-3333-333333333333", counters={"ops": 3000.0}),
        ],
    )
    workloads = [page([qos_workload("pg_known", "svm-a")])]
    lam = _load(load_modules, schema=[qos_schema_page()], workloads=workloads, rows=rows)
    _patch_clock(monkeypatch, lam)
    lam.run()
    known = _for_group(lam.by_name("QosOps", NS), "pg_known")
    unknown = _for_group(lam.by_name("QosOps", NS), "pg_unknown")
    assert [x["Name"] for x in known["Dimensions"]] == PG_SVM_DIMS
    assert [x["Name"] for x in unknown["Dimensions"]] == PG_DIMS


def test_only_present_counters_are_emitted(load_modules, monkeypatch) -> None:
    rows = _two_sample_rows(
        [qos_row("pg_min", counters={"ops": 100.0, "latency": 10_000.0})],
        [qos_row("pg_min", counters={"ops": 300.0, "latency": 50_000.0})],
    )
    lam = _load(
        load_modules, schema=[qos_schema_page()], workloads=[page([])], rows=rows
    )
    _patch_clock(monkeypatch, lam)
    lam.run()
    assert len(lam.by_name("QosOps", NS)) == 1
    assert len(lam.by_name("QosLatencyMicros", NS)) == 1
    assert lam.by_name("QosThroughputBytes", NS) == []
    assert lam.by_name("QosReadOps", NS) == []
    assert lam.by_name("QosWriteOps", NS) == []


def test_row_without_a_resolvable_policy_group_is_skipped(load_modules, monkeypatch, caplog) -> None:
    bad1 = {"id": "", "counters": [{"name": "ops", "value": 1.0}], "properties": []}
    bad2 = {"id": "", "counters": [{"name": "ops", "value": 2.0}], "properties": []}
    good1 = qos_row("pg_ok", counters={"ops": 1000.0})
    good2 = qos_row("pg_ok", counters={"ops": 3000.0})
    lam = _load(
        load_modules,
        schema=[qos_schema_page()],
        workloads=[page([])],
        rows=_two_sample_rows([bad1, good1], [bad2, good2]),
    )
    _patch_clock(monkeypatch, lam)
    with caplog.at_level("WARNING"):
        result = lam.run()
    assert len(lam.by_name("QosOps", NS)) == 1
    assert result["collectors"]["qos"]["series_policy_groups"] == 1
    [trunc] = lam.by_name("QosPolicyGroupsTruncated", NS)
    assert trunc["Value"] == 1.0
    assert any("no resolvable policy-group name" in r.getMessage() for r in caplog.records)


def test_policy_group_name_falls_back_to_the_row_id_prefix(load_modules, monkeypatch) -> None:
    row1 = {
        "id": "pg_from_id:44444444-4444-4444-4444-444444444444",
        "counters": [{"name": "ops", "value": 1000.0}],
        "properties": [],
    }
    row2 = {
        "id": "pg_from_id:44444444-4444-4444-4444-444444444444",
        "counters": [{"name": "ops", "value": 2000.0}],
        "properties": [],
    }
    lam = _load(
        load_modules,
        schema=[qos_schema_page()],
        workloads=[page([])],
        rows=_two_sample_rows([row1], [row2]),
    )
    _patch_clock(monkeypatch, lam)
    lam.run()
    [ops] = lam.by_name("QosOps", NS)
    assert {"Name": "PolicyGroup", "Value": "pg_from_id"} in ops["Dimensions"]
    assert ops["Value"] == (2000.0 - 1000.0) / ELAPSED


# --------------------------------------------------------------------------
# Pagination, caps, request shape
# --------------------------------------------------------------------------


def test_rows_pagination_follows_next_links(load_modules, monkeypatch) -> None:
    # Sample 1 spans two pages (next link), sample 2 is one page; the next
    # link is consumed within a single paginated read.
    href2 = "/api/cluster/counter/tables/qos/rows?start=p2&max_records=200"
    lam = _load(
        load_modules,
        schema=[qos_schema_page()],
        workloads=[page([])],
        rows=[
            page([qos_row("pg1", counters={"ops": 1000.0})], href2),
            page([qos_row("pg2", "22222222-2222-2222-2222-222222222222", counters={"ops": 2000.0})]),
            page([
                qos_row("pg1", counters={"ops": 3000.0}),
                qos_row("pg2", "22222222-2222-2222-2222-222222222222", counters={"ops": 5000.0}),
            ]),
        ],
    )
    _patch_clock(monkeypatch, lam)
    result = lam.run()
    assert len(lam.by_name("QosOps", NS)) == 2
    # Two pages in sample 1 + one page in sample 2.
    assert result["collectors"]["qos"]["pages_read"] == 3


def test_policy_group_cap_limits_series_not_truncation_reporting(load_modules, monkeypatch) -> None:
    sample1 = [qos_row(f"pg{i}", f"{i:032d}", counters={"ops": 1000.0}) for i in range(1, 6)]
    sample2 = [qos_row(f"pg{i}", f"{i:032d}", counters={"ops": 2000.0}) for i in range(1, 6)]
    lam = _load(
        load_modules,
        schema=[qos_schema_page()],
        workloads=[page([])],
        rows=_two_sample_rows(sample1, sample2),
        QOS_MAX_POLICY_GROUPS="2",
    )
    _patch_clock(monkeypatch, lam)
    result = lam.run()
    assert len(lam.by_name("QosOps", NS)) == 2
    [trunc] = lam.by_name("QosPolicyGroupsTruncated", NS)
    assert trunc["Value"] == 1.0
    summary = result["collectors"]["qos"]
    assert summary["series_policy_groups"] == 2
    assert summary["policy_groups"] == 5


def test_put_metric_data_batches_across_more_than_twenty_datums(load_modules, monkeypatch) -> None:
    # Six policy groups x five counters each = 30 datums + 1 aggregate,
    # which must cross the 20-datum PutMetricData batch boundary.
    full1 = {"ops": 1000.0, "total_data": 1_000_000.0, "latency": 500_000.0,
             "read_ops": 600.0, "write_ops": 400.0}
    full2 = {"ops": 3000.0, "total_data": 6_000_000.0, "latency": 2_100_000.0,
             "read_ops": 1800.0, "write_ops": 1200.0}
    sample1 = [qos_row(f"pg{i}", f"{i:032d}", counters=full1) for i in range(1, 7)]
    sample2 = [qos_row(f"pg{i}", f"{i:032d}", counters=full2) for i in range(1, 7)]
    lam = _load(
        load_modules, schema=[qos_schema_page()], workloads=[page([])],
        rows=_two_sample_rows(sample1, sample2),
    )
    _patch_clock(monkeypatch, lam)
    lam.run()
    batch_sizes = [
        len(c.kwargs["MetricData"])
        for c in lam.cw.put_metric_data.call_args_list
        if c.kwargs["Namespace"] == NS
    ]
    assert len(batch_sizes) >= 2
    assert all(size <= 20 for size in batch_sizes)
    # 6 policy groups x 5 counters + 1 QosPolicyGroupsTruncated aggregate, plus
    # the CollectorSucceeded heartbeat the handler publishes into this namespace.
    assert sum(batch_sizes) == 6 * 5 + 1 + 1


def test_auth_failure_raises_and_drops_credentials(load_modules) -> None:
    # The schema request is the first QoS call; a 401 there must raise
    # OntapAuthError, stop further ONTAP calls, and drop cached credentials.
    lam = _load(
        load_modules,
        schema=lambda _n: Resp(401, {}),
        workloads=[page([])],
        rows=[page([])],
    )
    result = lam.run()
    assert len(lam.urls) == 1, lam.urls
    assert result["collectors"]["qos"]["succeeded"] is False
    [hb] = [
        d for d in lam.by_name("CollectorSucceeded", NS)
        if {"Name": "Collector", "Value": "qos"} in d["Dimensions"]
    ]
    assert hb["Value"] == 0.0
    assert lam.sm.get_secret_value.call_count == 1
    # Credentials were dropped, so the next invocation re-reads the secret.
    lam.run()
    assert lam.sm.get_secret_value.call_count == 2


def test_forbidden_is_also_an_auth_failure(load_modules) -> None:
    lam = _load(
        load_modules,
        schema=lambda _n: Resp(403, {}),
        workloads=[page([])],
        rows=[page([])],
    )
    result = lam.run()
    assert len(lam.urls) == 1
    assert result["collectors"]["qos"]["succeeded"] is False


def test_next_link_to_another_host_is_refused(load_modules, monkeypatch) -> None:
    lam = _load(
        load_modules,
        schema=[qos_schema_page()],
        workloads=[page([])],
        rows=[page(
            [qos_row("pg1")],
            "https://198.51.100.99/api/cluster/counter/tables/qos/rows?x=1",
        )],
    )
    _patch_clock(monkeypatch, lam)
    result = lam.run()
    assert result["collectors"]["qos"]["succeeded"] is False
    assert "Refusing non-ONTAP API path" in result["collectors"]["qos"]["error"]
    assert all("198.51.100.99" not in u for u in lam.urls)


def test_request_path_hits_the_real_counter_manager_and_workload_routes(load_modules, monkeypatch) -> None:
    rows = _two_sample_rows(
        [qos_row("pg_oltp", counters={"ops": 1000.0})],
        [qos_row("pg_oltp", counters={"ops": 3000.0})],
    )
    lam = _load(load_modules, schema=[qos_schema_page()], workloads=[page([])], rows=rows)
    _patch_clock(monkeypatch, lam)
    lam.run()
    paths = [urlsplit(u).path for u in lam.urls]
    netlocs = {urlsplit(u).netloc for u in lam.urls}
    assert netlocs == {MGMT_IP}
    # The no-stub discipline: the external call path is real, not echoed.
    assert any(p == "/api/cluster/counter/tables/qos/rows" for p in paths), paths
    # Regression guard: counter-manager rows return only id + _links unless
    # fields explicitly requests counters, so the rows request MUST carry
    # fields=...counters... or every row is published as "no counters"
    # (the live-run bug this test missed when the fixture pre-filled counters).
    rows_q = [parse_qs(urlsplit(u).query) for u in lam.urls
              if urlsplit(u).path == "/api/cluster/counter/tables/qos/rows"]
    assert rows_q and all(
        "counters" in ",".join(q.get("fields", [""])) for q in rows_q
    ), rows_q
    # The rows route is read twice (the two-sample window).
    assert len(rows_q) == 2, rows_q
    assert any(p == "/api/cluster/counter/tables/qos" for p in paths), paths
    assert any(p == "/api/storage/qos/workloads" for p in paths), paths


def test_recorded_fixtures_build_two_sample_rates(load_modules, monkeypatch) -> None:
    # The checked-in fixtures carry two samples (num_records per page); the
    # second sample's counters are higher, so the published values are deltas.
    lam = _load(
        load_modules,
        schema=[load_fixture("qos_schema.json")],
        workloads=[load_fixture("qos_workloads.json")],
        rows=[load_fixture("qos_rows_sample1.json"), load_fixture("qos_rows_sample2.json")],
    )
    _patch_clock(monkeypatch, lam)
    result = lam.run()
    ops = lam.by_name("QosOps", NS)
    # pg_oltp: ops 1993 -> 21993 over 10 s = 2000 ops/s.
    assert _for_group(ops, "pg_oltp")["Value"] == 2000.0
    assert result["collectors"]["qos"]["rate_policy_groups"] == 2


def test_registers_in_runners_and_emits_the_heartbeat(load_modules, monkeypatch) -> None:
    rows = _two_sample_rows(
        [qos_row("pg_oltp", counters={"ops": 1000.0})],
        [qos_row("pg_oltp", counters={"ops": 3000.0})],
    )
    lam = _load(load_modules, schema=[qos_schema_page()], workloads=[page([])], rows=rows)
    _patch_clock(monkeypatch, lam)
    lam.run()
    assert "qos" in lam.handler.RUNNERS
    assert lam.handler.COLLECTOR_NAMESPACES["qos"] == NS
    [hb] = [
        d for d in lam.by_name("CollectorSucceeded", NS)
        if {"Name": "Collector", "Value": "qos"} in d["Dimensions"]
    ]
    assert hb["Value"] == 1.0
    assert hb["Dimensions"] == [
        {"Name": "FileSystemId", "Value": FILE_SYSTEM_ID},
        {"Name": "Collector", "Value": "qos"},
    ]
