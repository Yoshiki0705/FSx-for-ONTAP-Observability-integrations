"""The report's utilization value: cloudwatch:GetMetricData, dimensions, carry-through.

The design gives the function ``cloudwatch:GetMetricData`` for the report
values. These tests pin the query (the series the trigger alarms read), the
value carried into the reports, the decision log and the archive events, and
the classification of a missing or failed read, which must never appear as a
bare null.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from ssd_test_support import (
    FILE_SYSTEM_ID,
    TRIGGER_ALARM,
    FakeCloudWatch,
    FakeFsx,
    FakeLogs,
    FakeS3,
    FakeSns,
    admin_action,
    alarm_response,
    archived,
    file_system,
    log_lines,
    make_client_error,
    make_transport_error,
    reports,
)

FS_DIMENSIONS = [
    {"Name": "FileSystemId", "Value": FILE_SYSTEM_ID},
    {"Name": "StorageTier", "Value": "SSD"},
    {"Name": "DataType", "Value": "All"},
]


def _second_gen() -> dict:
    return file_system(capacity=1024, deployment_type="SINGLE_AZ_2", ha_pairs=2)


def test_query_reads_the_alarm_series_with_per_aggregate_dimensions(load_handler) -> None:
    cloudwatch = FakeCloudWatch(alarm_response("ALARM"))
    loaded = load_handler(
        fsx=FakeFsx([_second_gen()]),
        cloudwatch=cloudwatch,
        env={"AGGREGATE_NAMES": "aggr1,aggr2"},
    )
    loaded.handler.lambda_handler({}, None)

    assert len(cloudwatch.metric_data_calls) == 1
    call = cloudwatch.metric_data_calls[0]
    assert call["ScanBy"] == "TimestampDescending"
    assert call["EndTime"] - call["StartTime"] == timedelta(seconds=1800)
    queries = call["MetricDataQueries"]
    assert [q["Id"] for q in queries] == ["fs", "agg0", "agg1"]
    for query in queries:
        stat = query["MetricStat"]
        # Same namespace, metric, statistic and period as the trigger alarms.
        assert stat["Metric"]["Namespace"] == "AWS/FSx"
        assert stat["Metric"]["MetricName"] == "StorageCapacityUtilization"
        assert stat["Stat"] == "Average"
        assert stat["Period"] == 300
        assert query["ReturnData"] is True
    assert queries[0]["MetricStat"]["Metric"]["Dimensions"] == FS_DIMENSIONS
    assert queries[1]["MetricStat"]["Metric"]["Dimensions"] == [
        *FS_DIMENSIONS,
        {"Name": "Aggregate", "Value": "aggr1"},
    ]
    assert queries[2]["MetricStat"]["Metric"]["Dimensions"] == [
        *FS_DIMENSIONS,
        {"Name": "Aggregate", "Value": "aggr2"},
    ]


def test_first_generation_queries_only_the_file_system_series(load_handler) -> None:
    cloudwatch = FakeCloudWatch(alarm_response("ALARM"))
    loaded = load_handler(fsx=FakeFsx([file_system(capacity=1024)]), cloudwatch=cloudwatch)
    loaded.handler.lambda_handler({}, None)
    queries = cloudwatch.metric_data_calls[0]["MetricDataQueries"]
    assert [q["MetricStat"]["Metric"]["Dimensions"] for q in queries] == [FS_DIMENSIONS]


def test_utilization_value_is_carried_into_reports_logs_and_archive(load_handler) -> None:
    """The read value reaches every place the design names, through the lifecycle."""
    store: dict = {}
    cloudwatch = FakeCloudWatch(
        alarm_response("ALARM"), series={None: [91.25, 90.0], "aggr1": [88.5]}
    )
    s3, sns, logs = FakeS3(), FakeSns(), FakeLogs()
    env = {"AGGREGATE_NAMES": "aggr1"}
    loaded = load_handler(
        fsx=FakeFsx([_second_gen()]), cloudwatch=cloudwatch, s3=s3, sns=sns, logs=logs,
        lock_store=store, env=env,
    )
    assert loaded.handler.lambda_handler({}, None)["decision"] == "submitted"

    end = cloudwatch.metric_data_calls[0]["EndTime"]
    expected_fs = {
        "status": "ok",
        "value": 91.25,
        "timestamp": (end - timedelta(seconds=300)).isoformat(),
        "status_code": "Complete",
    }

    def check(utilization: dict) -> None:
        assert utilization["status"] == "ok"
        assert "detail" not in utilization
        assert utilization["metric"] == "AWS/FSx StorageCapacityUtilization"
        assert utilization["file_system"] == expected_fs
        assert utilization["aggregates"]["aggr1"]["value"] == 88.5

    # Archive: the intent decision event.
    intent = [body for _, _, event, body in archived(s3) if event == "decision"]
    assert len(intent) == 1
    check(intent[0]["utilization"])
    # Decision log: the same body.
    decision_lines = [line for line in log_lines(logs) if line["event"] == "decision"]
    check(decision_lines[0]["utilization"])
    # Reports: the pre-call report and the submitted report.
    by_subject = dict(reports(sns))
    check(by_subject[f"T4 will increase {FILE_SYSTEM_ID}"]["utilization"])
    check(by_subject[f"T4 submitted increase for {FILE_SYSTEM_ID}"]["utilization"])
    # Persisted for the follower invocations that send later reports.
    check(store[FILE_SYSTEM_ID]["report_context"]["utilization"])

    # A later invocation sends the terminal report from the persisted context:
    # the value is the one read at decision time, not re-read.
    request_time = datetime.fromtimestamp(
        int(store[FILE_SYSTEM_ID]["request_time"]), tz=timezone.utc
    )
    actions = [admin_action(status="COMPLETED", target_capacity=1127, request_time=request_time)]
    later_cw = FakeCloudWatch(alarm_response("ALARM"), series={None: [10.0]})
    sns2 = FakeSns()
    loaded = load_handler(
        fsx=FakeFsx([file_system(capacity=1127, deployment_type="SINGLE_AZ_2", ha_pairs=2,
                                 administrative_actions=actions)]),
        cloudwatch=later_cw, sns=sns2, lock_store=store, env=env,
    )
    assert loaded.handler.lambda_handler({}, None)["decision"] == "terminal"
    terminal = dict(reports(sns2))[f"T4 increase COMPLETED for {FILE_SYSTEM_ID}"]
    check(terminal["utilization"])
    # Existing-state handlers do not query again.
    assert later_cw.metric_data_calls == []


@pytest.mark.parametrize("mode", ["notify_only", "approve"])
def test_no_call_modes_carry_the_value(load_handler, mode: str) -> None:
    s3, sns = FakeS3(), FakeSns()
    cloudwatch = FakeCloudWatch(alarm_response("ALARM"), series={None: [83.0]})
    loaded = load_handler(
        fsx=FakeFsx([file_system(capacity=1024)]), cloudwatch=cloudwatch, s3=s3, sns=sns,
        env={"MODE": mode},
    )
    assert loaded.handler.lambda_handler({}, None)["decision"] == mode
    (_, _, _, body), = archived(s3)
    assert body["utilization"]["file_system"]["value"] == 83.0
    (_, report), = reports(sns)
    assert report["utilization"]["file_system"]["value"] == 83.0


def test_alarm_ok_decision_carries_the_value(load_handler) -> None:
    s3 = FakeS3()
    cloudwatch = FakeCloudWatch(alarm_response("OK"), series={None: [12.5]})
    loaded = load_handler(fsx=FakeFsx([file_system(capacity=1024)]), cloudwatch=cloudwatch, s3=s3)
    assert loaded.handler.lambda_handler({}, None)["decision"] == "alarm_not_in_alarm"
    (_, _, _, body), = archived(s3)
    assert body["alarm_states"] == {TRIGGER_ALARM: "OK"}
    assert body["utilization"]["status"] == "ok"
    assert body["utilization"]["file_system"]["value"] == 12.5


def _assert_no_bare_null(sns: FakeSns, s3: FakeS3) -> None:
    for _, body in reports(sns):
        if "utilization" in body:
            assert isinstance(body["utilization"], dict), body
    for _, _, _, body in archived(s3):
        if "utilization" in body:
            assert isinstance(body["utilization"], dict), body
    assert all('"utilization": null' not in p["Message"] for p in sns.published)


def test_no_datapoints_is_classified_and_reported(load_handler) -> None:
    s3, sns = FakeS3(), FakeSns()
    cloudwatch = FakeCloudWatch(alarm_response("ALARM"), series={None: []})
    loaded = load_handler(fsx=FakeFsx([file_system(capacity=1024)]), cloudwatch=cloudwatch,
                          s3=s3, sns=sns)
    # A report input, not a guard: the evaluation still proceeds.
    assert loaded.handler.lambda_handler({}, None)["decision"] == "submitted"
    intent = next(body for _, _, event, body in archived(s3) if event == "decision")
    utilization = intent["utilization"]
    assert utilization["status"] == "incomplete"
    assert utilization["file_system"] == {"status": "no_datapoints", "status_code": "Complete"}
    assert "value" not in utilization["file_system"]
    assert utilization["detail"] == "file_system: no_datapoints (Complete)"
    pre_call = dict(reports(sns))[f"T4 will increase {FILE_SYSTEM_ID}"]
    assert pre_call["utilization"]["status"] == "incomplete"
    _assert_no_bare_null(sns, s3)


@pytest.mark.parametrize(
    ("error", "detail"),
    [
        (make_client_error("AccessDeniedException", "GetMetricData"),
         "GetMetricData failed: AccessDeniedException"),
        (make_transport_error("https://monitoring.ap-northeast-1.amazonaws.com/"),
         "GetMetricData failed: EndpointConnectionError"),
    ],
)
def test_query_failure_is_classified_and_reported(load_handler, error, detail) -> None:
    s3, sns = FakeS3(), FakeSns()
    cloudwatch = FakeCloudWatch(alarm_response("ALARM"), metric_error=error)
    loaded = load_handler(
        fsx=FakeFsx([_second_gen()]), cloudwatch=cloudwatch, s3=s3, sns=sns,
        env={"AGGREGATE_NAMES": "aggr1"},
    )
    assert loaded.handler.lambda_handler({}, None)["decision"] == "submitted"
    intent = next(body for _, _, event, body in archived(s3) if event == "decision")
    assert intent["utilization"]["status"] == "query_failed"
    assert intent["utilization"]["detail"] == detail
    assert intent["utilization"]["file_system"] == {"status": "query_failed"}
    assert intent["utilization"]["aggregates"] == {"aggr1": {"status": "query_failed"}}
    _assert_no_bare_null(sns, s3)


def test_one_aggregate_series_error_marks_reading_incomplete(load_handler) -> None:
    s3 = FakeS3()
    cloudwatch = FakeCloudWatch(
        alarm_response("ALARM"),
        series={"aggr2": []},
        status_codes={"aggr2": "InternalError"},
    )
    loaded = load_handler(
        fsx=FakeFsx([_second_gen()]), cloudwatch=cloudwatch, s3=s3,
        env={"AGGREGATE_NAMES": "aggr1,aggr2", "MODE": "notify_only"},
    )
    assert loaded.handler.lambda_handler({}, None)["decision"] == "notify_only"
    (_, _, _, body), = archived(s3)
    utilization = body["utilization"]
    assert utilization["status"] == "incomplete"
    assert utilization["file_system"]["status"] == "ok"
    assert utilization["aggregates"]["aggr1"]["status"] == "ok"
    assert utilization["aggregates"]["aggr2"] == {
        "status": "series_error", "status_code": "InternalError",
    }
    assert utilization["detail"] == "aggregate aggr2: series_error (InternalError)"


def test_missing_result_is_classified(load_handler) -> None:
    loaded = load_handler(fsx=FakeFsx([file_system(capacity=1024)]))

    class NoResults:
        def get_metric_data(self, **_: object) -> dict:
            return {"MetricDataResults": [], "Messages": []}

    reading = loaded.handler.read_utilization(
        NoResults(), FILE_SYSTEM_ID, ("aggr1",), datetime(2026, 1, 1, tzinfo=timezone.utc)
    )
    assert reading["status"] == "incomplete"
    assert reading["file_system"] == {"status": "missing_result"}
    assert reading["aggregates"] == {"aggr1": {"status": "missing_result"}}
    assert reading["detail"] == "file_system: missing_result; aggregate aggr1: missing_result"
