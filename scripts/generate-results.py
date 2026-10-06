#!/usr/bin/env python3
"""Generate E2E verification results documents for supported vendors.

Collects results from the orchestrator output JSON, bilingual comparison,
and screenshot validation, then renders the final Markdown results document.

Supports multiple vendors via the --vendor flag:
- datadog (default): Uses Datadog-specific renderer and defaults
- new-relic: Uses New Relic-specific renderer with NRQL results,
  alert config, and demo timeline sections

Usage:
    # Datadog (default)
    python scripts/generate-results.py \
        --verifier-name "repository maintainer" \
        --output docs/ja/verification-results-datadog.md

    # New Relic
    python scripts/generate-results.py \
        --vendor new-relic \
        --verifier-name "repository maintainer" \
        --output docs/ja/verification-results-new-relic.md
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Ensure project root is on sys.path for package imports
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from scripts.verification.bilingual_comparator import compare_setup_guides
from scripts.verification.screenshot_validator import (
    validate_screenshots,
    validate_new_relic_screenshots,
)
from scripts.verification.results_renderer import render_report, render_bilingual_summary
from scripts.verification.new_relic_results_renderer import render_new_relic_report
from scripts.verification.models import (
    AlertConditionConfig,
    DemoScenarioTimeline,
    Issue,
    NewRelicVerificationEnvironment,
    NRQLQueryResult,
    VerificationReport,
    VerificationStep,
    VerificationEnvironment,
    VerifierInfo,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Optional argument list for testing. Defaults to sys.argv[1:].

    Returns:
        Parsed arguments namespace.
    """
    parser = argparse.ArgumentParser(
        description="Generate E2E verification results document.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--vendor",
        choices=["datadog", "new-relic"],
        default="datadog",
        help="Vendor to generate results for (default: datadog)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help=(
            "Output path for the results document "
            "(default: docs/ja/verification-results-datadog.md for datadog, "
            "docs/ja/verification-results-new-relic.md for new-relic)"
        ),
    )
    parser.add_argument(
        "--orchestrator-results",
        default=None,
        help="Path to JSON output from the verification orchestrator (optional)",
    )
    parser.add_argument(
        "--ja",
        default=None,
        help=(
            "Path to the Japanese setup guide "
            "(default: integrations/datadog/docs/ja/setup-guide.md for datadog, "
            "integrations/new-relic/docs/ja/setup-guide.md for new-relic)"
        ),
    )
    parser.add_argument(
        "--en",
        default=None,
        help=(
            "Path to the English setup guide "
            "(default: integrations/datadog/docs/en/setup-guide.md for datadog, "
            "integrations/new-relic/docs/en/setup-guide.md for new-relic)"
        ),
    )
    parser.add_argument(
        "--screenshots-dir",
        default=None,
        help=(
            "Path to the screenshots directory "
            "(default: docs/screenshots for datadog, "
            "docs/screenshots/new-relic for new-relic)"
        ),
    )
    parser.add_argument(
        "--verifier-name",
        required=True,
        help="Name of the person performing verification (required)",
    )
    parser.add_argument(
        "--verifier-role",
        default="DevOps Engineer",
        help="Role of the verifier (default: DevOps Engineer)",
    )
    parser.add_argument(
        "--region",
        default="ap-northeast-1",
        help="AWS region (default: ap-northeast-1)",
    )
    parser.add_argument(
        "--stack-name",
        default=None,
        help=(
            "CloudFormation stack name "
            "(default: fsxn-datadog-integration for datadog, "
            "fsxn-new-relic-integration for new-relic)"
        ),
    )

    args = parser.parse_args(argv)

    # Apply vendor-specific defaults
    if args.vendor == "new-relic":
        if args.output is None:
            args.output = "docs/ja/verification-results-new-relic.md"
        if args.ja is None:
            args.ja = "integrations/new-relic/docs/ja/setup-guide.md"
        if args.en is None:
            args.en = "integrations/new-relic/docs/en/setup-guide.md"
        if args.screenshots_dir is None:
            args.screenshots_dir = "docs/screenshots/new-relic"
        if args.stack_name is None:
            args.stack_name = "fsxn-new-relic-integration"
    else:
        # Datadog defaults
        if args.output is None:
            args.output = "docs/ja/verification-results-datadog.md"
        if args.ja is None:
            args.ja = "integrations/datadog/docs/ja/setup-guide.md"
        if args.en is None:
            args.en = "integrations/datadog/docs/en/setup-guide.md"
        if args.screenshots_dir is None:
            args.screenshots_dir = "docs/screenshots"
        if args.stack_name is None:
            args.stack_name = "fsxn-datadog-integration"

    return args


def load_orchestrator_results(path: str) -> list[VerificationStep]:
    """Load verification steps from orchestrator JSON output.

    The JSON file is expected to contain a list of step objects with
    fields matching the VerificationStep dataclass.

    Args:
        path: Path to the orchestrator results JSON file.

    Returns:
        A list of VerificationStep instances parsed from the JSON.
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    steps: list[VerificationStep] = []
    for item in data.get("steps", []):
        steps.append(
            VerificationStep(
                step_number=item.get("step_number", 0),
                step_name=item.get("step_name", ""),
                result=item.get("result", "skipped"),
                command=item.get("command"),
                output=item.get("output"),
                screenshot_path=item.get("screenshot_path"),
                error_detail=item.get("error_detail"),
                timestamp=item.get("timestamp", ""),
            )
        )

    return steps


def build_report(args: argparse.Namespace) -> VerificationReport:
    """Build a VerificationReport from all collected data sources.

    Collects orchestrator results (if available), runs bilingual
    comparison, runs screenshot validation, and assembles the
    complete report.

    Args:
        args: Parsed command-line arguments.

    Returns:
        A fully populated VerificationReport instance.
    """
    # Determine verification timestamp (JST = UTC+9)
    jst = timezone(timedelta(hours=9))
    verification_date = datetime.now(jst).strftime("%Y-%m-%dT%H:%M:%S+09:00")

    # Build environment info
    environment = VerificationEnvironment(
        aws_region=args.region,
        stack_name=args.stack_name,
        lambda_function_name=f"{args.stack_name}-shipper",
        datadog_site="datadoghq.com",
    )

    # Build verifier info
    verifier = VerifierInfo(
        name=args.verifier_name,
        role=args.verifier_role,
    )

    # Collect orchestrator steps (if JSON provided)
    steps: list[VerificationStep] = []
    if args.orchestrator_results and os.path.exists(args.orchestrator_results):
        steps = load_orchestrator_results(args.orchestrator_results)

    # Run bilingual comparison
    bilingual_result = compare_setup_guides(args.ja, args.en)

    # Run screenshot validation
    screenshot_steps = validate_screenshots(args.screenshots_dir)

    # Append screenshot validation steps (renumber after orchestrator steps)
    base_step_number = max((s.step_number for s in steps), default=0)
    for i, ss in enumerate(screenshot_steps, start=1):
        ss.step_number = base_step_number + i
        steps.append(ss)

    # Collect issues from failures
    issues: list[Issue] = []
    for step in steps:
        if step.result == "failure" and step.error_detail:
            issues.append(
                Issue(
                    title=f"ステップ {step.step_number}: {step.step_name}",
                    description=step.error_detail,
                )
            )

    if bilingual_result.status == "fail":
        for diff in bilingual_result.differences:
            issues.append(
                Issue(
                    title=f"バイリンガル差異: {diff.diff_type} ({diff.section})",
                    description=(
                        f"ファイル: {diff.file_path_ja} / {diff.file_path_en}\n"
                        f"期待値: {diff.expected}\n"
                        f"実際値: {diff.actual}"
                    ),
                )
            )

    return VerificationReport(
        verification_date=verification_date,
        environment=environment,
        verifier=verifier,
        steps=steps,
        bilingual_comparison=bilingual_result,
        issues=issues,
    )


def load_new_relic_orchestrator_data(
    path: str,
) -> tuple[
    list[VerificationStep],
    list[NRQLQueryResult],
    AlertConditionConfig,
    DemoScenarioTimeline,
    NewRelicVerificationEnvironment | None,
]:
    """Load New Relic-specific data from orchestrator JSON output.

    The JSON file is expected to contain steps, NRQL results, alert
    configuration, demo timeline, and environment details.

    Args:
        path: Path to the orchestrator results JSON file.

    Returns:
        A tuple of (steps, nrql_results, alert_config, demo_timeline, env).
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    # Parse steps
    steps: list[VerificationStep] = []
    for item in data.get("steps", []):
        steps.append(
            VerificationStep(
                step_number=item.get("step_number", 0),
                step_name=item.get("step_name", ""),
                result=item.get("result", "skipped"),
                command=item.get("command"),
                output=item.get("output"),
                screenshot_path=item.get("screenshot_path"),
                error_detail=item.get("error_detail"),
                timestamp=item.get("timestamp", ""),
            )
        )

    # Parse NRQL results
    nrql_results: list[NRQLQueryResult] = []
    for item in data.get("nrql_results", []):
        nrql_results.append(
            NRQLQueryResult(
                query=item.get("query", ""),
                result_summary=item.get("result_summary", ""),
                row_count=item.get("row_count", 0),
                first_rows=item.get("first_rows", []),
                execution_timestamp=item.get("execution_timestamp", ""),
                status=item.get("status", "fail"),
                error_reason=item.get("error_reason"),
                retry_count=item.get("retry_count", 0),
            )
        )

    # Parse alert config
    alert_data = data.get("alert_config", {})
    alert_config = AlertConditionConfig(
        nrql_query=alert_data.get("nrql_query", ""),
        threshold_value=alert_data.get("threshold_value", 0),
        evaluation_window_minutes=alert_data.get("evaluation_window_minutes", 5),
        notification_channel=alert_data.get("notification_channel", ""),
        test_trigger_timestamp=alert_data.get("test_trigger_timestamp"),
        notification_receipt_timestamp=alert_data.get(
            "notification_receipt_timestamp"
        ),
    )

    # Parse demo timeline
    timeline_data = data.get("demo_timeline", {})
    demo_timeline = DemoScenarioTimeline(
        file_write_timestamp=timeline_data.get("file_write_timestamp", ""),
        ems_event_timestamp=timeline_data.get("ems_event_timestamp"),
        s3_object_creation_timestamp=timeline_data.get(
            "s3_object_creation_timestamp"
        ),
        lambda_invocation_timestamp=timeline_data.get(
            "lambda_invocation_timestamp"
        ),
        new_relic_log_arrival_timestamp=timeline_data.get(
            "new_relic_log_arrival_timestamp"
        ),
        scenario_status=timeline_data.get("scenario_status", "fail"),
        last_successful_stage=timeline_data.get("last_successful_stage"),
        failing_stage=timeline_data.get("failing_stage"),
        elapsed_at_failure=timeline_data.get("elapsed_at_failure"),
    )

    # Parse environment (if present)
    env_data = data.get("environment")
    env: NewRelicVerificationEnvironment | None = None
    if env_data:
        env = NewRelicVerificationEnvironment(
            aws_region=env_data.get("aws_region", ""),
            stack_name=env_data.get("stack_name", ""),
            lambda_function_name=env_data.get("lambda_function_name", ""),
            new_relic_region=env_data.get("new_relic_region", "US"),
            new_relic_account_id_masked=env_data.get(
                "new_relic_account_id_masked", ""
            ),
            aws_account_id_masked=env_data.get("aws_account_id_masked", ""),
            fsx_file_system_id=env_data.get("fsx_file_system_id", ""),
        )

    return steps, nrql_results, alert_config, demo_timeline, env


def build_new_relic_report(
    args: argparse.Namespace,
) -> tuple[VerificationReport, str]:
    """Build a New Relic verification report and render it to Markdown.

    Collects orchestrator results (if available), runs bilingual
    comparison, runs New Relic screenshot validation, and renders
    the report using the New Relic-specific renderer.

    Args:
        args: Parsed command-line arguments.

    Returns:
        A tuple of (VerificationReport, rendered_markdown_string).
    """
    # Determine verification timestamp (JST = UTC+9)
    jst = timezone(timedelta(hours=9))
    verification_date = datetime.now(jst).strftime("%Y-%m-%dT%H:%M:%S+09:00")

    # Build verifier info
    verifier = VerifierInfo(
        name=args.verifier_name,
        role=args.verifier_role,
    )

    # Initialize New Relic-specific data
    steps: list[VerificationStep] = []
    nrql_results: list[NRQLQueryResult] = []
    alert_config = AlertConditionConfig(
        nrql_query="SELECT count(*) FROM Log WHERE source='fsxn-ontap' AND result='Failure'",
        threshold_value=1,
        evaluation_window_minutes=5,
        notification_channel="",
    )
    demo_timeline = DemoScenarioTimeline(
        file_write_timestamp="",
        scenario_status="fail",
    )
    nr_env: NewRelicVerificationEnvironment | None = None

    # Load orchestrator results if available
    if args.orchestrator_results and os.path.exists(args.orchestrator_results):
        (
            steps,
            nrql_results,
            alert_config,
            demo_timeline,
            nr_env,
        ) = load_new_relic_orchestrator_data(args.orchestrator_results)

    # Build environment from args if not loaded from orchestrator
    if nr_env is None:
        nr_env = NewRelicVerificationEnvironment(
            aws_region=args.region,
            stack_name=args.stack_name,
            lambda_function_name=f"{args.stack_name}-shipper",
            new_relic_region="US",
            new_relic_account_id_masked="****0000",
            aws_account_id_masked="****0000",
            fsx_file_system_id="fs-unknown",
        )

    # Run bilingual comparison
    bilingual_result = compare_setup_guides(args.ja, args.en)

    # Run New Relic screenshot validation
    screenshot_steps = validate_new_relic_screenshots(args.screenshots_dir)

    # Append screenshot validation steps (renumber after orchestrator steps)
    base_step_number = max((s.step_number for s in steps), default=0)
    for i, ss in enumerate(screenshot_steps, start=1):
        ss.step_number = base_step_number + i
        steps.append(ss)

    # Collect issues from failures
    issues: list[Issue] = []
    for step in steps:
        if step.result == "failure" and step.error_detail:
            issues.append(
                Issue(
                    title=f"ステップ {step.step_number}: {step.step_name}",
                    description=step.error_detail,
                )
            )

    if bilingual_result.status == "fail":
        for diff in bilingual_result.differences:
            issues.append(
                Issue(
                    title=f"バイリンガル差異: {diff.diff_type} ({diff.section})",
                    description=(
                        f"ファイル: {diff.file_path_ja} / {diff.file_path_en}\n"
                        f"期待値: {diff.expected}\n"
                        f"実際値: {diff.actual}"
                    ),
                )
            )

    # Build a VerificationEnvironment for the base report (required field)
    environment = VerificationEnvironment(
        aws_region=args.region,
        stack_name=args.stack_name,
        lambda_function_name=f"{args.stack_name}-shipper",
        datadog_site="",
    )

    report = VerificationReport(
        verification_date=verification_date,
        environment=environment,
        verifier=verifier,
        steps=steps,
        bilingual_comparison=bilingual_result,
        issues=issues,
    )

    # Render using New Relic-specific renderer
    report_md = render_new_relic_report(
        report=report,
        nrql_results=nrql_results,
        alert_config=alert_config,
        demo_timeline=demo_timeline,
        env=nr_env,
    )

    # Append bilingual summary
    bilingual_md = render_bilingual_summary(report.bilingual_comparison)
    final_document = report_md + "\n" + bilingual_md

    return report, final_document


def main(argv: list[str] | None = None) -> int:
    """Main entry point for the results generator.

    Args:
        argv: Optional argument list for testing. Defaults to sys.argv[1:].

    Returns:
        Exit code: 0 for success, 1 for failure.
    """
    args = parse_args(argv)

    if args.vendor == "new-relic":
        # New Relic vendor path
        report, final_document = build_new_relic_report(args)
    else:
        # Datadog vendor path (default)
        report = build_report(args)

        # Render the report
        report_md = render_report(report)
        bilingual_md = render_bilingual_summary(report.bilingual_comparison)

        # Combine into final document
        final_document = report_md + "\n" + bilingual_md

    # Ensure output directory exists
    output_dir = os.path.dirname(args.output)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    # Write to output file
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(final_document)

    # Print summary to stdout
    total_steps = len(report.steps)
    passed_steps = sum(1 for s in report.steps if s.result == "success")
    failed_steps = sum(1 for s in report.steps if s.result == "failure")
    skipped_steps = sum(1 for s in report.steps if s.result == "skipped")

    vendor_label = "New Relic" if args.vendor == "new-relic" else "Datadog"

    print("=" * 60)
    print(f"{vendor_label} E2E 検証結果サマリ")
    print("=" * 60)
    print(f"  検証日時: {report.verification_date}")
    print(f"  検証者: {report.verifier.name} ({report.verifier.role})")
    print(f"  リージョン: {report.environment.aws_region}")
    print(f"  スタック名: {report.environment.stack_name}")
    print("-" * 60)
    print(f"  ステップ合計: {total_steps}")
    print(f"    ✅ 成功: {passed_steps}")
    print(f"    ❌ 失敗: {failed_steps}")
    print(f"    ⏭️  スキップ: {skipped_steps}")
    print("-" * 60)
    print(f"  バイリンガル対応: {report.bilingual_comparison.status}")
    print(f"  検出された問題: {len(report.issues)} 件")
    print("-" * 60)
    print(f"  出力ファイル: {args.output}")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
