"""Verification report renderer for New Relic E2E verification.

Renders VerificationReport with New Relic-specific data models
(NRQLQueryResult, AlertConditionConfig, DemoScenarioTimeline,
NewRelicVerificationEnvironment) into structured Markdown documents.
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
)

_MAX_OUTPUT_LINES = 200

_RESULT_BADGES: dict[str, str] = {
    "success": "✅ 成功",
    "failure": "❌ 失敗",
    "skipped": "⏭️ スキップ",
}

_NRQL_STATUS_BADGES: dict[str, str] = {
    "pass": "✅ PASS",
    "fail": "❌ FAIL",
}


def mask_account_id(account_id: str) -> str:
    """Mask an account ID to show only the last 4 digits.

    Args:
        account_id: The full account ID string (numeric).

    Returns:
        Masked string showing only last 4 digits preceded by asterisks.
        For example, "123456789012" becomes "****9012".
    """
    if len(account_id) <= 4:
        return f"****{account_id}"
    return f"****{account_id[-4:]}"


def render_new_relic_report(
    report: VerificationReport,
    nrql_results: list[NRQLQueryResult],
    alert_config: AlertConditionConfig,
    demo_timeline: DemoScenarioTimeline,
    env: NewRelicVerificationEnvironment,
) -> str:
    """Render a complete New Relic verification report as Markdown.

    Args:
        report: The verification report data containing steps, verifier,
            and issues.
        nrql_results: List of NRQL query execution results.
        alert_config: Alert condition configuration details.
        demo_timeline: Demo scenario execution timeline.
        env: New Relic verification environment details.

    Returns:
        A Markdown-formatted string containing the full report.
    """
    sections: list[str] = []

    sections.append(_render_title())
    sections.append(_render_header(report, env))
    sections.append(_render_steps(report.steps))
    sections.append(_render_nrql_results(nrql_results))
    sections.append(_render_alert_config(alert_config))
    sections.append(_render_demo_timeline(demo_timeline))
    sections.append(_render_issues(report.issues))
    sections.append(_render_conclusion(report.steps))

    return "\n".join(sections)


def _render_title() -> str:
    """Render the report title."""
    return "# New Relic 統合 動作確認結果\n"


def _render_header(
    report: VerificationReport,
    env: NewRelicVerificationEnvironment,
) -> str:
    """Render the header section with date, environment, and verifier."""
    lines: list[str] = []

    lines.append("## 実施概要")
    lines.append("")
    lines.append("| 項目 | 値 |")
    lines.append("|------|-----|")
    lines.append(f"| 検証日時 | {report.verification_date} |")
    lines.append("")
    lines.append("### 環境情報")
    lines.append("")
    lines.append("| 項目 | 値 |")
    lines.append("|------|-----|")
    lines.append(f"| AWS リージョン | {env.aws_region} |")
    lines.append(f"| スタック名 | {env.stack_name} |")
    lines.append(f"| Lambda 関数名 | {env.lambda_function_name} |")
    lines.append(f"| New Relic リージョン | {env.new_relic_region} |")
    lines.append(f"| New Relic アカウント ID | `{env.new_relic_account_id_masked}` |")
    lines.append(f"| AWS アカウント ID | `{env.aws_account_id_masked}` |")
    lines.append(f"| FSx ファイルシステム ID | {env.fsx_file_system_id} |")
    lines.append("")
    lines.append("### 検証者")
    lines.append("")
    lines.append("| 項目 | 値 |")
    lines.append("|------|-----|")
    lines.append(f"| 氏名 | {report.verifier.name} |")
    lines.append(f"| ロール | {report.verifier.role} |")
    lines.append("")

    return "\n".join(lines)


def _render_steps(steps: list[VerificationStep]) -> str:
    """Render the verification steps section."""
    lines: list[str] = []

    lines.append("## 検証ステップ")
    lines.append("")

    for step in steps:
        lines.append(_render_single_step(step))

    return "\n".join(lines)


def _render_single_step(step: VerificationStep) -> str:
    """Render a single verification step."""
    lines: list[str] = []

    badge = _RESULT_BADGES.get(step.result, step.result)
    lines.append(f"### ステップ {step.step_number}: {step.step_name}")
    lines.append("")
    lines.append(f"結果: {badge}")
    lines.append("")

    if step.command:
        lines.append("**実行コマンド**:")
        lines.append("")
        lines.append("```bash")
        lines.append(step.command)
        lines.append("```")
        lines.append("")

    if step.output:
        truncated_output = _truncate_output(step.output)
        lines.append("**出力**:")
        lines.append("")
        lines.append("```")
        lines.append(truncated_output)
        lines.append("```")
        lines.append("")

    if step.screenshot_path:
        lines.append(f"![{step.step_name}]({step.screenshot_path})")
        lines.append("")

    if step.error_detail:
        lines.append(f"**エラー詳細**: {step.error_detail}")
        lines.append("")

    return "\n".join(lines)


def _render_nrql_results(nrql_results: list[NRQLQueryResult]) -> str:
    """Render the NRQL query results section."""
    lines: list[str] = []

    lines.append("## NRQL クエリ結果")
    lines.append("")

    if not nrql_results:
        lines.append("NRQL クエリ結果なし")
        lines.append("")
        return "\n".join(lines)

    for i, result in enumerate(nrql_results, start=1):
        status_badge = _NRQL_STATUS_BADGES.get(result.status, result.status)
        lines.append(f"### クエリ {i}")
        lines.append("")
        lines.append("**NRQL**:")
        lines.append("")
        lines.append("```sql")
        lines.append(result.query)
        lines.append("```")
        lines.append("")
        lines.append("| 項目 | 値 |")
        lines.append("|------|-----|")
        lines.append(f"| 結果サマリー | {result.result_summary} |")
        lines.append(f"| 行数 | {result.row_count} |")
        lines.append(f"| 実行日時 | {result.execution_timestamp} |")
        lines.append(f"| ステータス | {status_badge} |")
        if result.error_reason:
            lines.append(f"| エラー理由 | {result.error_reason} |")
        if result.retry_count > 0:
            lines.append(f"| リトライ回数 | {result.retry_count} |")
        lines.append("")

    return "\n".join(lines)


def _render_alert_config(alert_config: AlertConditionConfig) -> str:
    """Render the alert condition configuration section."""
    lines: list[str] = []

    lines.append("## アラート設定詳細")
    lines.append("")
    lines.append("**NRQL クエリ**:")
    lines.append("")
    lines.append("```sql")
    lines.append(alert_config.nrql_query)
    lines.append("```")
    lines.append("")
    lines.append("| 項目 | 値 |")
    lines.append("|------|-----|")
    lines.append(f"| 閾値 | {alert_config.threshold_value} |")
    lines.append(f"| 評価ウィンドウ | {alert_config.evaluation_window_minutes} 分 |")
    lines.append(f"| 通知チャネル | {alert_config.notification_channel} |")
    if alert_config.test_trigger_timestamp:
        lines.append(
            f"| テストトリガー日時 | {alert_config.test_trigger_timestamp} |"
        )
    if alert_config.notification_receipt_timestamp:
        lines.append(
            f"| 通知受信日時 | {alert_config.notification_receipt_timestamp} |"
        )
    lines.append("")

    return "\n".join(lines)


def _render_demo_timeline(demo_timeline: DemoScenarioTimeline) -> str:
    """Render the demo scenario timeline section."""
    lines: list[str] = []

    lines.append("## デモシナリオタイムライン")
    lines.append("")
    lines.append("| 項目 | 値 |")
    lines.append("|------|-----|")
    lines.append(
        f"| ファイル書き込み | {demo_timeline.file_write_timestamp} |"
    )
    if demo_timeline.ems_event_timestamp:
        lines.append(
            f"| EMS イベント | {demo_timeline.ems_event_timestamp} |"
        )
    if demo_timeline.s3_object_creation_timestamp:
        lines.append(
            f"| S3 オブジェクト作成 | {demo_timeline.s3_object_creation_timestamp} |"
        )
    if demo_timeline.lambda_invocation_timestamp:
        lines.append(
            f"| Lambda 起動 | {demo_timeline.lambda_invocation_timestamp} |"
        )
    if demo_timeline.new_relic_log_arrival_timestamp:
        lines.append(
            f"| New Relic ログ到着 | {demo_timeline.new_relic_log_arrival_timestamp} |"
        )
    lines.append(
        f"| シナリオステータス | "
        f"{_NRQL_STATUS_BADGES.get(demo_timeline.scenario_status, demo_timeline.scenario_status)} |"
    )
    if demo_timeline.last_successful_stage:
        lines.append(
            f"| 最終成功ステージ | {demo_timeline.last_successful_stage} |"
        )
    if demo_timeline.failing_stage:
        lines.append(f"| 失敗ステージ | {demo_timeline.failing_stage} |")
    if demo_timeline.elapsed_at_failure is not None:
        lines.append(
            f"| 失敗時経過時間 | {demo_timeline.elapsed_at_failure:.1f} 秒 |"
        )
    lines.append("")

    return "\n".join(lines)


def _render_issues(issues: list[Issue]) -> str:
    """Render the issues section."""
    lines: list[str] = []

    lines.append("## 既知の問題と対応策")
    lines.append("")

    if not issues:
        lines.append("問題なし")
        lines.append("")
        return "\n".join(lines)

    for issue in issues:
        lines.append(f"### {issue.title}")
        lines.append("")
        lines.append(issue.description)
        lines.append("")
        if issue.resolution:
            lines.append(f"**対処方法**: {issue.resolution}")
            lines.append("")

    return "\n".join(lines)


def _render_conclusion(steps: list[VerificationStep]) -> str:
    """Render the conclusion section based on step results.

    Args:
        steps: List of verification steps to evaluate.

    Returns:
        Markdown conclusion section. All steps passing yields
        "本番環境利用可能"; any failure yields "本番環境利用不可"
        with a list of failed step numbers.
    """
    lines: list[str] = []

    lines.append("## 結論")
    lines.append("")

    failed_steps = [s for s in steps if s.result == "failure"]

    if not failed_steps:
        lines.append("本番環境利用可能")
        lines.append("")
    else:
        lines.append("本番環境利用不可")
        lines.append("")
        lines.append("**失敗した基準**:")
        lines.append("")
        for step in failed_steps:
            lines.append(f"- ステップ {step.step_number}: {step.step_name}")
        lines.append("")

    return "\n".join(lines)


def _truncate_output(output: str) -> str:
    """Truncate output to a maximum number of lines.

    Args:
        output: The raw output string.

    Returns:
        The output truncated to _MAX_OUTPUT_LINES lines, with a
        truncation notice appended if lines were removed.
    """
    output_lines = output.splitlines()
    if len(output_lines) <= _MAX_OUTPUT_LINES:
        return output

    truncated = output_lines[:_MAX_OUTPUT_LINES]
    remaining = len(output_lines) - _MAX_OUTPUT_LINES
    truncated.append(f"... ({remaining} 行省略)")
    return "\n".join(truncated)
