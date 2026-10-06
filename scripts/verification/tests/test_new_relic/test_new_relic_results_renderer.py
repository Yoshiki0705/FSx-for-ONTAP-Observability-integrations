"""Unit tests for the New Relic verification results renderer.

Tests render_new_relic_report and mask_account_id functions for correct
Markdown output generation across various input scenarios including
NRQL results, alert configuration, demo timeline, ID masking, and
conclusion logic.

Requirements: 1.6, 2.6, 3.5, 4.6, 5.6, 8.5, 9.2, 9.3, 9.4, 9.5, 9.6
"""

from __future__ import annotations

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
from scripts.verification.new_relic_results_renderer import (
    mask_account_id,
    render_new_relic_report,
)


# ---------------------------------------------------------------------------
# Helper fixtures (inline for clarity)
# ---------------------------------------------------------------------------


def _render_full_report(
    report: VerificationReport,
    nrql_results: list[NRQLQueryResult],
    alert_config: AlertConditionConfig,
    demo_timeline: DemoScenarioTimeline,
    env: NewRelicVerificationEnvironment,
) -> str:
    """Shortcut to render a full report."""
    return render_new_relic_report(report, nrql_results, alert_config, demo_timeline, env)


# ---------------------------------------------------------------------------
# Test: Full report rendering with all New Relic-specific fields
# ---------------------------------------------------------------------------


class TestRenderFullReport:
    """Test full report rendering with all New Relic-specific fields."""

    def test_contains_verification_date(
        self,
        sample_new_relic_report: VerificationReport,
        sample_nrql_results: list[NRQLQueryResult],
        sample_alert_condition: AlertConditionConfig,
        sample_demo_timeline: DemoScenarioTimeline,
        sample_new_relic_environment: NewRelicVerificationEnvironment,
    ) -> None:
        """Rendered report contains the ISO 8601 verification date."""
        output = _render_full_report(
            sample_new_relic_report,
            sample_nrql_results,
            sample_alert_condition,
            sample_demo_timeline,
            sample_new_relic_environment,
        )
        assert "2026-01-20T10:00:00+09:00" in output

    def test_contains_environment_info(
        self,
        sample_new_relic_report: VerificationReport,
        sample_nrql_results: list[NRQLQueryResult],
        sample_alert_condition: AlertConditionConfig,
        sample_demo_timeline: DemoScenarioTimeline,
        sample_new_relic_environment: NewRelicVerificationEnvironment,
    ) -> None:
        """Rendered report contains environment details."""
        output = _render_full_report(
            sample_new_relic_report,
            sample_nrql_results,
            sample_alert_condition,
            sample_demo_timeline,
            sample_new_relic_environment,
        )
        assert "ap-northeast-1" in output
        assert "fsxn-new-relic-integration" in output
        assert "fsxn-new-relic-integration-shipper" in output
        assert "US" in output
        assert "fs-0123456789abcdef0" in output

    def test_contains_verifier_info(
        self,
        sample_new_relic_report: VerificationReport,
        sample_nrql_results: list[NRQLQueryResult],
        sample_alert_condition: AlertConditionConfig,
        sample_demo_timeline: DemoScenarioTimeline,
        sample_new_relic_environment: NewRelicVerificationEnvironment,
    ) -> None:
        """Rendered report contains verifier name and role."""
        output = _render_full_report(
            sample_new_relic_report,
            sample_nrql_results,
            sample_alert_condition,
            sample_demo_timeline,
            sample_new_relic_environment,
        )
        assert "リポジトリ管理者" in output
        assert "DevOps Engineer" in output

    def test_contains_step_numbers_and_names(
        self,
        sample_new_relic_report: VerificationReport,
        sample_nrql_results: list[NRQLQueryResult],
        sample_alert_condition: AlertConditionConfig,
        sample_demo_timeline: DemoScenarioTimeline,
        sample_new_relic_environment: NewRelicVerificationEnvironment,
    ) -> None:
        """Rendered report contains each step's number and name."""
        output = _render_full_report(
            sample_new_relic_report,
            sample_nrql_results,
            sample_alert_condition,
            sample_demo_timeline,
            sample_new_relic_environment,
        )
        assert "ステップ 1" in output
        assert "Deploy CloudFormation Stack" in output
        assert "ステップ 2" in output
        assert "Invoke Lambda with Test Event" in output
        assert "ステップ 3" in output
        assert "Verify New Relic Log Arrival" in output

    def test_contains_nrql_query_text(
        self,
        sample_new_relic_report: VerificationReport,
        sample_nrql_results: list[NRQLQueryResult],
        sample_alert_condition: AlertConditionConfig,
        sample_demo_timeline: DemoScenarioTimeline,
        sample_new_relic_environment: NewRelicVerificationEnvironment,
    ) -> None:
        """Rendered report contains NRQL query text in code blocks."""
        output = _render_full_report(
            sample_new_relic_report,
            sample_nrql_results,
            sample_alert_condition,
            sample_demo_timeline,
            sample_new_relic_environment,
        )
        assert "SELECT count(*) FROM Log WHERE source='fsxn-ontap' SINCE 1 hour ago" in output
        assert "SELECT count(*) FROM Log WHERE source='fsxn-ontap' FACET operation SINCE 1 hour ago" in output

    def test_contains_alert_config_details(
        self,
        sample_new_relic_report: VerificationReport,
        sample_nrql_results: list[NRQLQueryResult],
        sample_alert_condition: AlertConditionConfig,
        sample_demo_timeline: DemoScenarioTimeline,
        sample_new_relic_environment: NewRelicVerificationEnvironment,
    ) -> None:
        """Rendered report contains alert condition configuration."""
        output = _render_full_report(
            sample_new_relic_report,
            sample_nrql_results,
            sample_alert_condition,
            sample_demo_timeline,
            sample_new_relic_environment,
        )
        assert "SELECT count(*) FROM Log WHERE source='fsxn-ontap' AND result='Failure'" in output
        assert "slack-alerts" in output
        assert "5 分" in output

    def test_contains_demo_timeline(
        self,
        sample_new_relic_report: VerificationReport,
        sample_nrql_results: list[NRQLQueryResult],
        sample_alert_condition: AlertConditionConfig,
        sample_demo_timeline: DemoScenarioTimeline,
        sample_new_relic_environment: NewRelicVerificationEnvironment,
    ) -> None:
        """Rendered report contains demo scenario timeline timestamps."""
        output = _render_full_report(
            sample_new_relic_report,
            sample_nrql_results,
            sample_alert_condition,
            sample_demo_timeline,
            sample_new_relic_environment,
        )
        assert "2026-01-20T11:00:00+09:00" in output
        assert "2026-01-20T11:00:05+09:00" in output
        assert "2026-01-20T11:01:45+09:00" in output


# ---------------------------------------------------------------------------
# Test: ID masking
# ---------------------------------------------------------------------------


class TestIDMasking:
    """Test ID masking (AWS account and New Relic account)."""

    def test_mask_aws_account_id_12_digits(self) -> None:
        """AWS account ID '123456789012' is masked to '****9012'."""
        assert mask_account_id("123456789012") == "****9012"

    def test_mask_new_relic_account_id_7_digits(self) -> None:
        """New Relic account ID '1234567' is masked to '****4567'."""
        assert mask_account_id("1234567") == "****4567"

    def test_mask_short_id_4_digits(self) -> None:
        """Short ID (4 digits) is masked to '****' + full ID."""
        assert mask_account_id("9012") == "****9012"

    def test_mask_short_id_3_digits(self) -> None:
        """Very short ID (3 digits) is masked to '****' + full ID."""
        assert mask_account_id("123") == "****123"

    def test_masked_ids_in_rendered_report(
        self,
        sample_new_relic_report: VerificationReport,
        sample_nrql_results: list[NRQLQueryResult],
        sample_alert_condition: AlertConditionConfig,
        sample_demo_timeline: DemoScenarioTimeline,
        sample_new_relic_environment: NewRelicVerificationEnvironment,
    ) -> None:
        """Rendered report shows masked IDs from environment."""
        output = _render_full_report(
            sample_new_relic_report,
            sample_nrql_results,
            sample_alert_condition,
            sample_demo_timeline,
            sample_new_relic_environment,
        )
        # Environment has pre-masked IDs: ****4567 and ****9012
        assert "****4567" in output
        assert "****9012" in output


# ---------------------------------------------------------------------------
# Test: Conclusion text
# ---------------------------------------------------------------------------


class TestConclusionText:
    """Test conclusion logic: all-pass vs some-fail."""

    def test_all_pass_produces_production_ready(
        self,
        sample_new_relic_report: VerificationReport,
        sample_nrql_results: list[NRQLQueryResult],
        sample_alert_condition: AlertConditionConfig,
        sample_demo_timeline: DemoScenarioTimeline,
        sample_new_relic_environment: NewRelicVerificationEnvironment,
    ) -> None:
        """All steps passing produces '本番環境利用可能'."""
        output = _render_full_report(
            sample_new_relic_report,
            sample_nrql_results,
            sample_alert_condition,
            sample_demo_timeline,
            sample_new_relic_environment,
        )
        assert "本番環境利用可能" in output
        assert "本番環境利用不可" not in output

    def test_some_fail_produces_not_production_ready(
        self,
        sample_new_relic_report_with_failures: VerificationReport,
        sample_nrql_results: list[NRQLQueryResult],
        sample_alert_condition: AlertConditionConfig,
        sample_demo_timeline: DemoScenarioTimeline,
        sample_new_relic_environment: NewRelicVerificationEnvironment,
    ) -> None:
        """Some failed steps produce '本番環境利用不可'."""
        output = _render_full_report(
            sample_new_relic_report_with_failures,
            sample_nrql_results,
            sample_alert_condition,
            sample_demo_timeline,
            sample_new_relic_environment,
        )
        assert "本番環境利用不可" in output

    def test_failed_step_ids_listed_in_conclusion(
        self,
        sample_new_relic_report_with_failures: VerificationReport,
        sample_nrql_results: list[NRQLQueryResult],
        sample_alert_condition: AlertConditionConfig,
        sample_demo_timeline: DemoScenarioTimeline,
        sample_new_relic_environment: NewRelicVerificationEnvironment,
    ) -> None:
        """Failed step numbers and names are listed in conclusion."""
        output = _render_full_report(
            sample_new_relic_report_with_failures,
            sample_nrql_results,
            sample_alert_condition,
            sample_demo_timeline,
            sample_new_relic_environment,
        )
        assert "ステップ 2" in output
        assert "Verify New Relic Log Arrival" in output
        assert "ステップ 3" in output
        assert "Execute NRQL Queries" in output


# ---------------------------------------------------------------------------
# Test: Empty issues → "問題なし"
# ---------------------------------------------------------------------------


class TestEmptyIssues:
    """Test that empty issues list renders '問題なし'."""

    def test_empty_issues_renders_mondainashi(
        self,
        sample_new_relic_report: VerificationReport,
        sample_nrql_results: list[NRQLQueryResult],
        sample_alert_condition: AlertConditionConfig,
        sample_demo_timeline: DemoScenarioTimeline,
        sample_new_relic_environment: NewRelicVerificationEnvironment,
    ) -> None:
        """Empty issues list produces '問題なし' in output."""
        # sample_new_relic_report has issues=[]
        output = _render_full_report(
            sample_new_relic_report,
            sample_nrql_results,
            sample_alert_condition,
            sample_demo_timeline,
            sample_new_relic_environment,
        )
        assert "問題なし" in output

    def test_non_empty_issues_does_not_render_mondainashi(
        self,
        sample_new_relic_report_with_failures: VerificationReport,
        sample_nrql_results: list[NRQLQueryResult],
        sample_alert_condition: AlertConditionConfig,
        sample_demo_timeline: DemoScenarioTimeline,
        sample_new_relic_environment: NewRelicVerificationEnvironment,
    ) -> None:
        """Non-empty issues list does NOT produce '問題なし'."""
        output = _render_full_report(
            sample_new_relic_report_with_failures,
            sample_nrql_results,
            sample_alert_condition,
            sample_demo_timeline,
            sample_new_relic_environment,
        )
        assert "問題なし" not in output


# ---------------------------------------------------------------------------
# Test: Steps with/without screenshots
# ---------------------------------------------------------------------------


class TestScreenshots:
    """Test steps with/without screenshots produce correct image links."""

    def test_step_with_screenshot_has_image_link(
        self,
        sample_new_relic_report: VerificationReport,
        sample_nrql_results: list[NRQLQueryResult],
        sample_alert_condition: AlertConditionConfig,
        sample_demo_timeline: DemoScenarioTimeline,
        sample_new_relic_environment: NewRelicVerificationEnvironment,
    ) -> None:
        """Steps with screenshot_path produce Markdown image links."""
        output = _render_full_report(
            sample_new_relic_report,
            sample_nrql_results,
            sample_alert_condition,
            sample_demo_timeline,
            sample_new_relic_environment,
        )
        # Step 3 has screenshot_path="docs/screenshots/new-relic/logs-ui-arrival.png"
        assert "![Verify New Relic Log Arrival](docs/screenshots/new-relic/logs-ui-arrival.png)" in output

    def test_step_without_screenshot_has_no_image_link_for_that_step(
        self,
        sample_nrql_results: list[NRQLQueryResult],
        sample_alert_condition: AlertConditionConfig,
        sample_demo_timeline: DemoScenarioTimeline,
        sample_new_relic_environment: NewRelicVerificationEnvironment,
    ) -> None:
        """Steps without screenshot_path do not produce image links."""
        report = VerificationReport(
            verification_date="2026-01-20T10:00:00+09:00",
            environment=None,  # type: ignore[arg-type]
            verifier=VerifierInfo(name="テスト太郎", role="Engineer"),
            steps=[
                VerificationStep(
                    step_number=1,
                    step_name="Deploy Stack",
                    result="success",
                    command="aws cloudformation deploy",
                    output="Stack created.",
                    screenshot_path=None,
                    timestamp="2026-01-20T10:00:00+09:00",
                ),
            ],
            issues=[],
        )
        output = _render_full_report(
            report,
            sample_nrql_results,
            sample_alert_condition,
            sample_demo_timeline,
            sample_new_relic_environment,
        )
        assert "![Deploy Stack]" not in output


# ---------------------------------------------------------------------------
# Test: NRQL query results formatting
# ---------------------------------------------------------------------------


class TestNRQLResultsFormatting:
    """Test NRQL query results formatting (query text, result summary, timestamp)."""

    def test_nrql_query_text_in_sql_code_block(
        self,
        sample_new_relic_report: VerificationReport,
        sample_nrql_results: list[NRQLQueryResult],
        sample_alert_condition: AlertConditionConfig,
        sample_demo_timeline: DemoScenarioTimeline,
        sample_new_relic_environment: NewRelicVerificationEnvironment,
    ) -> None:
        """NRQL queries are rendered in SQL code blocks."""
        output = _render_full_report(
            sample_new_relic_report,
            sample_nrql_results,
            sample_alert_condition,
            sample_demo_timeline,
            sample_new_relic_environment,
        )
        assert "```sql" in output
        assert "SELECT count(*) FROM Log WHERE source='fsxn-ontap' SINCE 1 hour ago" in output

    def test_nrql_result_summary_present(
        self,
        sample_new_relic_report: VerificationReport,
        sample_nrql_results: list[NRQLQueryResult],
        sample_alert_condition: AlertConditionConfig,
        sample_demo_timeline: DemoScenarioTimeline,
        sample_new_relic_environment: NewRelicVerificationEnvironment,
    ) -> None:
        """NRQL result summaries are present in output."""
        output = _render_full_report(
            sample_new_relic_report,
            sample_nrql_results,
            sample_alert_condition,
            sample_demo_timeline,
            sample_new_relic_environment,
        )
        assert "count: 42" in output
        assert "CREATE: 15, READ: 20, DELETE: 7" in output

    def test_nrql_execution_timestamp_present(
        self,
        sample_new_relic_report: VerificationReport,
        sample_nrql_results: list[NRQLQueryResult],
        sample_alert_condition: AlertConditionConfig,
        sample_demo_timeline: DemoScenarioTimeline,
        sample_new_relic_environment: NewRelicVerificationEnvironment,
    ) -> None:
        """NRQL execution timestamps are present in output."""
        output = _render_full_report(
            sample_new_relic_report,
            sample_nrql_results,
            sample_alert_condition,
            sample_demo_timeline,
            sample_new_relic_environment,
        )
        assert "2026-01-20T10:30:00+09:00" in output
        assert "2026-01-20T10:31:00+09:00" in output

    def test_nrql_pass_status_badge(
        self,
        sample_new_relic_report: VerificationReport,
        sample_nrql_results: list[NRQLQueryResult],
        sample_alert_condition: AlertConditionConfig,
        sample_demo_timeline: DemoScenarioTimeline,
        sample_new_relic_environment: NewRelicVerificationEnvironment,
    ) -> None:
        """NRQL pass status shows pass badge."""
        output = _render_full_report(
            sample_new_relic_report,
            sample_nrql_results,
            sample_alert_condition,
            sample_demo_timeline,
            sample_new_relic_environment,
        )
        assert "✅ PASS" in output

    def test_nrql_fail_status_badge(
        self,
        sample_new_relic_report: VerificationReport,
        sample_alert_condition: AlertConditionConfig,
        sample_demo_timeline: DemoScenarioTimeline,
        sample_new_relic_environment: NewRelicVerificationEnvironment,
    ) -> None:
        """NRQL fail status shows fail badge."""
        failed_nrql = [
            NRQLQueryResult(
                query="SELECT count(*) FROM Log WHERE source='fsxn-ontap'",
                result_summary="count: 0",
                row_count=0,
                first_rows=[],
                execution_timestamp="2026-01-20T10:30:00+09:00",
                status="fail",
                error_reason="Query returned 0 results after 3 retries",
                retry_count=3,
            ),
        ]
        output = _render_full_report(
            sample_new_relic_report,
            failed_nrql,
            sample_alert_condition,
            sample_demo_timeline,
            sample_new_relic_environment,
        )
        assert "❌ FAIL" in output
        assert "Query returned 0 results after 3 retries" in output
        assert "3" in output  # retry_count

    def test_nrql_row_count_present(
        self,
        sample_new_relic_report: VerificationReport,
        sample_nrql_results: list[NRQLQueryResult],
        sample_alert_condition: AlertConditionConfig,
        sample_demo_timeline: DemoScenarioTimeline,
        sample_new_relic_environment: NewRelicVerificationEnvironment,
    ) -> None:
        """NRQL row counts are present in output."""
        output = _render_full_report(
            sample_new_relic_report,
            sample_nrql_results,
            sample_alert_condition,
            sample_demo_timeline,
            sample_new_relic_environment,
        )
        # First query has row_count=1, second has row_count=3
        assert "1" in output
        assert "3" in output
