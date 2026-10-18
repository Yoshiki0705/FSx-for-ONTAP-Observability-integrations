"""QoS collector: value rules, SvmName resolution, caps, and request shape.

Runs through the Terraform handler with COLLECTORS=qos, so every test also
exercises the real request path (qtree_quota_poller._ontap_get) against a
recording urllib3 fake. Fixture records follow the counter-manager response
shapes verified on ONTAP 9.19.1 (see the design note and the qos_*.json
fixtures). The counter manager is the only REST route to live QoS
performance; there is no burst-specific counter, so these series expose
effective throughput/IOPS per policy group, not a direct "bursting" signal.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from ontap_test_support import (
    FILE_SYSTEM_ID,
    MGMT_IP,
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


def _load(
    load_modules,
    *,
    schema: Any = None,
    workloads: Any = None,
    rows: Any = None,
    **env: str | None,
):
    return load_modules(
        route(qos_schema=schema, qos_workloads=workloads, qos_rows=rows),
        env={**QOS_ENV, **env},
    )


def _fixtures(load_modules, **env: str | None):
    return _load(
        load_modules,
        schema=[load_fixture("qos_schema.json")],
        workloads=[load_fixture("qos_workloads.json")],
        rows=[load_fixture("qos_rows.json")],
        **env,
    )


def _for_group(datums: list[dict[str, Any]], policy_group: str) -> dict[str, Any]:
    [match] = [
        d for d in datums
        if {"Name": "PolicyGroup", "Value": policy_group} in d["Dimensions"]
    ]
    return match


# --------------------------------------------------------------------------
# Value rules on the recorded counter-manager shape
# --------------------------------------------------------------------------


def test_recorded_rows_build_the_expected_values(load_modules) -> None:
    lam = _fixtures(load_modules)
    result = lam.run()

    ops = lam.by_name("QosOps", NS)
    assert _for_group(ops, "pg_oltp")["Value"] == 1993.0
    assert _for_group(ops, "pg_batch")["Value"] == 410.0

    tput = lam.by_name("QosThroughputBytes", NS)
    assert _for_group(tput, "pg_oltp")["Value"] == 52428800.0

    latency = lam.by_name("QosLatencyMicros", NS)
    assert _for_group(latency, "pg_oltp")["Value"] == 845.0

    read_ops = lam.by_name("QosReadOps", NS)
    write_ops = lam.by_name("QosWriteOps", NS)
    assert _for_group(read_ops, "pg_oltp")["Value"] == 1200.0
    assert _for_group(write_ops, "pg_oltp")["Value"] == 793.0

    summary = result["collectors"]["qos"]
    assert summary["succeeded"] is True
    assert summary["policy_groups"] == 2
    assert summary["series_policy_groups"] == 2
    # Every published counter is rate/average in the schema fixture, so the
    # point-sample path is not taken here.
    assert summary["point_sample"] is False


def test_dimension_lists_and_units(load_modules) -> None:
    lam = _fixtures(load_modules)
    lam.run()
    # pg_oltp resolves an SvmName from the workload map; pg_batch does not.
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


def test_svm_is_added_only_when_resolvable(load_modules) -> None:
    rows = [page([
        qos_row("pg_known"),
        qos_row("pg_unknown", "33333333-3333-3333-3333-333333333333"),
    ])]
    workloads = [page([qos_workload("pg_known", "svm-a")])]
    lam = _load(load_modules, schema=[qos_schema_page()], workloads=workloads, rows=rows)
    lam.run()
    known = _for_group(lam.by_name("QosOps", NS), "pg_known")
    unknown = _for_group(lam.by_name("QosOps", NS), "pg_unknown")
    assert [x["Name"] for x in known["Dimensions"]] == PG_SVM_DIMS
    assert [x["Name"] for x in unknown["Dimensions"]] == PG_DIMS


def test_only_present_counters_are_emitted(load_modules) -> None:
    rows = [page([qos_row("pg_min", counters={"ops": 100.0, "latency": 10.0})])]
    lam = _load(
        load_modules, schema=[qos_schema_page()], workloads=[page([])], rows=rows
    )
    lam.run()
    assert len(lam.by_name("QosOps", NS)) == 1
    assert len(lam.by_name("QosLatencyMicros", NS)) == 1
    assert lam.by_name("QosThroughputBytes", NS) == []
    assert lam.by_name("QosReadOps", NS) == []
    assert lam.by_name("QosWriteOps", NS) == []


def test_raw_only_counter_sets_point_sample(load_modules) -> None:
    schema = [qos_schema_page({
        "ops": {"type": "raw", "unit": "none"},
        "latency": {"type": "average", "unit": "microsec"},
    })]
    rows = [page([qos_row("pg_raw", counters={"ops": 1993350.0, "latency": 820.0})])]
    lam = _load(load_modules, schema=schema, workloads=[page([])], rows=rows)
    result = lam.run()
    # The raw ops value is still published as the point sample the table gives.
    assert _for_group(lam.by_name("QosOps", NS), "pg_raw")["Value"] == 1993350.0
    assert result["collectors"]["qos"]["point_sample"] is True


def test_row_without_a_resolvable_policy_group_is_skipped(load_modules, caplog) -> None:
    bad = {"id": "", "counters": [{"name": "ops", "value": 1.0}], "properties": []}
    good = qos_row("pg_ok")
    lam = _load(
        load_modules, schema=[qos_schema_page()], workloads=[page([])], rows=[page([bad, good])]
    )
    with caplog.at_level("WARNING"):
        result = lam.run()
    assert len(lam.by_name("QosOps", NS)) == 1
    assert result["collectors"]["qos"]["series_policy_groups"] == 1
    [trunc] = lam.by_name("QosPolicyGroupsTruncated", NS)
    assert trunc["Value"] == 1.0
    assert any("no resolvable policy-group name" in r.getMessage() for r in caplog.records)


def test_policy_group_name_falls_back_to_the_row_id_prefix(load_modules) -> None:
    row = {
        "id": "pg_from_id:44444444-4444-4444-4444-444444444444",
        "counters": [{"name": "ops", "value": 7.0}],
        "properties": [],
    }
    lam = _load(load_modules, schema=[qos_schema_page()], workloads=[page([])], rows=[page([row])])
    lam.run()
    [ops] = lam.by_name("QosOps", NS)
    assert {"Name": "PolicyGroup", "Value": "pg_from_id"} in ops["Dimensions"]


# --------------------------------------------------------------------------
# Pagination, caps, request shape
# --------------------------------------------------------------------------


def test_rows_pagination_follows_next_links(load_modules) -> None:
    href2 = "/api/cluster/counter/tables/qos/rows?start=p2&max_records=200"
    lam = _load(
        load_modules,
        schema=[qos_schema_page()],
        workloads=[page([])],
        rows=[page([qos_row("pg1")], href2), page([qos_row("pg2")])],
    )
    result = lam.run()
    assert len(lam.by_name("QosOps", NS)) == 2
    assert result["collectors"]["qos"]["pages_read"] == 2


def test_policy_group_cap_limits_series_not_truncation_reporting(load_modules) -> None:
    rows = [page([qos_row(f"pg{i}", f"{i:032d}") for i in range(1, 6)])]
    lam = _load(
        load_modules,
        schema=[qos_schema_page()],
        workloads=[page([])],
        rows=rows,
        QOS_MAX_POLICY_GROUPS="2",
    )
    result = lam.run()
    assert len(lam.by_name("QosOps", NS)) == 2
    [trunc] = lam.by_name("QosPolicyGroupsTruncated", NS)
    assert trunc["Value"] == 1.0
    summary = result["collectors"]["qos"]
    assert summary["series_policy_groups"] == 2
    assert summary["policy_groups"] == 5


def test_put_metric_data_batches_across_more_than_twenty_datums(load_modules) -> None:
    # Six policy groups x five counters each = 30 datums + 1 aggregate,
    # which must cross the 20-datum PutMetricData batch boundary.
    rows = [page([qos_row(f"pg{i}", f"{i:032d}") for i in range(1, 7)])]
    lam = _load(load_modules, schema=[qos_schema_page()], workloads=[page([])], rows=rows)
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


def test_next_link_to_another_host_is_refused(load_modules) -> None:
    lam = _load(
        load_modules,
        schema=[qos_schema_page()],
        workloads=[page([])],
        rows=[page(
            [qos_row("pg1")],
            "https://198.51.100.99/api/cluster/counter/tables/qos/rows?x=1",
        )],
    )
    result = lam.run()
    assert result["collectors"]["qos"]["succeeded"] is False
    assert "Refusing non-ONTAP API path" in result["collectors"]["qos"]["error"]
    assert all("198.51.100.99" not in u for u in lam.urls)


def test_request_path_hits_the_real_counter_manager_and_workload_routes(load_modules) -> None:
    lam = _fixtures(load_modules)
    lam.run()
    paths = [urlsplit(u).path for u in lam.urls]
    netlocs = {urlsplit(u).netloc for u in lam.urls}
    assert netlocs == {MGMT_IP}
    # The no-stub discipline: the external call path is real, not echoed.
    assert any(p == "/api/cluster/counter/tables/qos/rows" for p in paths), paths
    assert any(p == "/api/cluster/counter/tables/qos" for p in paths), paths
    assert any(p == "/api/storage/qos/workloads" for p in paths), paths


def test_registers_in_runners_and_emits_the_heartbeat(load_modules) -> None:
    lam = _fixtures(load_modules)
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
