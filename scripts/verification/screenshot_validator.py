"""Screenshot validation for E2E verification workflows.

Validates that all required screenshots exist in the expected directory,
meet file size requirements, use valid PNG format, and follow naming
conventions. Supports vendor-specific screenshot sets (Datadog, New Relic).
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone

from scripts.verification.models import VerificationStep

# Datadog required screenshots
REQUIRED_SCREENSHOTS = [
    "datadog-logs-arrival.png",
    "datadog-pipeline-config.png",
    "datadog-facets-config.png",
    "datadog-dashboard.png",
    "datadog-unauthorized-access.png",
]

# New Relic required screenshots
NEW_RELIC_REQUIRED_SCREENSHOTS = [
    "logs-ui-arrival.png",
    "nrql-query-result.png",
    "alert-condition-config.png",
    "alert-policy-overview.png",
]

PNG_MAGIC_BYTES = b"\x89PNG\r\n\x1a\n"

MIN_FILE_SIZE_BYTES = 1024  # 1KB
MAX_FILE_SIZE_BYTES = 500 * 1024  # 500KB

# Kebab-case filename pattern: 3-50 lowercase alphanumeric + hyphens
KEBAB_CASE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9\-]{1,48}[a-z0-9]$")


def validate_screenshots(screenshot_dir: str) -> list[VerificationStep]:
    """Validate all required screenshots exist and are valid PNG files.

    Checks each required screenshot file for:
    1. Existence in the specified directory
    2. Minimum file size (>= 1KB)
    3. Valid PNG magic bytes in the file header

    Args:
        screenshot_dir: Path to the directory containing screenshot files.

    Returns:
        A list of VerificationStep instances, one per required screenshot,
        with result="success" if all checks pass or result="failure" with
        error details if any check fails.
    """
    results: list[VerificationStep] = []
    timestamp = datetime.now(timezone.utc).isoformat()

    for step_number, filename in enumerate(REQUIRED_SCREENSHOTS, start=1):
        filepath = os.path.join(screenshot_dir, filename)
        step = _validate_single_screenshot(step_number, filename, filepath, timestamp)
        results.append(step)

    return results


def _validate_single_screenshot(
    step_number: int,
    filename: str,
    filepath: str,
    timestamp: str,
) -> VerificationStep:
    """Validate a single screenshot file.

    Args:
        step_number: The step number for this validation.
        filename: The expected filename of the screenshot.
        filepath: The full path to the screenshot file.
        timestamp: ISO 8601 timestamp for the verification step.

    Returns:
        A VerificationStep with the validation result.
    """
    step_name = f"Screenshot validation: {filename}"

    # Check existence
    if not os.path.exists(filepath):
        return VerificationStep(
            step_number=step_number,
            step_name=step_name,
            result="failure",
            screenshot_path=filepath,
            error_detail=f"File not found: {filepath}",
            timestamp=timestamp,
        )

    # Check file size
    file_size = os.path.getsize(filepath)
    if file_size < MIN_FILE_SIZE_BYTES:
        return VerificationStep(
            step_number=step_number,
            step_name=step_name,
            result="failure",
            screenshot_path=filepath,
            error_detail=(
                f"File too small: {file_size} bytes "
                f"(minimum {MIN_FILE_SIZE_BYTES} bytes)"
            ),
            timestamp=timestamp,
        )

    # Check PNG magic bytes
    try:
        with open(filepath, "rb") as f:
            header = f.read(8)
    except OSError as e:
        return VerificationStep(
            step_number=step_number,
            step_name=step_name,
            result="failure",
            screenshot_path=filepath,
            error_detail=f"Failed to read file: {e}",
            timestamp=timestamp,
        )

    if header != PNG_MAGIC_BYTES:
        return VerificationStep(
            step_number=step_number,
            step_name=step_name,
            result="failure",
            screenshot_path=filepath,
            error_detail=(
                "Invalid PNG format: file does not start with PNG magic bytes"
            ),
            timestamp=timestamp,
        )

    # All checks passed
    return VerificationStep(
        step_number=step_number,
        step_name=step_name,
        result="success",
        screenshot_path=filepath,
        timestamp=timestamp,
    )


def validate_new_relic_screenshots(
    screenshot_dir: str,
) -> list[VerificationStep]:
    """Validate all required New Relic screenshots exist and meet criteria.

    Checks each required screenshot file for:
    1. Existence in the specified directory
    2. Kebab-case filename (3-50 characters, lowercase alphanumeric + hyphens)
    3. Minimum file size (>= 1KB)
    4. Maximum file size (<= 500KB)
    5. Valid PNG magic bytes in the file header

    Args:
        screenshot_dir: Path to the directory containing New Relic
            screenshot files (e.g., ``docs/screenshots/new-relic/``).

    Returns:
        A list of VerificationStep instances, one per required screenshot,
        with result="success" if all checks pass or result="failure" with
        error details if any check fails.
    """
    results: list[VerificationStep] = []
    timestamp = datetime.now(timezone.utc).isoformat()

    for step_number, filename in enumerate(
        NEW_RELIC_REQUIRED_SCREENSHOTS, start=1
    ):
        filepath = os.path.join(screenshot_dir, filename)
        step = _validate_new_relic_single_screenshot(
            step_number, filename, filepath, timestamp
        )
        results.append(step)

    return results


def _validate_new_relic_single_screenshot(
    step_number: int,
    filename: str,
    filepath: str,
    timestamp: str,
) -> VerificationStep:
    """Validate a single New Relic screenshot file.

    Applies all validation rules: existence, kebab-case naming,
    minimum/maximum file size, and PNG magic bytes.

    Args:
        step_number: The step number for this validation.
        filename: The expected filename of the screenshot.
        filepath: The full path to the screenshot file.
        timestamp: ISO 8601 timestamp for the verification step.

    Returns:
        A VerificationStep with the validation result.
    """
    step_name = f"Screenshot validation: {filename}"

    # Check kebab-case filename (stem without .png extension)
    stem = filename.removesuffix(".png")
    if not KEBAB_CASE_PATTERN.match(stem):
        return VerificationStep(
            step_number=step_number,
            step_name=step_name,
            result="failure",
            screenshot_path=filepath,
            error_detail=(
                f"Invalid filename format: '{stem}' does not match "
                f"kebab-case pattern (3-50 lowercase alphanumeric + hyphens)"
            ),
            timestamp=timestamp,
        )

    # Check existence
    if not os.path.exists(filepath):
        return VerificationStep(
            step_number=step_number,
            step_name=step_name,
            result="failure",
            screenshot_path=filepath,
            error_detail=f"File not found: {filepath}",
            timestamp=timestamp,
        )

    # Check file size (minimum)
    file_size = os.path.getsize(filepath)
    if file_size < MIN_FILE_SIZE_BYTES:
        return VerificationStep(
            step_number=step_number,
            step_name=step_name,
            result="failure",
            screenshot_path=filepath,
            error_detail=(
                f"File too small: {file_size} bytes "
                f"(minimum {MIN_FILE_SIZE_BYTES} bytes)"
            ),
            timestamp=timestamp,
        )

    # Check file size (maximum)
    if file_size > MAX_FILE_SIZE_BYTES:
        return VerificationStep(
            step_number=step_number,
            step_name=step_name,
            result="failure",
            screenshot_path=filepath,
            error_detail=(
                f"File too large: {file_size} bytes "
                f"(maximum {MAX_FILE_SIZE_BYTES} bytes)"
            ),
            timestamp=timestamp,
        )

    # Check PNG magic bytes
    try:
        with open(filepath, "rb") as f:
            header = f.read(8)
    except OSError as e:
        return VerificationStep(
            step_number=step_number,
            step_name=step_name,
            result="failure",
            screenshot_path=filepath,
            error_detail=f"Failed to read file: {e}",
            timestamp=timestamp,
        )

    if header != PNG_MAGIC_BYTES:
        return VerificationStep(
            step_number=step_number,
            step_name=step_name,
            result="failure",
            screenshot_path=filepath,
            error_detail=(
                "Invalid PNG format: file does not start with PNG magic bytes"
            ),
            timestamp=timestamp,
        )

    # All checks passed
    return VerificationStep(
        step_number=step_number,
        step_name=step_name,
        result="success",
        screenshot_path=filepath,
        timestamp=timestamp,
    )
