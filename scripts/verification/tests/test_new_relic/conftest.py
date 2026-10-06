"""Shared pytest fixtures and Hypothesis strategies for New Relic verification tests.

Provides Hypothesis strategies for generating random instances of
NewRelicVerificationEnvironment, NRQLQueryResult, AlertConditionConfig,
and DemoScenarioTimeline dataclasses, plus sample fixtures for
New Relic verification report data.
"""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import strategies as st

from scripts.verification.models import (
    AlertConditionConfig,
    DemoScenarioTimeline,
    Issue,
    NewRelicVerificationEnvironment,
    NRQLQueryResult,
    VerificationReport,
    VerificationStep,
    VerifierInfo,
)


# ---------------------------------------------------------------------------
# Hypothesis strategies for New Relic data models
# ---------------------------------------------------------------------------


@st.composite
def iso8601_timestamp_strategy(draw: st.DrawFn) -> str:
    """Generate a valid ISO 8601 timestamp string with timezone."""
    year = draw(st.integers(min_value=2024, max_value=2030))
    month = draw(st.integers(min_value=1, max_value=12))
    day = draw(st.integers(min_value=1, max_value=28))
    hour = draw(st.integers(min_value=0, max_value=23))
    minute = draw(st.integers(min_value=0, max_value=59))
    second = draw(st.integers(min_value=0, max_value=59))
    tz_offset = draw(st.sampled_from(["+09:00", "+00:00", "-05:00", "+01:00"]))
    return f"{year:04d}-{month:02d}-{day:02d}T{hour:02d}:{minute:02d}:{second:02d}{tz_offset}"


@st.composite
def new_relic_verification_environment_strategy(
    draw: st.DrawFn,
) -> NewRelicVerificationEnvironment:
    """Generate a random NewRelicVerificationEnvironment instance."""
    aws_region = draw(
        st.sampled_from([
            "ap-northeast-1",
            "us-east-1",
            "eu-west-1",
            "us-west-2",
            "ap-southeast-1",
        ])
    )
    stack_name = draw(
        st.text(
            alphabet=st.characters(
                whitelist_categories=("L", "N"),
                blacklist_characters="\n\r",
            ),
            min_size=3,
            max_size=20,
        ).map(lambda s: f"fsxn-{s.strip() or 'nr'}-integration")
    )
    lambda_function_name = draw(
        st.text(
            alphabet=st.characters(
                whitelist_categories=("L", "N"),
                blacklist_characters="\n\r",
            ),
            min_size=3,
            max_size=20,
        ).map(lambda s: f"fsxn-{s.strip() or 'nr'}-shipper")
    )
    new_relic_region = draw(st.sampled_from(["US", "EU"]))
    # Masked IDs: last 4 digits preceded by asterisks
    nr_last4 = draw(st.from_regex(r"[0-9]{4}", fullmatch=True))
    aws_last4 = draw(st.from_regex(r"[0-9]{4}", fullmatch=True))
    fsx_id = draw(
        st.from_regex(r"fs-[0-9a-f]{17}", fullmatch=True)
    )
    return NewRelicVerificationEnvironment(
        aws_region=aws_region,
        stack_name=stack_name,
        lambda_function_name=lambda_function_name,
        new_relic_region=new_relic_region,
        new_relic_account_id_masked=f"****{nr_last4}",
        aws_account_id_masked=f"****{aws_last4}",
        fsx_file_system_id=fsx_id,
    )


@st.composite
def nrql_query_result_strategy(draw: st.DrawFn) -> NRQLQueryResult:
    """Generate a random NRQLQueryResult instance."""
    query = draw(
        st.sampled_from([
            "SELECT count(*) FROM Log WHERE source='fsxn-ontap' SINCE 1 hour ago",
            "SELECT count(*) FROM Log WHERE source='fsxn-ontap' FACET operation SINCE 1 hour ago",
            "SELECT count(*) FROM Log WHERE source='fsxn-ontap' AND result='Failure'",
            "SELECT * FROM Log WHERE source='fsxn-ontap' LIMIT 10",
            "SELECT count(*) FROM Log WHERE source='fsxn-ontap' AND event_type LIKE 'wafl.quota%' SINCE 1 hour ago",
        ])
    )
    result_summary = draw(
        st.text(
            alphabet=st.characters(
                whitelist_categories=("L", "N", "P", "Z"),
                blacklist_characters="\n\r",
            ),
            min_size=3,
            max_size=50,
        ).map(lambda s: s.strip() or "count: 5")
    )
    row_count = draw(st.integers(min_value=0, max_value=100))
    # Generate first_rows as a list of simple dicts
    first_rows = draw(
        st.lists(
            st.fixed_dictionaries({
                "count": st.integers(min_value=0, max_value=1000).map(str),
                "operation": st.sampled_from([
                    "CREATE", "READ", "WRITE", "DELETE", "RENAME", "OPEN",
                ]),
            }),
            min_size=0,
            max_size=5,
        )
    )
    execution_timestamp = draw(iso8601_timestamp_strategy())
    status = draw(st.sampled_from(["pass", "fail"]))
    error_reason = draw(
        st.one_of(
            st.none(),
            st.text(
                alphabet=st.characters(
                    whitelist_categories=("L", "N", "P", "Z"),
                    blacklist_characters="\n\r",
                ),
                min_size=5,
                max_size=40,
            ).map(lambda s: s.strip() or "Query timeout"),
        )
    )
    retry_count = draw(st.integers(min_value=0, max_value=3))
    return NRQLQueryResult(
        query=query,
        result_summary=result_summary,
        row_count=row_count,
        first_rows=first_rows,
        execution_timestamp=execution_timestamp,
        status=status,
        error_reason=error_reason,
        retry_count=retry_count,
    )


@st.composite
def alert_condition_config_strategy(draw: st.DrawFn) -> AlertConditionConfig:
    """Generate a random AlertConditionConfig instance."""
    nrql_query = draw(
        st.sampled_from([
            "SELECT count(*) FROM Log WHERE source='fsxn-ontap' AND result='Failure'",
            "SELECT count(*) FROM Log WHERE source='fsxn-ontap' AND event_type LIKE 'wafl.quota%'",
            "SELECT count(*) FROM Log WHERE source='fsxn-ontap' AND operation='DELETE'",
        ])
    )
    threshold_value = draw(st.integers(min_value=1, max_value=100))
    evaluation_window_minutes = draw(st.sampled_from([1, 5, 10, 15, 30, 60]))
    notification_channel = draw(
        st.sampled_from([
            "email-ops-team",
            "slack-alerts",
            "pagerduty-critical",
            "webhook-incident",
        ])
    )
    test_trigger_timestamp = draw(
        st.one_of(st.none(), iso8601_timestamp_strategy())
    )
    notification_receipt_timestamp = draw(
        st.one_of(st.none(), iso8601_timestamp_strategy())
    )
    return AlertConditionConfig(
        nrql_query=nrql_query,
        threshold_value=threshold_value,
        evaluation_window_minutes=evaluation_window_minutes,
        notification_channel=notification_channel,
        test_trigger_timestamp=test_trigger_timestamp,
        notification_receipt_timestamp=notification_receipt_timestamp,
    )


@st.composite
def demo_scenario_timeline_strategy(draw: st.DrawFn) -> DemoScenarioTimeline:
    """Generate a random DemoScenarioTimeline instance."""
    file_write_timestamp = draw(iso8601_timestamp_strategy())
    ems_event_timestamp = draw(
        st.one_of(st.none(), iso8601_timestamp_strategy())
    )
    s3_object_creation_timestamp = draw(
        st.one_of(st.none(), iso8601_timestamp_strategy())
    )
    lambda_invocation_timestamp = draw(
        st.one_of(st.none(), iso8601_timestamp_strategy())
    )
    new_relic_log_arrival_timestamp = draw(
        st.one_of(st.none(), iso8601_timestamp_strategy())
    )
    scenario_status = draw(st.sampled_from(["pass", "fail"]))
    last_successful_stage = draw(
        st.one_of(
            st.none(),
            st.sampled_from([
                "file_write",
                "ems_event",
                "s3_object_creation",
                "lambda_invocation",
                "new_relic_log_arrival",
            ]),
        )
    )
    failing_stage = draw(
        st.one_of(
            st.none(),
            st.sampled_from([
                "ems_event",
                "s3_object_creation",
                "lambda_invocation",
                "new_relic_log_arrival",
            ]),
        )
    )
    elapsed_at_failure = draw(
        st.one_of(
            st.none(),
            st.floats(min_value=0.1, max_value=300.0, allow_nan=False, allow_infinity=False),
        )
    )
    return DemoScenarioTimeline(
        file_write_timestamp=file_write_timestamp,
        ems_event_timestamp=ems_event_timestamp,
        s3_object_creation_timestamp=s3_object_creation_timestamp,
        lambda_invocation_timestamp=lambda_invocation_timestamp,
        new_relic_log_arrival_timestamp=new_relic_log_arrival_timestamp,
        scenario_status=scenario_status,
        last_successful_stage=last_successful_stage,
        failing_stage=failing_stage,
        elapsed_at_failure=elapsed_at_failure,
    )


# ---------------------------------------------------------------------------
# pytest fixtures for sample New Relic verification report data
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_new_relic_environment() -> NewRelicVerificationEnvironment:
    """Sample New Relic verification environment for unit tests."""
    return NewRelicVerificationEnvironment(
        aws_region="ap-northeast-1",
        stack_name="fsxn-new-relic-integration",
        lambda_function_name="fsxn-new-relic-integration-shipper",
        new_relic_region="US",
        new_relic_account_id_masked="****4567",
        aws_account_id_masked="****9012",
        fsx_file_system_id="fs-0123456789abcdef0",
    )


@pytest.fixture
def sample_nrql_results() -> list[NRQLQueryResult]:
    """Sample NRQL query results for unit tests."""
    return [
        NRQLQueryResult(
            query="SELECT count(*) FROM Log WHERE source='fsxn-ontap' SINCE 1 hour ago",
            result_summary="count: 42",
            row_count=1,
            first_rows=[{"count": "42"}],
            execution_timestamp="2026-01-20T10:30:00+09:00",
            status="pass",
            error_reason=None,
            retry_count=0,
        ),
        NRQLQueryResult(
            query="SELECT count(*) FROM Log WHERE source='fsxn-ontap' FACET operation SINCE 1 hour ago",
            result_summary="CREATE: 15, READ: 20, DELETE: 7",
            row_count=3,
            first_rows=[
                {"count": "15", "operation": "CREATE"},
                {"count": "20", "operation": "READ"},
                {"count": "7", "operation": "DELETE"},
            ],
            execution_timestamp="2026-01-20T10:31:00+09:00",
            status="pass",
            error_reason=None,
            retry_count=0,
        ),
    ]


@pytest.fixture
def sample_alert_condition() -> AlertConditionConfig:
    """Sample alert condition configuration for unit tests."""
    return AlertConditionConfig(
        nrql_query="SELECT count(*) FROM Log WHERE source='fsxn-ontap' AND result='Failure'",
        threshold_value=1,
        evaluation_window_minutes=5,
        notification_channel="slack-alerts",
        test_trigger_timestamp="2026-01-20T10:45:00+09:00",
        notification_receipt_timestamp="2026-01-20T10:47:30+09:00",
    )


@pytest.fixture
def sample_demo_timeline() -> DemoScenarioTimeline:
    """Sample demo scenario timeline for unit tests."""
    return DemoScenarioTimeline(
        file_write_timestamp="2026-01-20T11:00:00+09:00",
        ems_event_timestamp="2026-01-20T11:00:05+09:00",
        s3_object_creation_timestamp="2026-01-20T11:00:30+09:00",
        lambda_invocation_timestamp="2026-01-20T11:01:00+09:00",
        new_relic_log_arrival_timestamp="2026-01-20T11:01:45+09:00",
        scenario_status="pass",
        last_successful_stage="new_relic_log_arrival",
        failing_stage=None,
        elapsed_at_failure=None,
    )


@pytest.fixture
def sample_new_relic_verification_steps() -> list[VerificationStep]:
    """Sample verification steps for New Relic E2E tests."""
    return [
        VerificationStep(
            step_number=1,
            step_name="Deploy CloudFormation Stack",
            result="success",
            command="aws cloudformation deploy --template-file template.yaml --stack-name fsxn-new-relic-integration --capabilities CAPABILITY_IAM",
            output="Stack fsxn-new-relic-integration created successfully.",
            screenshot_path=None,
            timestamp="2026-01-20T10:00:00+09:00",
        ),
        VerificationStep(
            step_number=2,
            step_name="Invoke Lambda with Test Event",
            result="success",
            command="aws lambda invoke --function-name fsxn-new-relic-integration-shipper --payload file://test-event.json response.json",
            output='{"statusCode": 200, "body": {"total_logs": 10, "total_shipped": 10, "errors": []}}',
            screenshot_path=None,
            timestamp="2026-01-20T10:05:00+09:00",
        ),
        VerificationStep(
            step_number=3,
            step_name="Verify New Relic Log Arrival",
            result="success",
            command=None,
            output="Found 10 logs matching source='fsxn-ontap' within 2 minutes.",
            screenshot_path="docs/screenshots/new-relic/logs-ui-arrival.png",
            timestamp="2026-01-20T10:10:00+09:00",
        ),
        VerificationStep(
            step_number=4,
            step_name="Execute NRQL Queries",
            result="success",
            command=None,
            output="All NRQL queries returned non-zero results.",
            screenshot_path="docs/screenshots/new-relic/nrql-query-result.png",
            timestamp="2026-01-20T10:15:00+09:00",
        ),
        VerificationStep(
            step_number=5,
            step_name="Configure Alert Condition",
            result="success",
            command=None,
            output="Alert condition created and notification received.",
            screenshot_path="docs/screenshots/new-relic/alert-condition-config.png",
            timestamp="2026-01-20T10:20:00+09:00",
        ),
    ]


@pytest.fixture
def sample_new_relic_report(
    sample_new_relic_verification_steps: list[VerificationStep],
) -> VerificationReport:
    """Sample complete New Relic verification report for unit tests."""
    return VerificationReport(
        verification_date="2026-01-20T10:00:00+09:00",
        environment=None,  # type: ignore[arg-type]
        verifier=VerifierInfo(name="リポジトリ管理者", role="DevOps Engineer"),
        steps=sample_new_relic_verification_steps,
        issues=[],
    )


@pytest.fixture
def sample_new_relic_report_with_failures() -> VerificationReport:
    """Sample New Relic verification report with some failed steps."""
    return VerificationReport(
        verification_date="2026-01-20T10:00:00+09:00",
        environment=None,  # type: ignore[arg-type]
        verifier=VerifierInfo(name="リポジトリ管理者", role="DevOps Engineer"),
        steps=[
            VerificationStep(
                step_number=1,
                step_name="Deploy CloudFormation Stack",
                result="success",
                command="aws cloudformation deploy --template-file template.yaml",
                output="Stack created successfully.",
                timestamp="2026-01-20T10:00:00+09:00",
            ),
            VerificationStep(
                step_number=2,
                step_name="Verify New Relic Log Arrival",
                result="failure",
                command=None,
                output="Timeout: No logs found within 2 minutes.",
                error_detail="Log arrival timeout exceeded 120 seconds",
                timestamp="2026-01-20T10:05:00+09:00",
            ),
            VerificationStep(
                step_number=3,
                step_name="Execute NRQL Queries",
                result="failure",
                command=None,
                output="Query returned 0 results after 3 retries.",
                error_detail="NRQL query returned empty result set",
                timestamp="2026-01-20T10:10:00+09:00",
            ),
        ],
        issues=[
            Issue(
                title="ログ到着タイムアウト",
                description="New Relic Log API へのログ配信が2分以内に完了しなかった",
                resolution="Lambda 関数のタイムアウト設定を確認し、New Relic License Key の有効性を検証する",
            ),
        ],
    )
