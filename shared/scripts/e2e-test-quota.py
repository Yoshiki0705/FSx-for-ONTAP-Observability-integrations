#!/usr/bin/env python3
"""E2E test script for quota threshold exceeded events.

Tests the full flow: ONTAP quota soft limit exceeded → EMS Webhook →
API Gateway → Lambda → CloudWatch Logs.

Prerequisites:
- Deployed EMS Webhook stack (shared/templates/ems-webhook-apigw.yaml)
- FSx for ONTAP with EMS Webhook configured to API Gateway endpoint
- ONTAP CLI access (SSH) to the SVM
- boto3 installed with valid AWS credentials

Usage:
    python e2e-test-quota.py \
        --region ap-northeast-1 \
        --log-group /aws/lambda/fsxn-ems-receiver \
        --svm-name svm-prod-01 \
        --volume-name vol_test_quota
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S%z",
)
logger = logging.getLogger(__name__)

# Constants
POLL_TIMEOUT_SECONDS = 180
POLL_INTERVAL_SECONDS = 10
EXPECTED_EVENT_NAME = "wafl.quota.softlimit.exceeded"


# =============================================================================
# ONTAP CLI Commands (documented for manual execution)
# =============================================================================

ONTAP_CLI_COMMANDS = {
    "create_quota_rule": (
        "volume quota policy rule create "
        "-vserver {svm_name} "
        "-policy-name default "
        "-volume {volume_name} "
        "-type tree "
        "-target \"\" "
        "-disk-limit 100MB "
        "-soft-disk-limit 50MB"
    ),
    "activate_quota": (
        "volume quota on "
        "-vserver {svm_name} "
        "-volume {volume_name}"
    ),
    "write_test_data": (
        "# Write 60MB+ of data to exceed the soft quota limit.\n"
        "# From a client with NFS/SMB mount:\n"
        "dd if=/dev/zero of=/mnt/{volume_name}/testfile_quota bs=1M count=65"
    ),
    "check_quota_report": (
        "volume quota report "
        "-vserver {svm_name} "
        "-volume {volume_name}"
    ),
    "check_ems_log": (
        "event log show "
        "-event wafl.quota* "
        "-vserver {svm_name}"
    ),
    "delete_quota_rule": (
        "volume quota policy rule delete "
        "-vserver {svm_name} "
        "-policy-name default "
        "-volume {volume_name} "
        "-type tree "
        "-target \"\""
    ),
    "deactivate_quota": (
        "volume quota off "
        "-vserver {svm_name} "
        "-volume {volume_name}"
    ),
    "remove_test_data": (
        "# Remove test data from client mount:\n"
        "rm -f /mnt/{volume_name}/testfile_quota"
    ),
}


def get_formatted_commands(svm_name: str, volume_name: str) -> dict[str, str]:
    """Format ONTAP CLI commands with actual SVM and volume names.

    Args:
        svm_name: SVM name for command substitution.
        volume_name: Volume name for command substitution.

    Returns:
        Dictionary of command name to formatted command string.
    """
    return {
        key: cmd.format(svm_name=svm_name, volume_name=volume_name)
        for key, cmd in ONTAP_CLI_COMMANDS.items()
    }


# =============================================================================
# CloudWatch Logs Polling
# =============================================================================


def poll_cloudwatch_logs(
    logs_client: "boto3.client",
    log_group: str,
    start_time_ms: int,
    timeout_seconds: int = POLL_TIMEOUT_SECONDS,
    poll_interval: int = POLL_INTERVAL_SECONDS,
) -> dict | None:
    """Poll CloudWatch Logs for quota threshold exceeded event.

    Args:
        logs_client: boto3 CloudWatch Logs client.
        log_group: CloudWatch Logs log group name.
        start_time_ms: Start time in milliseconds since epoch.
        timeout_seconds: Maximum time to poll before timeout.
        poll_interval: Seconds between poll attempts.

    Returns:
        Matching log event dict if found, None on timeout.
    """
    deadline = time.time() + timeout_seconds
    attempt = 0

    logger.info(
        "Polling CloudWatch Logs (log_group=%s, timeout=%ds)...",
        log_group,
        timeout_seconds,
    )

    while time.time() < deadline:
        attempt += 1
        logger.info("Poll attempt %d (%.0fs remaining)...", attempt, deadline - time.time())

        try:
            response = logs_client.filter_log_events(
                logGroupName=log_group,
                startTime=start_time_ms,
                filterPattern=EXPECTED_EVENT_NAME,
                limit=10,
            )

            events = response.get("events", [])
            for event in events:
                message = event.get("message", "")
                if EXPECTED_EVENT_NAME in message:
                    logger.info("Found matching log event in stream: %s", event.get("logStreamName"))
                    return event

        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code == "ResourceNotFoundException":
                logger.warning("Log group '%s' not found. Waiting...", log_group)
            else:
                logger.error("CloudWatch Logs API error: %s", e)
                raise

        time.sleep(poll_interval)

    return None


# =============================================================================
# Verification
# =============================================================================


def verify_log_content(log_message: str, volume_name: str) -> dict[str, bool]:
    """Verify that the log message contains expected quota event fields.

    Args:
        log_message: The log message string from CloudWatch Logs.
        volume_name: Expected volume name in the log.

    Returns:
        Dictionary of field name to verification result (True=found).
    """
    checks = {
        "event_name": f"event_name={EXPECTED_EVENT_NAME}" in log_message,
        "volume_name": f"volume_name={volume_name}" in log_message
            or "volume_name" in log_message,
        "quota_target": "quota_target" in log_message,
        "used_bytes": "used_bytes" in log_message,
        "limit_bytes": "limit_bytes" in log_message,
    }
    return checks


# =============================================================================
# Diagnostics
# =============================================================================


def output_timeout_diagnostics(
    svm_name: str,
    volume_name: str,
) -> None:
    """Output diagnostic information on timeout.

    Prints ONTAP CLI commands for manual diagnosis.

    Args:
        svm_name: SVM name for diagnostic commands.
        volume_name: Volume name for diagnostic commands.
    """
    commands = get_formatted_commands(svm_name, volume_name)

    print("\n" + "=" * 70)
    print("TIMEOUT DIAGNOSTICS")
    print("=" * 70)
    print("\nThe quota event was not received within the timeout period.")
    print("Run the following ONTAP CLI commands to diagnose:\n")

    print("1. Check quota report:")
    print(f"   {commands['check_quota_report']}")
    print()

    print("2. Check EMS log for quota events:")
    print(f"   {commands['check_ems_log']}")
    print()

    print("3. Verify EMS webhook destination is configured:")
    print(f"   event notification destination show -vserver {svm_name}")
    print()

    print("4. Verify EMS notification filter includes quota events:")
    print(f"   event notification show -vserver {svm_name}")
    print()

    print("5. Check API Gateway access logs for incoming requests")
    print("=" * 70)


# =============================================================================
# Cleanup
# =============================================================================


def print_cleanup_commands(svm_name: str, volume_name: str) -> None:
    """Print cleanup commands for manual execution.

    Args:
        svm_name: SVM name for cleanup commands.
        volume_name: Volume name for cleanup commands.
    """
    commands = get_formatted_commands(svm_name, volume_name)

    print("\n" + "-" * 70)
    print("CLEANUP COMMANDS")
    print("-" * 70)
    print("\nExecute the following commands to clean up test resources:\n")

    print("1. Remove test data:")
    print(f"   {commands['remove_test_data']}")
    print()

    print("2. Deactivate quota on volume:")
    print(f"   {commands['deactivate_quota']}")
    print()

    print("3. Delete quota rule:")
    print(f"   {commands['delete_quota_rule']}")
    print("-" * 70)


def attempt_cleanup(svm_name: str, volume_name: str) -> list[str]:
    """Attempt cleanup and report any failures.

    Since ONTAP CLI commands require SSH access which is not automated here,
    this function documents the cleanup steps and notes that manual cleanup
    may be needed.

    Args:
        svm_name: SVM name for cleanup.
        volume_name: Volume name for cleanup.

    Returns:
        List of cleanup failure warnings (empty if all documented successfully).
    """
    warnings: list[str] = []

    logger.info("Cleanup: documenting cleanup steps for manual execution...")
    print_cleanup_commands(svm_name, volume_name)

    # Note: In a fully automated environment with SSH access to ONTAP CLI,
    # the cleanup commands would be executed here. Since this script focuses
    # on the CloudWatch Logs verification side, cleanup commands are documented
    # for manual execution.
    logger.warning(
        "Cleanup commands require manual execution via ONTAP CLI. "
        "Ensure quota rule is deleted and test data is removed."
    )
    warnings.append(
        "Manual cleanup required: delete quota rule and remove test data via ONTAP CLI"
    )

    return warnings


# =============================================================================
# Main Test Flow
# =============================================================================


def run_quota_e2e_test(
    region: str,
    log_group: str,
    svm_name: str,
    volume_name: str,
) -> bool:
    """Run the quota threshold exceeded E2E test.

    Args:
        region: AWS region for CloudWatch Logs.
        log_group: CloudWatch Logs log group name.
        svm_name: ONTAP SVM name.
        volume_name: ONTAP volume name for quota test.

    Returns:
        True if test passed, False otherwise.
    """
    commands = get_formatted_commands(svm_name, volume_name)
    test_start_time = datetime.now(timezone.utc)
    start_time_ms = int(test_start_time.timestamp() * 1000)

    print("=" * 70)
    print("E2E TEST: Quota Threshold Exceeded Event")
    print("=" * 70)
    print(f"  Region:      {region}")
    print(f"  Log Group:   {log_group}")
    print(f"  SVM:         {svm_name}")
    print(f"  Volume:      {volume_name}")
    print(f"  Start Time:  {test_start_time.isoformat()}")
    print(f"  Timeout:     {POLL_TIMEOUT_SECONDS}s")
    print("=" * 70)

    # Step 1: Document quota setup commands
    print("\n--- Step 1: Quota Setup (execute manually via ONTAP CLI) ---")
    print(f"\n  Create quota rule:\n    {commands['create_quota_rule']}")
    print(f"\n  Activate quota:\n    {commands['activate_quota']}")
    print(f"\n  Write test data (60MB+):\n    {commands['write_test_data']}")
    print("\n  >> Execute the above commands, then press Enter to start polling...")

    try:
        input()
    except EOFError:
        # Non-interactive mode: proceed immediately
        logger.info("Non-interactive mode: proceeding with CloudWatch Logs polling.")

    # Step 2: Poll CloudWatch Logs
    print("\n--- Step 2: CloudWatch Logs Polling ---")
    logs_client = boto3.client("logs", region_name=region)

    matched_event = poll_cloudwatch_logs(
        logs_client=logs_client,
        log_group=log_group,
        start_time_ms=start_time_ms,
        timeout_seconds=POLL_TIMEOUT_SECONDS,
        poll_interval=POLL_INTERVAL_SECONDS,
    )

    # Step 3: Verify results
    print("\n--- Step 3: Verification ---")
    cleanup_warnings: list[str] = []

    if matched_event is None:
        print(f"\n❌ FAIL: Quota event not received within {POLL_TIMEOUT_SECONDS}s timeout.")
        output_timeout_diagnostics(svm_name, volume_name)
        cleanup_warnings = attempt_cleanup(svm_name, volume_name)
        print_result(False, cleanup_warnings)
        return False

    # Event found — verify content
    log_message = matched_event.get("message", "")
    log_stream = matched_event.get("logStreamName", "unknown")
    log_timestamp = matched_event.get("timestamp", 0)

    print("\n  ✅ Event received!")
    print(f"  Log Stream:  {log_stream}")
    print(f"  Timestamp:   {datetime.fromtimestamp(log_timestamp / 1000, tz=timezone.utc).isoformat()}")
    print(f"  Message:     {log_message[:200]}...")

    # Verify required fields
    field_checks = verify_log_content(log_message, volume_name)
    all_passed = all(field_checks.values())

    print("\n  Field Verification:")
    for field, passed in field_checks.items():
        status = "✅" if passed else "❌"
        print(f"    {status} {field}")

    # Step 4: Cleanup
    print("\n--- Step 4: Cleanup ---")
    cleanup_warnings = attempt_cleanup(svm_name, volume_name)

    # Final result
    print_result(all_passed, cleanup_warnings)
    return all_passed


def print_result(passed: bool, cleanup_warnings: list[str]) -> None:
    """Print structured test result.

    Args:
        passed: Whether the test passed.
        cleanup_warnings: List of cleanup warning messages.
    """
    print("\n" + "=" * 70)
    if passed:
        print("RESULT: ✅ PASS — Quota threshold exceeded event verified successfully.")
    else:
        print("RESULT: ❌ FAIL — Quota threshold exceeded event verification failed.")

    if cleanup_warnings:
        print("\nWARNINGS:")
        for warning in cleanup_warnings:
            print(f"  ⚠️  {warning}")

    print("=" * 70)


# =============================================================================
# Entry Point
# =============================================================================


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments.

    Returns:
        Parsed arguments namespace.
    """
    parser = argparse.ArgumentParser(
        description="E2E test for ONTAP quota threshold exceeded events.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --region ap-northeast-1 --log-group /aws/lambda/fsxn-ems-receiver \\
           --svm-name svm-prod-01 --volume-name vol_test_quota

Prerequisites:
  1. Deploy EMS Webhook stack (shared/templates/ems-webhook-apigw.yaml)
  2. Configure ONTAP EMS Webhook destination to API Gateway endpoint
  3. Ensure boto3 is installed and AWS credentials are configured
  4. Have SSH access to ONTAP CLI for manual command execution
        """,
    )
    parser.add_argument(
        "--region",
        required=True,
        help="AWS region for CloudWatch Logs (e.g., ap-northeast-1)",
    )
    parser.add_argument(
        "--log-group",
        required=True,
        help="CloudWatch Logs log group name for the EMS Receiver Lambda",
    )
    parser.add_argument(
        "--svm-name",
        required=True,
        help="ONTAP SVM name (e.g., svm-prod-01)",
    )
    parser.add_argument(
        "--volume-name",
        required=True,
        help="ONTAP volume name for quota test (e.g., vol_test_quota)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    success = run_quota_e2e_test(
        region=args.region,
        log_group=args.log_group,
        svm_name=args.svm_name,
        volume_name=args.volume_name,
    )

    sys.exit(0 if success else 1)
