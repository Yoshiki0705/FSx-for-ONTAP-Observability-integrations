#!/usr/bin/env python3
"""Verification script for docs/ja/event-sources.md.

Extracts ONTAP CLI commands from the event-sources.md documentation,
documents expected execution order and validation checks, and runs
bilingual code block comparison between Japanese and English versions.

Generates a structured JSON report with command verification results.

Usage:
    python verify-event-sources.py --docs-dir ./docs --output results.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------


@dataclass
class CommandVerification:
    """A single ONTAP CLI command verification entry."""

    command: str
    section: str
    execution_order: int
    expected_result: str
    actual_result: str
    status: str  # "PASS" or "FAIL"
    verification_command: str
    error_content: str | None = None
    correct_syntax: str | None = None
    fix_proposal: str | None = None


@dataclass
class BilingualDifference:
    """A code block difference between ja and en documentation."""

    block_index: int
    ja_content: str | None
    en_content: str | None


@dataclass
class VerificationReport:
    """Complete verification report for event-sources.md."""

    document_path: str
    ems_webhook_commands: list[dict[str, Any]] = field(default_factory=list)
    fpolicy_commands: list[dict[str, Any]] = field(default_factory=list)
    bilingual_comparison: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Bilingual Code Block Comparison
# ---------------------------------------------------------------------------


def compare_code_blocks(ja_path: str, en_path: str) -> list[dict[str, Any]]:
    """Compare fenced code blocks between Japanese and English Markdown files.

    Extracts all fenced code blocks (delimited by ```) from both files and
    compares them in order of appearance. Code blocks are expected to be
    identical across translations since they contain language-independent
    technical content.

    Args:
        ja_path: Path to the Japanese Markdown file.
        en_path: Path to the English Markdown file.

    Returns:
        Empty list if all code blocks match. Otherwise, a list of
        dictionaries describing each difference with keys:
        - block_index: Zero-based index of the differing code block.
        - ja_content: Content of the code block in the Japanese file.
        - en_content: Content of the code block in the English file.

    Raises:
        FileNotFoundError: If either file does not exist.
    """
    ja_blocks = _extract_code_blocks(ja_path)
    en_blocks = _extract_code_blocks(en_path)

    differences: list[dict[str, Any]] = []
    max_len = max(len(ja_blocks), len(en_blocks))

    for i in range(max_len):
        ja_content = ja_blocks[i] if i < len(ja_blocks) else None
        en_content = en_blocks[i] if i < len(en_blocks) else None

        if ja_content != en_content:
            differences.append({
                "block_index": i,
                "ja_content": ja_content,
                "en_content": en_content,
            })

    return differences


def _extract_code_blocks(file_path: str) -> list[str]:
    """Extract all fenced code blocks from a Markdown file.

    Args:
        file_path: Path to the Markdown file.

    Returns:
        List of code block contents (without the fence delimiters).

    Raises:
        FileNotFoundError: If the file does not exist.
    """
    path = Path(file_path)
    content = path.read_text(encoding="utf-8")

    pattern = re.compile(r"^```[^\n]*\n(.*?)^```", re.MULTILINE | re.DOTALL)
    matches = pattern.findall(content)

    return matches


# ---------------------------------------------------------------------------
# ONTAP CLI Command Extraction
# ---------------------------------------------------------------------------


def extract_ontap_commands(md_path: str) -> dict[str, list[str]]:
    """Extract ONTAP CLI commands from event-sources.md.

    Parses the Markdown file and extracts commands from the EMS Webhook
    section (Pattern A) and the FPolicy section.

    Args:
        md_path: Path to the event-sources.md file.

    Returns:
        Dictionary with keys 'ems_webhook' and 'fpolicy', each containing
        a list of CLI command strings.
    """
    path = Path(md_path)
    content = path.read_text(encoding="utf-8")

    commands: dict[str, list[str]] = {
        "ems_webhook": [],
        "fpolicy": [],
    }

    # Extract bash code blocks and categorize by section
    sections = _split_by_sections(content)

    for section_name, section_content in sections.items():
        bash_blocks = _extract_bash_commands(section_content)
        if "EMS" in section_name or "Webhook" in section_name:
            commands["ems_webhook"].extend(bash_blocks)
        elif "FPolicy" in section_name:
            commands["fpolicy"].extend(bash_blocks)

    return commands


def _split_by_sections(content: str) -> dict[str, str]:
    """Split Markdown content into sections by ## headings.

    Args:
        content: Full Markdown content.

    Returns:
        Dictionary mapping section heading to section content.
    """
    sections: dict[str, str] = {}
    current_heading = ""
    current_content: list[str] = []

    for line in content.split("\n"):
        if line.startswith("## "):
            if current_heading:
                sections[current_heading] = "\n".join(current_content)
            current_heading = line.lstrip("# ").strip()
            current_content = []
        else:
            current_content.append(line)

    if current_heading:
        sections[current_heading] = "\n".join(current_content)

    return sections


def _extract_bash_commands(section_content: str) -> list[str]:
    """Extract individual CLI commands from bash code blocks.

    Handles multi-line commands joined with backslash continuation.

    Args:
        section_content: Content of a Markdown section.

    Returns:
        List of complete CLI command strings.
    """
    # Find bash code blocks
    pattern = re.compile(r"```bash\n(.*?)```", re.DOTALL)
    matches = pattern.findall(section_content)

    commands: list[str] = []
    for block in matches:
        # Parse individual commands, handling line continuations
        current_cmd = ""
        for line in block.split("\n"):
            stripped = line.strip()
            # Skip comments and empty lines
            if not stripped or stripped.startswith("#"):
                if current_cmd:
                    commands.append(current_cmd.strip())
                    current_cmd = ""
                continue

            if stripped.endswith("\\"):
                current_cmd += stripped[:-1].strip() + " "
            else:
                current_cmd += stripped
                commands.append(current_cmd.strip())
                current_cmd = ""

        if current_cmd:
            commands.append(current_cmd.strip())

    return commands


# ---------------------------------------------------------------------------
# Command Verification Logic
# ---------------------------------------------------------------------------


def build_ems_webhook_verifications(
    commands: list[str],
) -> list[dict[str, Any]]:
    """Build verification entries for EMS Webhook ONTAP CLI commands.

    Documents expected execution order, success output, and verification
    commands for each EMS Webhook CLI command.

    Args:
        commands: List of EMS Webhook CLI command strings.

    Returns:
        List of verification dictionaries.
    """
    verifications: list[dict[str, Any]] = []

    for i, cmd in enumerate(commands, start=1):
        verification = _build_command_verification(
            command=cmd,
            section="EMS Webhook (Pattern A)",
            execution_order=i,
        )
        verifications.append(verification)

    return verifications


def build_fpolicy_verifications(
    commands: list[str],
) -> list[dict[str, Any]]:
    """Build verification entries for FPolicy ONTAP CLI commands.

    Documents expected execution order, success output, and verification
    commands for each FPolicy CLI command.

    Args:
        commands: List of FPolicy CLI command strings.

    Returns:
        List of verification dictionaries.
    """
    verifications: list[dict[str, Any]] = []

    for i, cmd in enumerate(commands, start=1):
        verification = _build_command_verification(
            command=cmd,
            section="FPolicy",
            execution_order=i,
        )
        verifications.append(verification)

    return verifications


def _build_command_verification(
    command: str,
    section: str,
    execution_order: int,
) -> dict[str, Any]:
    """Build a single command verification entry.

    Determines expected result and verification command based on the
    command type.

    Args:
        command: The ONTAP CLI command string.
        section: Section name (EMS Webhook or FPolicy).
        execution_order: Execution order within the section.

    Returns:
        Verification dictionary with command details.
    """
    expected_result = _get_expected_result(command)
    verification_cmd = _get_verification_command(command)

    return {
        "command": command,
        "section": section,
        "execution_order": execution_order,
        "expected_result": expected_result,
        "actual_result": "",  # Populated during actual execution
        "status": "NOT_RUN",  # PASS, FAIL, or NOT_RUN
        "verification_command": verification_cmd,
        "error_content": None,
        "correct_syntax": None,
        "fix_proposal": None,
    }


def _get_expected_result(command: str) -> str:
    """Determine expected success output for an ONTAP CLI command.

    Args:
        command: The ONTAP CLI command string.

    Returns:
        Description of expected successful output.
    """
    if "event notification destination create" in command:
        return (
            "Command completes without error. "
            "Notification destination is created successfully."
        )
    elif "event filter create" in command:
        return (
            "Command completes without error. "
            "Event filter is created successfully."
        )
    elif "event filter rule add" in command:
        return (
            "Command completes without error. "
            "Filter rule is added to the specified filter."
        )
    elif "event notification create" in command:
        return (
            "Command completes without error. "
            "Event notification is configured with the specified filter and destination."
        )
    elif "fpolicy policy external-engine create" in command:
        return (
            "Command completes without error. "
            "FPolicy external engine is created with specified parameters."
        )
    elif "fpolicy policy event create" in command:
        return (
            "Command completes without error. "
            "FPolicy event is created with specified protocol and file operations."
        )
    elif "fpolicy policy create" in command:
        return (
            "Command completes without error. "
            "FPolicy policy is created linking the event and engine."
        )
    elif "fpolicy enable" in command:
        return (
            "Command completes without error. "
            "FPolicy policy is enabled with the specified sequence number."
        )
    else:
        return "Command completes without error."


def _get_verification_command(command: str) -> str:
    """Determine the verification command to confirm settings were applied.

    Args:
        command: The ONTAP CLI command string.

    Returns:
        ONTAP CLI command to verify the setting was applied.
    """
    if "event notification destination create" in command:
        # Extract destination name if possible
        name_match = re.search(r"-name\s+(\S+)", command)
        name = name_match.group(1) if name_match else "<destination-name>"
        return f"event notification destination show -name {name}"
    elif "event filter create" in command:
        name_match = re.search(r"-filter-name\s+(\S+)", command)
        name = name_match.group(1) if name_match else "<filter-name>"
        return f"event filter show -filter-name {name}"
    elif "event filter rule add" in command:
        name_match = re.search(r"-filter-name\s+(\S+)", command)
        name = name_match.group(1) if name_match else "<filter-name>"
        return f"event filter rule show -filter-name {name}"
    elif "event notification create" in command:
        return "event notification show"
    elif "fpolicy policy external-engine create" in command:
        vserver_match = re.search(r"-vserver\s+(\S+)", command)
        vserver = vserver_match.group(1) if vserver_match else "<vserver>"
        return f"vserver fpolicy show-engine -vserver {vserver}"
    elif "fpolicy policy event create" in command:
        vserver_match = re.search(r"-vserver\s+(\S+)", command)
        vserver = vserver_match.group(1) if vserver_match else "<vserver>"
        return f"vserver fpolicy policy event show -vserver {vserver}"
    elif "fpolicy policy create" in command:
        vserver_match = re.search(r"-vserver\s+(\S+)", command)
        vserver = vserver_match.group(1) if vserver_match else "<vserver>"
        return f"vserver fpolicy show -vserver {vserver}"
    elif "fpolicy enable" in command:
        vserver_match = re.search(r"-vserver\s+(\S+)", command)
        vserver = vserver_match.group(1) if vserver_match else "<vserver>"
        return f"vserver fpolicy show -vserver {vserver} -status-enabled true"
    else:
        return "# No specific verification command defined"


# ---------------------------------------------------------------------------
# Report Generation
# ---------------------------------------------------------------------------


def generate_report(
    docs_dir: str,
) -> dict[str, Any]:
    """Generate the full verification report.

    Extracts commands from event-sources.md, builds verification entries,
    and runs bilingual code block comparison.

    Args:
        docs_dir: Path to the docs/ directory.

    Returns:
        Structured report dictionary.
    """
    docs_path = Path(docs_dir)
    ja_event_sources = docs_path / "ja" / "event-sources.md"
    en_event_sources = docs_path / "en" / "event-sources.md"

    # Validate files exist
    if not ja_event_sources.exists():
        raise FileNotFoundError(f"Japanese event-sources.md not found: {ja_event_sources}")
    if not en_event_sources.exists():
        raise FileNotFoundError(f"English event-sources.md not found: {en_event_sources}")

    # Extract ONTAP CLI commands
    commands = extract_ontap_commands(str(ja_event_sources))

    # Build verification entries
    ems_verifications = build_ems_webhook_verifications(commands["ems_webhook"])
    fpolicy_verifications = build_fpolicy_verifications(commands["fpolicy"])

    # Run bilingual code block comparison
    bilingual_diffs = compare_code_blocks(str(ja_event_sources), str(en_event_sources))

    # Build summary
    total_commands = len(ems_verifications) + len(fpolicy_verifications)
    bilingual_status = "PASS" if not bilingual_diffs else "FAIL"

    report = {
        "document_path": str(ja_event_sources),
        "ems_webhook_commands": ems_verifications,
        "fpolicy_commands": fpolicy_verifications,
        "bilingual_comparison": {
            "ja_path": str(ja_event_sources),
            "en_path": str(en_event_sources),
            "differences": bilingual_diffs,
            "status": bilingual_status,
            "total_ja_blocks": len(_extract_code_blocks(str(ja_event_sources))),
            "total_en_blocks": len(_extract_code_blocks(str(en_event_sources))),
        },
        "summary": {
            "total_ems_commands": len(ems_verifications),
            "total_fpolicy_commands": len(fpolicy_verifications),
            "total_commands": total_commands,
            "bilingual_status": bilingual_status,
            "bilingual_differences_count": len(bilingual_diffs),
        },
    }

    return report


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Argument list (defaults to sys.argv[1:]).

    Returns:
        Parsed arguments namespace.
    """
    parser = argparse.ArgumentParser(
        description="Verify accuracy of docs/ja/event-sources.md ONTAP CLI commands.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  python verify-event-sources.py --docs-dir ./docs --output results.json
  python verify-event-sources.py --docs-dir /path/to/docs
""",
    )
    parser.add_argument(
        "--docs-dir",
        type=str,
        required=True,
        help="Path to the docs/ directory containing ja/ and en/ subdirectories.",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="verification-results.json",
        help="Output file path for the JSON report (default: verification-results.json).",
    )

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Main entry point for the verification script.

    Args:
        argv: Command-line arguments (defaults to sys.argv[1:]).

    Returns:
        Exit code: 0 for success, 1 for errors.
    """
    args = parse_args(argv)

    try:
        report = generate_report(args.docs_dir)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # Write report to output file
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # Print summary to stdout
    print(f"Verification report written to: {output_path}")
    print(f"  EMS Webhook commands: {report['summary']['total_ems_commands']}")
    print(f"  FPolicy commands: {report['summary']['total_fpolicy_commands']}")
    print(f"  Total commands: {report['summary']['total_commands']}")
    print(f"  Bilingual comparison: {report['summary']['bilingual_status']}")
    if report["summary"]["bilingual_differences_count"] > 0:
        print(
            f"  Bilingual differences: {report['summary']['bilingual_differences_count']}"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
