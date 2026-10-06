"""Integration tests for the full verification pipeline.

Tests end-to-end flows wiring bilingual comparison, screenshot validation,
and results rendering together with real file I/O via tmp_path.

Validates: Requirements 8.1, 9.1, 9.5
"""

from __future__ import annotations

from scripts.verification.bilingual_comparator import compare_setup_guides
from scripts.verification.models import (
    BilingualComparisonResult,
    Issue,
    VerificationEnvironment,
    VerificationReport,
    VerificationStep,
    VerifierInfo,
)
from scripts.verification.results_renderer import render_bilingual_summary, render_report
from scripts.verification.screenshot_validator import (
    MIN_FILE_SIZE_BYTES,
    PNG_MAGIC_BYTES,
    REQUIRED_SCREENSHOTS,
    validate_screenshots,
)


class TestBilingualToRendererPipeline:
    """Integration: bilingual comparison → render_bilingual_summary."""

    def test_pass_result_renders_correctly(
        self, tmp_path, sample_markdown_ja, sample_markdown_en
    ):
        """Identical ja/en docs produce a pass summary with counts."""
        ja_file = tmp_path / "ja" / "setup-guide.md"
        en_file = tmp_path / "en" / "setup-guide.md"
        ja_file.parent.mkdir(parents=True)
        en_file.parent.mkdir(parents=True)
        ja_file.write_text(sample_markdown_ja, encoding="utf-8")
        en_file.write_text(sample_markdown_en, encoding="utf-8")

        # Run comparison
        comparison_result = compare_setup_guides(str(ja_file), str(en_file))

        # Pass result to renderer
        summary = render_bilingual_summary(comparison_result)

        # Verify output contains expected fields
        assert "pass" in summary
        assert str(comparison_result.heading_count) in summary
        assert str(comparison_result.code_block_count) in summary
        assert str(comparison_result.table_count) in summary
        assert "0" in summary  # 0 differences
        assert str(ja_file) in summary
        assert str(en_file) in summary

    def test_fail_result_renders_differences(self, tmp_path):
        """Structurally different docs produce a fail summary with diffs."""
        content_ja = "# タイトル\n\n## セクション1\n\n## セクション2\n\n```bash\necho hello\n```\n"
        content_en = "# Title\n\n## Section 1\n\n```bash\necho hello\n```\n"

        ja_file = tmp_path / "ja" / "setup-guide.md"
        en_file = tmp_path / "en" / "setup-guide.md"
        ja_file.parent.mkdir(parents=True)
        en_file.parent.mkdir(parents=True)
        ja_file.write_text(content_ja, encoding="utf-8")
        en_file.write_text(content_en, encoding="utf-8")

        # Run comparison
        comparison_result = compare_setup_guides(str(ja_file), str(en_file))

        # Pass result to renderer
        summary = render_bilingual_summary(comparison_result)

        # Verify output reflects failure
        assert "fail" in summary
        assert "heading" in summary
        assert str(ja_file) in summary
        assert str(en_file) in summary
        assert len(comparison_result.differences) > 0

    def test_missing_file_renders_error(self, tmp_path):
        """Missing file produces a fail summary with error info."""
        content_en = "# Title\n\n## Section\n"
        en_file = tmp_path / "en" / "setup-guide.md"
        en_file.parent.mkdir(parents=True)
        en_file.write_text(content_en, encoding="utf-8")

        nonexistent_ja = str(tmp_path / "ja" / "setup-guide.md")

        # Run comparison with missing file
        comparison_result = compare_setup_guides(nonexistent_ja, str(en_file))

        # Pass result to renderer
        summary = render_bilingual_summary(comparison_result)

        assert "fail" in summary
        assert len(comparison_result.differences) >= 1


class TestScreenshotToRendererPipeline:
    """Integration: screenshot validation → render_report."""

    def _create_valid_png(self, path, size: int = 2048) -> None:
        """Create a valid PNG file with proper magic bytes and minimum size."""
        # PNG magic bytes + enough padding to exceed MIN_FILE_SIZE_BYTES
        content = PNG_MAGIC_BYTES + b"\x00" * (size - len(PNG_MAGIC_BYTES))
        path.write_bytes(content)

    def test_all_valid_screenshots_render_success_steps(self, tmp_path):
        """All valid PNGs produce success steps in the rendered report."""
        # Create all required screenshots as valid PNGs
        for filename in REQUIRED_SCREENSHOTS:
            self._create_valid_png(tmp_path / filename)

        # Run validation
        steps = validate_screenshots(str(tmp_path))

        # Build a report with the validation steps
        report = VerificationReport(
            verification_date="2026-01-15T14:30:00+09:00",
            environment=VerificationEnvironment(
                aws_region="ap-northeast-1",
                stack_name="fsxn-datadog-integration",
                lambda_function_name="fsxn-datadog-integration-shipper",
                datadog_site="datadoghq.com",
            ),
            verifier=VerifierInfo(name="田中太郎", role="SRE Engineer"),
            steps=steps,
            issues=[],
        )

        # Render the report
        output = render_report(report)

        # Verify all steps appear as success
        for step in steps:
            assert step.result == "success"
            assert step.step_name in output

        # Verify screenshot paths are referenced
        assert "✅ 成功" in output
        assert "❌ 失敗" not in output

    def test_missing_screenshots_render_failure_steps(self, tmp_path):
        """Missing PNGs produce failure steps with error details in report."""
        # Create only 2 of the 5 required screenshots
        self._create_valid_png(tmp_path / REQUIRED_SCREENSHOTS[0])
        self._create_valid_png(tmp_path / REQUIRED_SCREENSHOTS[1])

        # Run validation
        steps = validate_screenshots(str(tmp_path))

        # Build a report with the validation steps
        report = VerificationReport(
            verification_date="2026-01-15T14:30:00+09:00",
            environment=VerificationEnvironment(
                aws_region="ap-northeast-1",
                stack_name="fsxn-datadog-integration",
                lambda_function_name="fsxn-datadog-integration-shipper",
                datadog_site="datadoghq.com",
            ),
            verifier=VerifierInfo(name="田中太郎", role="SRE Engineer"),
            steps=steps,
            issues=[],
        )

        # Render the report
        output = render_report(report)

        # Verify mix of success and failure
        success_steps = [s for s in steps if s.result == "success"]
        failure_steps = [s for s in steps if s.result == "failure"]
        assert len(success_steps) == 2
        assert len(failure_steps) == 3

        # Verify failure details appear in output
        assert "❌ 失敗" in output
        assert "File not found" in output

    def test_invalid_png_renders_failure_with_format_error(self, tmp_path):
        """Non-PNG file produces failure step with format error in report."""
        # Create a file with wrong magic bytes but sufficient size
        invalid_file = tmp_path / REQUIRED_SCREENSHOTS[0]
        invalid_file.write_bytes(b"NOT_A_PNG" + b"\x00" * 2048)

        # Create remaining files as valid PNGs
        for filename in REQUIRED_SCREENSHOTS[1:]:
            self._create_valid_png(tmp_path / filename)

        # Run validation
        steps = validate_screenshots(str(tmp_path))

        # Build and render report
        report = VerificationReport(
            verification_date="2026-01-15T14:30:00+09:00",
            environment=VerificationEnvironment(
                aws_region="ap-northeast-1",
                stack_name="fsxn-datadog-integration",
                lambda_function_name="fsxn-datadog-integration-shipper",
                datadog_site="datadoghq.com",
            ),
            verifier=VerifierInfo(name="田中太郎", role="SRE Engineer"),
            steps=steps,
            issues=[],
        )

        output = render_report(report)

        # First step should fail with PNG format error
        assert steps[0].result == "failure"
        assert "Invalid PNG format" in steps[0].error_detail
        assert "Invalid PNG format" in output


class TestFullPipelineIntegration:
    """Integration: all components wired together for full report generation."""

    def _create_valid_png(self, path, size: int = 2048) -> None:
        """Create a valid PNG file with proper magic bytes and minimum size."""
        content = PNG_MAGIC_BYTES + b"\x00" * (size - len(PNG_MAGIC_BYTES))
        path.write_bytes(content)

    def test_full_report_with_all_sections(
        self, tmp_path, sample_markdown_ja, sample_markdown_en
    ):
        """Full pipeline: bilingual + screenshots + report with all sections."""
        # --- Setup bilingual docs ---
        ja_file = tmp_path / "docs" / "ja" / "setup-guide.md"
        en_file = tmp_path / "docs" / "en" / "setup-guide.md"
        ja_file.parent.mkdir(parents=True)
        en_file.parent.mkdir(parents=True)
        ja_file.write_text(sample_markdown_ja, encoding="utf-8")
        en_file.write_text(sample_markdown_en, encoding="utf-8")

        # --- Setup screenshots ---
        screenshot_dir = tmp_path / "screenshots"
        screenshot_dir.mkdir()
        for filename in REQUIRED_SCREENSHOTS:
            self._create_valid_png(screenshot_dir / filename)

        # --- Run bilingual comparison ---
        bilingual_result = compare_setup_guides(str(ja_file), str(en_file))

        # --- Run screenshot validation ---
        screenshot_steps = validate_screenshots(str(screenshot_dir))

        # --- Build full report ---
        report = VerificationReport(
            verification_date="2026-01-15T14:30:00+09:00",
            environment=VerificationEnvironment(
                aws_region="ap-northeast-1",
                stack_name="fsxn-datadog-integration",
                lambda_function_name="fsxn-datadog-integration-shipper",
                datadog_site="datadoghq.com",
            ),
            verifier=VerifierInfo(name="リポジトリ管理者", role="Cloud Engineer"),
            steps=[
                VerificationStep(
                    step_number=1,
                    step_name="Deploy CloudFormation Stack",
                    result="success",
                    command="aws cloudformation deploy --template-file template.yaml --stack-name fsxn-datadog-integration",
                    output="Stack fsxn-datadog-integration created successfully.",
                    timestamp="2026-01-15T14:30:00+09:00",
                ),
                VerificationStep(
                    step_number=2,
                    step_name="Invoke Lambda with Test Event",
                    result="success",
                    command="aws lambda invoke --function-name fsxn-datadog-integration-shipper --payload file://test-event.json response.json",
                    output='{"statusCode": 200, "body": {"total_logs": 5, "total_shipped": 5, "errors": []}}',
                    timestamp="2026-01-15T14:32:00+09:00",
                ),
                *screenshot_steps,
            ],
            bilingual_comparison=bilingual_result,
            issues=[],
        )

        # --- Render full report ---
        output = render_report(report)

        # --- Verify all sections present ---
        # Title
        assert "Datadog 統合 動作確認結果" in output

        # Header: date, environment, verifier
        assert "2026-01-15T14:30:00+09:00" in output
        assert "ap-northeast-1" in output
        assert "fsxn-datadog-integration" in output
        assert "fsxn-datadog-integration-shipper" in output
        assert "datadoghq.com" in output
        assert "リポジトリ管理者" in output
        assert "Cloud Engineer" in output

        # Steps section
        assert "検証ステップ" in output
        assert "Deploy CloudFormation Stack" in output
        assert "Invoke Lambda with Test Event" in output

        # Screenshot validation steps
        for filename in REQUIRED_SCREENSHOTS:
            assert filename in output

        # All screenshot steps should be success
        for step in screenshot_steps:
            assert step.result == "success"

        # Issues section (empty → 問題なし)
        assert "問題なし" in output

        # --- Render bilingual summary separately ---
        bilingual_summary = render_bilingual_summary(bilingual_result)
        assert "pass" in bilingual_summary
        assert str(bilingual_result.heading_count) in bilingual_summary
        assert str(bilingual_result.code_block_count) in bilingual_summary

    def test_full_report_with_failures_and_issues(self, tmp_path):
        """Full pipeline with failures produces report with issues section."""
        # --- Setup bilingual docs with structural difference ---
        ja_content = "# タイトル\n\n## セクション1\n\n## セクション2\n\n```bash\necho hello\n```\n"
        en_content = "# Title\n\n## Section 1\n\n```bash\necho hello\n```\n"

        ja_file = tmp_path / "docs" / "ja" / "setup-guide.md"
        en_file = tmp_path / "docs" / "en" / "setup-guide.md"
        ja_file.parent.mkdir(parents=True)
        en_file.parent.mkdir(parents=True)
        ja_file.write_text(ja_content, encoding="utf-8")
        en_file.write_text(en_content, encoding="utf-8")

        # --- Setup screenshots with some missing ---
        screenshot_dir = tmp_path / "screenshots"
        screenshot_dir.mkdir()
        # Only create first 3 screenshots
        for filename in REQUIRED_SCREENSHOTS[:3]:
            self._create_valid_png(screenshot_dir / filename)

        # --- Run bilingual comparison ---
        bilingual_result = compare_setup_guides(str(ja_file), str(en_file))
        assert bilingual_result.status == "fail"

        # --- Run screenshot validation ---
        screenshot_steps = validate_screenshots(str(screenshot_dir))

        # --- Build report with issues ---
        report = VerificationReport(
            verification_date="2026-01-15T15:00:00+09:00",
            environment=VerificationEnvironment(
                aws_region="ap-northeast-1",
                stack_name="fsxn-datadog-integration",
                lambda_function_name="fsxn-datadog-integration-shipper",
                datadog_site="datadoghq.com",
            ),
            verifier=VerifierInfo(name="リポジトリ管理者", role="Cloud Engineer"),
            steps=screenshot_steps,
            bilingual_comparison=bilingual_result,
            issues=[
                Issue(
                    title="スクリーンショット不足",
                    description="2件のスクリーンショットが見つかりません",
                    resolution="手動で再撮影してください",
                ),
                Issue(
                    title="バイリンガル不一致",
                    description="日英ドキュメントの見出し構造が一致しません",
                    resolution="英語版に不足している見出しを追加してください",
                ),
            ],
        )

        # --- Render full report ---
        output = render_report(report)

        # Verify failure steps present
        failure_steps = [s for s in screenshot_steps if s.result == "failure"]
        assert len(failure_steps) == 2
        assert "❌ 失敗" in output
        assert "File not found" in output

        # Verify issues section (not 問題なし)
        assert "問題なし" not in output
        assert "スクリーンショット不足" in output
        assert "バイリンガル不一致" in output
        assert "手動で再撮影してください" in output
        assert "英語版に不足している見出しを追加してください" in output

        # Verify bilingual summary shows failure
        bilingual_summary = render_bilingual_summary(bilingual_result)
        assert "fail" in bilingual_summary
        assert len(bilingual_result.differences) > 0

    def test_full_report_screenshot_paths_are_referenced(self, tmp_path):
        """Screenshot paths from validation appear as image links in report."""
        screenshot_dir = tmp_path / "screenshots"
        screenshot_dir.mkdir()
        for filename in REQUIRED_SCREENSHOTS:
            self._create_valid_png(screenshot_dir / filename)

        steps = validate_screenshots(str(screenshot_dir))

        report = VerificationReport(
            verification_date="2026-01-15T14:30:00+09:00",
            environment=VerificationEnvironment(
                aws_region="ap-northeast-1",
                stack_name="fsxn-datadog-integration",
                lambda_function_name="fsxn-datadog-integration-shipper",
                datadog_site="datadoghq.com",
            ),
            verifier=VerifierInfo(name="田中太郎", role="SRE Engineer"),
            steps=steps,
            issues=[],
        )

        output = render_report(report)

        # Each successful step with a screenshot_path should have an image link
        for step in steps:
            if step.screenshot_path:
                assert f"![{step.step_name}]({step.screenshot_path})" in output
