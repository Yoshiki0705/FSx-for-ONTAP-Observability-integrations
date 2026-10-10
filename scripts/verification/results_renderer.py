"""Verification report renderer for Datadog E2E verification.

Renders VerificationReport and BilingualComparisonResult data models
into structured Markdown documents.
"""

from __future__ import annotations

from scripts.verification.models import (
    BilingualComparisonResult,
    Issue,
    VerificationReport,
    VerificationStep,
)

_MAX_OUTPUT_LINES = 200

_RESULT_BADGES: dict[str, str] = {
    "success": "✅ 成功",
    "failure": "❌ 失敗",
    "skipped": "⏭️ スキップ",
}


def render_report(report: VerificationReport) -> str:
    """Render a complete verification report as Markdown.

    Args:
        report: The verification report data to render.

    Returns:
        A Markdown-formatted string containing the full report.
    """
    sections: list[str] = []

    sections.append(_render_title())
    sections.append(_render_header(report))
    sections.append(_render_steps(report.steps))
    sections.append(_render_issues(report.issues))

    return "\n".join(sections)


def render_bilingual_summary(result: BilingualComparisonResult) -> str:
    """Render a bilingual comparison result summary as Markdown.

    Args:
        result: The bilingual comparison result to render.

    Returns:
        A Markdown-formatted string containing the summary.
    """
    lines: list[str] = []

    lines.append("## バイリンガル対応確認結果")
    lines.append("")
    lines.append("| 項目 | 値 |")
    lines.append("|------|-----|")
    lines.append(f"| ステータス | {result.status} |")
    lines.append(f"| 見出し数 | {result.heading_count} |")
    lines.append(f"| コードブロック数 | {result.code_block_count} |")
    lines.append(f"| テーブル数 | {result.table_count} |")
    lines.append(f"| 差異件数 | {len(result.differences)} |")
    lines.append("")

    if result.files_compared:
        lines.append("### 比較対象ファイル")
        lines.append("")
        for ja_path, en_path in result.files_compared:
            lines.append(f"- `{ja_path}` / `{en_path}`")
        lines.append("")

    if result.differences:
        lines.append("### 検出された差異")
        lines.append("")
        for diff in result.differences:
            lines.append(f"- **{diff.diff_type}** ({diff.section})")
            lines.append(f"  - ファイル: `{diff.file_path_ja}` / `{diff.file_path_en}`")
            lines.append(f"  - 期待値: {diff.expected}")
            lines.append(f"  - 実際値: {diff.actual}")
        lines.append("")

    return "\n".join(lines)


def _render_title() -> str:
    """Render the report title."""
    return "# Datadog 統合 動作確認結果\n"


def _render_header(report: VerificationReport) -> str:
    """Render the header section with date, environment, and verifier.

    Label/value pairs are emitted as Markdown tables to match the
    hand-authored done-set verification-results documents.
    """
    lines: list[str] = []

    lines.append("| 項目 | 値 |")
    lines.append("|------|-----|")
    lines.append(f"| 検証日時 | {report.verification_date} |")
    lines.append("")
    lines.append("### 検証環境")
    lines.append("")
    lines.append("| 項目 | 値 |")
    lines.append("|------|-----|")
    lines.append(f"| AWS リージョン | {report.environment.aws_region} |")
    lines.append(f"| スタック名 | {report.environment.stack_name} |")
    lines.append(f"| Lambda 関数名 | {report.environment.lambda_function_name} |")
    lines.append(f"| Datadog サイト | {report.environment.datadog_site} |")
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


def _render_issues(issues: list[Issue]) -> str:
    """Render the issues section."""
    lines: list[str] = []

    lines.append("## 検出された問題点")
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


def _truncate_output(output: str) -> str:
    """Truncate output to a maximum number of lines.

    Args:
        output: The raw output string.

    Returns:
        The output truncated to _MAX_OUTPUT_LINES lines, with a
        truncation notice appended if lines were removed.
    """
    lines = output.splitlines()
    if len(lines) <= _MAX_OUTPUT_LINES:
        return output

    truncated = lines[:_MAX_OUTPUT_LINES]
    remaining = len(lines) - _MAX_OUTPUT_LINES
    truncated.append(f"... ({remaining} 行省略)")
    return "\n".join(truncated)
