"""Integration tests for the full New Relic verification pipeline.

Tests the end-to-end flow of:
1. Bilingual comparison → New Relic results renderer
2. Screenshot validation → results renderer
3. Full report generation with all New Relic components wired together

Uses tmp_path fixture for file isolation.
"""

from __future__ import annotations

import os

import pytest

from scripts.verification.bilingual_comparator import compare_setup_guides
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
    render_new_relic_report,
)
from scripts.verification.screenshot_validator import (
    validate_new_relic_screenshots,
)

# PNG magic bytes followed by enough padding to exceed 1KB minimum
PNG_MAGIC_BYTES = b"\x89PNG\r\n\x1a\n"


def _create_valid_png(path: str, size: int = 2048) -> None:
    """Create a valid PNG file with the correct magic bytes and minimum size."""
    with open(path, "wb") as f:
        f.write(PNG_MAGIC_BYTES)
        f.write(b"\x00" * (size - len(PNG_MAGIC_BYTES)))


class TestBilingualComparisonToRendererPipeline:
    """Test bilingual comparison → New Relic results renderer pipeline."""

    def test_matching_bilingual_docs_produce_pass_in_report(
        self, tmp_path: pytest.TempPathFactory
    ) -> None:
        """Bilingual comparison pass result feeds into renderer correctly."""
        # Create matching ja/en Markdown files
        ja_content = (
            "# セットアップガイド\n\n"
            "## 前提条件\n\n"
            "以下のリソースが必要です。\n\n"
            "```bash\n"
            "aws cloudformation deploy --stack-name fsxn-new-relic\n"
            "```\n\n"
            "## デプロイ\n\n"
            "手順に従ってください。\n"
        )
        en_content = (
            "# Setup Guide\n\n"
            "## Prerequisites\n\n"
            "The following resources are required.\n\n"
            "```bash\n"
            "aws cloudformation deploy --stack-name fsxn-new-relic\n"
            "```\n\n"
            "## Deployment\n\n"
            "Follow the steps below.\n"
        )

        ja_path = str(tmp_path / "ja" / "setup-guide.md")
        en_path = str(tmp_path / "en" / "setup-guide.md")
        os.makedirs(os.path.dirname(ja_path), exist_ok=True)
        os.makedirs(os.path.dirname(en_path), exist_ok=True)

        with open(ja_path, "w", encoding="utf-8") as f:
            f.write(ja_content)
        with open(en_path, "w", encoding="utf-8") as f:
            f.write(en_content)

        # Run bilingual comparison
        comparison_result = compare_setup_guides(ja_path, en_path)
        assert comparison_result.status == "pass"

        # Feed into renderer as a verification step
        bilingual_step = VerificationStep(
            step_number=6,
            step_name="Bilingual Comparison",
            result="success" if comparison_result.status == "pass" else "failure",
            command=f"python3 scripts/compare-bilingual.py --ja {ja_path} --en {en_path}",
            output=f"Status: {comparison_result.status}, Differences: {len(comparison_result.differences)}",
            timestamp="2026-01-20T12:00:00+09:00",
        )

        report = VerificationReport(
            verification_date="2026-01-20T12:00:00+09:00",
            environment=None,  # type: ignore[arg-type]
            verifier=VerifierInfo(name="テスト太郎", role="Engineer"),
            steps=[bilingual_step],
            issues=[],
        )

        env = NewRelicVerificationEnvironment(
            aws_region="ap-northeast-1",
            stack_name="fsxn-new-relic-integration",
            lambda_function_name="fsxn-new-relic-integration-shipper",
            new_relic_region="US",
            new_relic_account_id_masked="****1234",
            aws_account_id_masked="****5678",
            fsx_file_system_id="fs-0123456789abcdef0",
        )

        nrql_results: list[NRQLQueryResult] = []
        alert_config = AlertConditionConfig(
            nrql_query="SELECT count(*) FROM Log WHERE source='fsxn-ontap' AND result='Failure'",
            threshold_value=1,
            evaluation_window_minutes=5,
            notification_channel="slack-alerts",
        )
        demo_timeline = DemoScenarioTimeline(
            file_write_timestamp="2026-01-20T11:00:00+09:00",
            scenario_status="pass",
        )

        rendered = render_new_relic_report(
            report, nrql_results, alert_config, demo_timeline, env
        )

        # Verify bilingual section appears in rendered output
        assert "Bilingual Comparison" in rendered
        assert "✅ 成功" in rendered
        assert "本番環境利用可能" in rendered

    def test_mismatched_bilingual_docs_produce_failure_in_report(
        self, tmp_path: pytest.TempPathFactory
    ) -> None:
        """Bilingual comparison fail result feeds into renderer correctly."""
        # Create mismatched ja/en Markdown files (different heading count)
        ja_content = (
            "# セットアップガイド\n\n"
            "## 前提条件\n\n"
            "## デプロイ\n\n"
            "## 確認\n\n"
        )
        en_content = (
            "# Setup Guide\n\n"
            "## Prerequisites\n\n"
        )

        ja_path = str(tmp_path / "ja" / "setup-guide.md")
        en_path = str(tmp_path / "en" / "setup-guide.md")
        os.makedirs(os.path.dirname(ja_path), exist_ok=True)
        os.makedirs(os.path.dirname(en_path), exist_ok=True)

        with open(ja_path, "w", encoding="utf-8") as f:
            f.write(ja_content)
        with open(en_path, "w", encoding="utf-8") as f:
            f.write(en_content)

        # Run bilingual comparison
        comparison_result = compare_setup_guides(ja_path, en_path)
        assert comparison_result.status == "fail"
        assert len(comparison_result.differences) > 0

        # Feed into renderer as a failed verification step
        bilingual_step = VerificationStep(
            step_number=6,
            step_name="Bilingual Comparison",
            result="failure",
            command=f"python3 scripts/compare-bilingual.py --ja {ja_path} --en {en_path}",
            output=f"Status: {comparison_result.status}, Differences: {len(comparison_result.differences)}",
            error_detail=f"Found {len(comparison_result.differences)} structural difference(s)",
            timestamp="2026-01-20T12:00:00+09:00",
        )

        report = VerificationReport(
            verification_date="2026-01-20T12:00:00+09:00",
            environment=None,  # type: ignore[arg-type]
            verifier=VerifierInfo(name="テスト太郎", role="Engineer"),
            steps=[bilingual_step],
            issues=[],
        )

        env = NewRelicVerificationEnvironment(
            aws_region="ap-northeast-1",
            stack_name="fsxn-new-relic-integration",
            lambda_function_name="fsxn-new-relic-integration-shipper",
            new_relic_region="US",
            new_relic_account_id_masked="****1234",
            aws_account_id_masked="****5678",
            fsx_file_system_id="fs-0123456789abcdef0",
        )

        nrql_results: list[NRQLQueryResult] = []
        alert_config = AlertConditionConfig(
            nrql_query="SELECT count(*) FROM Log WHERE source='fsxn-ontap' AND result='Failure'",
            threshold_value=1,
            evaluation_window_minutes=5,
            notification_channel="slack-alerts",
        )
        demo_timeline = DemoScenarioTimeline(
            file_write_timestamp="2026-01-20T11:00:00+09:00",
            scenario_status="pass",
        )

        rendered = render_new_relic_report(
            report, nrql_results, alert_config, demo_timeline, env
        )

        # Verify failure appears in rendered output
        assert "Bilingual Comparison" in rendered
        assert "❌ 失敗" in rendered
        assert "本番環境利用不可" in rendered


class TestScreenshotValidationToRendererPipeline:
    """Test screenshot validation → results renderer pipeline."""

    def test_valid_screenshots_produce_pass_steps_in_report(
        self, tmp_path: pytest.TempPathFactory
    ) -> None:
        """Valid screenshots produce success steps that render correctly."""
        screenshot_dir = str(tmp_path / "screenshots" / "new-relic")
        os.makedirs(screenshot_dir, exist_ok=True)

        # Create valid PNG files for all required screenshots
        required_files = [
            "logs-ui-arrival.png",
            "nrql-query-result.png",
            "alert-condition-config.png",
            "alert-policy-overview.png",
        ]
        for filename in required_files:
            _create_valid_png(os.path.join(screenshot_dir, filename))

        # Run screenshot validation
        validation_steps = validate_new_relic_screenshots(screenshot_dir)
        assert len(validation_steps) == 4
        assert all(step.result == "success" for step in validation_steps)

        # Feed validation steps into the renderer
        report = VerificationReport(
            verification_date="2026-01-20T12:00:00+09:00",
            environment=None,  # type: ignore[arg-type]
            verifier=VerifierInfo(name="テスト太郎", role="Engineer"),
            steps=validation_steps,
            issues=[],
        )

        env = NewRelicVerificationEnvironment(
            aws_region="ap-northeast-1",
            stack_name="fsxn-new-relic-integration",
            lambda_function_name="fsxn-new-relic-integration-shipper",
            new_relic_region="US",
            new_relic_account_id_masked="****1234",
            aws_account_id_masked="****5678",
            fsx_file_system_id="fs-0123456789abcdef0",
        )

        nrql_results: list[NRQLQueryResult] = []
        alert_config = AlertConditionConfig(
            nrql_query="SELECT count(*) FROM Log WHERE source='fsxn-ontap' AND result='Failure'",
            threshold_value=1,
            evaluation_window_minutes=5,
            notification_channel="slack-alerts",
        )
        demo_timeline = DemoScenarioTimeline(
            file_write_timestamp="2026-01-20T11:00:00+09:00",
            scenario_status="pass",
        )

        rendered = render_new_relic_report(
            report, nrql_results, alert_config, demo_timeline, env
        )

        # Verify screenshot validation steps appear in output
        assert "Screenshot validation: logs-ui-arrival.png" in rendered
        assert "Screenshot validation: nrql-query-result.png" in rendered
        assert "Screenshot validation: alert-condition-config.png" in rendered
        assert "Screenshot validation: alert-policy-overview.png" in rendered
        assert "✅ 成功" in rendered
        assert "本番環境利用可能" in rendered

    def test_missing_screenshots_produce_failure_steps_in_report(
        self, tmp_path: pytest.TempPathFactory
    ) -> None:
        """Missing screenshots produce failure steps that render correctly."""
        screenshot_dir = str(tmp_path / "screenshots" / "new-relic")
        os.makedirs(screenshot_dir, exist_ok=True)

        # Only create 2 of 4 required files
        _create_valid_png(os.path.join(screenshot_dir, "logs-ui-arrival.png"))
        _create_valid_png(os.path.join(screenshot_dir, "nrql-query-result.png"))

        # Run screenshot validation
        validation_steps = validate_new_relic_screenshots(screenshot_dir)
        assert len(validation_steps) == 4

        # Some should fail
        failed_steps = [s for s in validation_steps if s.result == "failure"]
        assert len(failed_steps) == 2

        # Feed validation steps into the renderer
        report = VerificationReport(
            verification_date="2026-01-20T12:00:00+09:00",
            environment=None,  # type: ignore[arg-type]
            verifier=VerifierInfo(name="テスト太郎", role="Engineer"),
            steps=validation_steps,
            issues=[],
        )

        env = NewRelicVerificationEnvironment(
            aws_region="ap-northeast-1",
            stack_name="fsxn-new-relic-integration",
            lambda_function_name="fsxn-new-relic-integration-shipper",
            new_relic_region="US",
            new_relic_account_id_masked="****1234",
            aws_account_id_masked="****5678",
            fsx_file_system_id="fs-0123456789abcdef0",
        )

        nrql_results: list[NRQLQueryResult] = []
        alert_config = AlertConditionConfig(
            nrql_query="SELECT count(*) FROM Log WHERE source='fsxn-ontap' AND result='Failure'",
            threshold_value=1,
            evaluation_window_minutes=5,
            notification_channel="slack-alerts",
        )
        demo_timeline = DemoScenarioTimeline(
            file_write_timestamp="2026-01-20T11:00:00+09:00",
            scenario_status="pass",
        )

        rendered = render_new_relic_report(
            report, nrql_results, alert_config, demo_timeline, env
        )

        # Verify failure details appear in output
        assert "❌ 失敗" in rendered
        assert "本番環境利用不可" in rendered
        assert "Screenshot validation: alert-condition-config.png" in rendered
        assert "Screenshot validation: alert-policy-overview.png" in rendered


class TestFullReportGenerationPipeline:
    """Test full report generation with all New Relic components wired together."""

    def test_full_report_with_all_components(
        self, tmp_path: pytest.TempPathFactory
    ) -> None:
        """Full report with NRQL results, alert config, demo timeline renders all sections."""
        # Build a complete VerificationReport
        steps = [
            VerificationStep(
                step_number=1,
                step_name="Deploy CloudFormation Stack",
                result="success",
                command="aws cloudformation deploy --template-file template.yaml --stack-name fsxn-new-relic-integration --capabilities CAPABILITY_IAM",
                output="Stack fsxn-new-relic-integration created successfully.",
                timestamp="2026-01-20T10:00:00+09:00",
            ),
            VerificationStep(
                step_number=2,
                step_name="Invoke Lambda with Test Event",
                result="success",
                command="aws lambda invoke --function-name fsxn-new-relic-integration-shipper --payload file://test-event.json response.json",
                output='{"statusCode": 200, "body": {"total_logs": 10, "total_shipped": 10, "errors": []}}',
                timestamp="2026-01-20T10:05:00+09:00",
            ),
            VerificationStep(
                step_number=3,
                step_name="Verify New Relic Log Arrival",
                result="success",
                command=None,
                output="Found 10 logs matching source='fsxn-ontap'.",
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

        report = VerificationReport(
            verification_date="2026-01-20T10:00:00+09:00",
            environment=None,  # type: ignore[arg-type]
            verifier=VerifierInfo(name="リポジトリ管理者", role="DevOps Engineer"),
            steps=steps,
            issues=[
                Issue(
                    title="軽微なレイテンシ増加",
                    description="ログ到着に通常より30秒長くかかった",
                    resolution="New Relic API のレート制限を確認する",
                ),
            ],
        )

        env = NewRelicVerificationEnvironment(
            aws_region="ap-northeast-1",
            stack_name="fsxn-new-relic-integration",
            lambda_function_name="fsxn-new-relic-integration-shipper",
            new_relic_region="US",
            new_relic_account_id_masked="****4567",
            aws_account_id_masked="****9012",
            fsx_file_system_id="fs-0123456789abcdef0",
        )

        nrql_results = [
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

        alert_config = AlertConditionConfig(
            nrql_query="SELECT count(*) FROM Log WHERE source='fsxn-ontap' AND result='Failure'",
            threshold_value=1,
            evaluation_window_minutes=5,
            notification_channel="slack-alerts",
            test_trigger_timestamp="2026-01-20T10:45:00+09:00",
            notification_receipt_timestamp="2026-01-20T10:47:30+09:00",
        )

        demo_timeline = DemoScenarioTimeline(
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

        rendered = render_new_relic_report(
            report, nrql_results, alert_config, demo_timeline, env
        )

        # Verify title
        assert "# New Relic 統合 動作確認結果" in rendered

        # Verify header/environment section
        assert "実施概要" in rendered
        assert "2026-01-20T10:00:00+09:00" in rendered
        assert "ap-northeast-1" in rendered
        assert "fsxn-new-relic-integration" in rendered
        assert "****4567" in rendered
        assert "****9012" in rendered
        assert "fs-0123456789abcdef0" in rendered
        assert "US" in rendered

        # Verify verifier info
        assert "リポジトリ管理者" in rendered
        assert "DevOps Engineer" in rendered

        # Verify steps section
        assert "検証ステップ" in rendered
        assert "Deploy CloudFormation Stack" in rendered
        assert "Invoke Lambda with Test Event" in rendered
        assert "Verify New Relic Log Arrival" in rendered
        assert "Execute NRQL Queries" in rendered
        assert "Configure Alert Condition" in rendered

        # Verify commands in code blocks
        assert "aws cloudformation deploy" in rendered
        assert "aws lambda invoke" in rendered

        # Verify screenshot image links
        assert "![Verify New Relic Log Arrival](docs/screenshots/new-relic/logs-ui-arrival.png)" in rendered
        assert "![Execute NRQL Queries](docs/screenshots/new-relic/nrql-query-result.png)" in rendered

        # Verify NRQL results section
        assert "NRQL クエリ結果" in rendered
        assert "SELECT count(*) FROM Log WHERE source='fsxn-ontap' SINCE 1 hour ago" in rendered
        assert "SELECT count(*) FROM Log WHERE source='fsxn-ontap' FACET operation" in rendered
        assert "count: 42" in rendered
        assert "✅ PASS" in rendered

        # Verify alert config section
        assert "アラート設定詳細" in rendered
        assert "SELECT count(*) FROM Log WHERE source='fsxn-ontap' AND result='Failure'" in rendered
        assert "5 分" in rendered
        assert "slack-alerts" in rendered
        assert "2026-01-20T10:45:00+09:00" in rendered
        assert "2026-01-20T10:47:30+09:00" in rendered

        # Verify demo timeline section
        assert "デモシナリオタイムライン" in rendered
        assert "2026-01-20T11:00:00+09:00" in rendered
        assert "2026-01-20T11:00:05+09:00" in rendered
        assert "2026-01-20T11:00:30+09:00" in rendered
        assert "2026-01-20T11:01:00+09:00" in rendered
        assert "2026-01-20T11:01:45+09:00" in rendered

        # Verify issues section
        assert "既知の問題と対応策" in rendered
        assert "軽微なレイテンシ増加" in rendered
        assert "ログ到着に通常より30秒長くかかった" in rendered
        assert "New Relic API のレート制限を確認する" in rendered

        # Verify conclusion (all pass)
        assert "結論" in rendered
        assert "本番環境利用可能" in rendered

    def test_full_report_with_failures_shows_not_production_ready(
        self, tmp_path: pytest.TempPathFactory
    ) -> None:
        """Full report with failed steps shows not-production-ready conclusion."""
        steps = [
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
        ]

        report = VerificationReport(
            verification_date="2026-01-20T10:00:00+09:00",
            environment=None,  # type: ignore[arg-type]
            verifier=VerifierInfo(name="テスト太郎", role="Engineer"),
            steps=steps,
            issues=[],
        )

        env = NewRelicVerificationEnvironment(
            aws_region="ap-northeast-1",
            stack_name="fsxn-new-relic-integration",
            lambda_function_name="fsxn-new-relic-integration-shipper",
            new_relic_region="US",
            new_relic_account_id_masked="****1234",
            aws_account_id_masked="****5678",
            fsx_file_system_id="fs-0123456789abcdef0",
        )

        nrql_results = [
            NRQLQueryResult(
                query="SELECT count(*) FROM Log WHERE source='fsxn-ontap' SINCE 1 hour ago",
                result_summary="count: 0",
                row_count=0,
                first_rows=[],
                execution_timestamp="2026-01-20T10:30:00+09:00",
                status="fail",
                error_reason="Query returned 0 results",
                retry_count=3,
            ),
        ]

        alert_config = AlertConditionConfig(
            nrql_query="SELECT count(*) FROM Log WHERE source='fsxn-ontap' AND result='Failure'",
            threshold_value=1,
            evaluation_window_minutes=5,
            notification_channel="slack-alerts",
        )

        demo_timeline = DemoScenarioTimeline(
            file_write_timestamp="2026-01-20T11:00:00+09:00",
            scenario_status="fail",
            last_successful_stage="file_write",
            failing_stage="ems_event",
            elapsed_at_failure=120.5,
        )

        rendered = render_new_relic_report(
            report, nrql_results, alert_config, demo_timeline, env
        )

        # Verify not-production-ready conclusion
        assert "本番環境利用不可" in rendered
        assert "Verify New Relic Log Arrival" in rendered
        assert "Execute NRQL Queries" in rendered

        # Verify error details are present
        assert "Log arrival timeout exceeded 120 seconds" in rendered
        assert "NRQL query returned empty result set" in rendered

        # Verify NRQL failure status
        assert "❌ FAIL" in rendered
        assert "Query returned 0 results" in rendered

        # Verify demo timeline failure info
        assert "失敗ステージ" in rendered
        assert "ems_event" in rendered
        assert "120.5" in rendered

    def test_full_report_with_no_issues_shows_no_issues_text(
        self, tmp_path: pytest.TempPathFactory
    ) -> None:
        """Full report with empty issues list shows '問題なし' text."""
        steps = [
            VerificationStep(
                step_number=1,
                step_name="Deploy Stack",
                result="success",
                timestamp="2026-01-20T10:00:00+09:00",
            ),
        ]

        report = VerificationReport(
            verification_date="2026-01-20T10:00:00+09:00",
            environment=None,  # type: ignore[arg-type]
            verifier=VerifierInfo(name="テスト太郎", role="Engineer"),
            steps=steps,
            issues=[],
        )

        env = NewRelicVerificationEnvironment(
            aws_region="ap-northeast-1",
            stack_name="fsxn-new-relic-integration",
            lambda_function_name="fsxn-new-relic-integration-shipper",
            new_relic_region="US",
            new_relic_account_id_masked="****1234",
            aws_account_id_masked="****5678",
            fsx_file_system_id="fs-0123456789abcdef0",
        )

        nrql_results: list[NRQLQueryResult] = []
        alert_config = AlertConditionConfig(
            nrql_query="SELECT count(*) FROM Log",
            threshold_value=1,
            evaluation_window_minutes=5,
            notification_channel="email",
        )
        demo_timeline = DemoScenarioTimeline(
            file_write_timestamp="2026-01-20T11:00:00+09:00",
            scenario_status="pass",
        )

        rendered = render_new_relic_report(
            report, nrql_results, alert_config, demo_timeline, env
        )

        assert "問題なし" in rendered
