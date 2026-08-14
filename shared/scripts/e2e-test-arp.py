#!/usr/bin/env python3
"""E2E test script for ARP (Autonomous Ransomware Protection) event detection.


This script verifies the end-to-end flow of ARP ransomware detection events
from FSx for ONTAP through API Gateway to the EMS Receiver Lambda function.

Prerequisites:
    - AWS credentials configured with CloudWatch Logs read access
    - FSx for ONTAP with ARP enabled on the target volume
    - EMS Webhook stack deployed (shared/templates/ems-webhook-apigw.yaml)
    - EMS Receiver Lambda function deployed and connected to the API Gateway

ONTAP CLI command to trigger ARP event (run manually via SSH to FSx for ONTAP):
    security anti-ransomware volume attack simulate -vserver <svm> -volume <vol>

Usage:
    python e2e-test-arp.py \
        --region ap-northeast-1 \
        --log-group /aws/lambda/fsxn-ems-receiver \
        --svm-name svm-prod-01 \
        --volume-name vol1 \
        --management-ip 198.51.100.100
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone

import boto3


# Constants
POLL_TIMEOUT_SECONDS = 120
POLL_INTERVAL_SECONDS = 5
EXPECTED_EVENT_NAME = "arw.volume.state"
EXPECTED_SEVERITY = "alert"


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments.

    Returns:
        Parsed arguments namespace.
    """
    parser = argparse.ArgumentParser(
        description="E2E test for ARP ransomware detection event flow.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
ONTAP CLI command to trigger ARP event:
    security anti-ransomware volume attack simulate -vserver <svm> -volume <vol>

Verify management IP beforehand:
    network interface show -vserver <svm_name> -role management
        """,
    )
    parser.add_argument(
        "--region",
        required=True,
        help="AWS region where the Lambda log group exists (e.g., ap-northeast-1)",
    )
    parser.add_argument(
        "--log-group",
        required=True,
        help="CloudWatch Logs log group name for the EMS Receiver Lambda",
    )
    parser.add_argument(
        "--svm-name",
        required=True,
        help="FSx for ONTAP SVM name (e.g., svm-prod-01)",
    )
    parser.add_argument(
        "--volume-name",
        required=True,
        help="FSx for ONTAP volume name where ARP is enabled (e.g., vol1)",
    )
    parser.add_argument(
        "--management-ip",
        required=True,
        help=(
            "FSx for ONTAP management IP address "
            "(from: network interface show -vserver <svm_name> -role management)"
        ),
    )
    return parser.parse_args()


def get_cloudwatch_logs_client(region: str) -> boto3.client:
    """Create a CloudWatch Logs client for the specified region.

    Args:
        region: AWS region name.

    Returns:
        boto3 CloudWatch Logs client.
    """
    return boto3.client("logs", region_name=region)


def poll_cloudwatch_logs(
    client: boto3.client,
    log_group: str,
    start_time_ms: int,
    volume_name: str,
) -> dict | None:
    """Poll CloudWatch Logs for the ARP event within the timeout period.

    Searches for log entries containing the expected ARP event fields:
    event_name=arw.volume.state, severity=alert, and the target volume_name.

    Args:
        client: boto3 CloudWatch Logs client.
        log_group: CloudWatch Logs log group name.
        start_time_ms: Start time in milliseconds since epoch.
        volume_name: Expected volume name in the log entry.

    Returns:
        Matching log event dict if found, None if timeout reached.
    """
    deadline = time.time() + POLL_TIMEOUT_SECONDS
    print(f"\n[POLLING] Searching CloudWatch Logs for ARP event...")
    print(f"  Log group: {log_group}")
    print(f"  Timeout: {POLL_TIMEOUT_SECONDS}s")
    print(f"  Looking for: event_name={EXPECTED_EVENT_NAME}, volume_name={volume_name}")
    print()

    attempt = 0
    while time.time() < deadline:
        attempt += 1
        elapsed = int(time.time() - (start_time_ms / 1000))
        print(f"  Poll attempt {attempt} ({elapsed}s elapsed)...", end="")

        try:
            response = client.filter_log_events(
                logGroupName=log_group,
                startTime=start_time_ms,
                filterPattern=f'"{EXPECTED_EVENT_NAME}"',
                limit=50,
            )
        except client.exceptions.ResourceNotFoundException:
            print(f" ERROR: Log group '{log_group}' not found.")
            return None

        for event in response.get("events", []):
            message = event.get("message", "")
            if (
                EXPECTED_EVENT_NAME in message
                and EXPECTED_SEVERITY in message
                and volume_name in message
            ):
                print(" FOUND!")
                return event

        print(" not found yet.")
        time.sleep(POLL_INTERVAL_SECONDS)

    return None


def verify_log_content(log_message: str, volume_name: str) -> dict:
    """Verify that the log message contains all required ARP event fields.

    Args:
        log_message: The CloudWatch Logs message string.
        volume_name: Expected volume name.

    Returns:
        Dict with verification results for each field.
    """
    checks = {
        "event_name": EXPECTED_EVENT_NAME in log_message,
        "severity": EXPECTED_SEVERITY in log_message,
        "volume_name": volume_name in log_message,
        "state": any(
            state in log_message
            for state in ["enabled", "disabled", "dry-run", "paused"]
        ),
    }
    return checks


def verify_source_ip(
    client: boto3.client,
    log_group: str,
    start_time_ms: int,
    management_ip: str,
) -> bool:
    """Verify that the source IP in API Gateway access logs matches the management IP.

    Searches the API Gateway access log for the request that delivered the ARP event
    and checks that the source IP matches the FSx for ONTAP management IP.

    Args:
        client: boto3 CloudWatch Logs client.
        log_group: CloudWatch Logs log group name (API Gateway access log group).
        start_time_ms: Start time in milliseconds since epoch.
        management_ip: Expected FSx for ONTAP management IP address.

    Returns:
        True if source IP matches management IP, False otherwise.
    """
    print(f"\n[SOURCE IP] Verifying source IP matches management IP: {management_ip}")

    try:
        response = client.filter_log_events(
            logGroupName=log_group,
            startTime=start_time_ms,
            filterPattern=f'"{management_ip}"',
            limit=10,
        )
    except client.exceptions.ResourceNotFoundException:
        print(f"  WARNING: Log group '{log_group}' not found for source IP verification.")
        print("  Skipping source IP verification (API Gateway access log may use a different log group).")
        return False

    events = response.get("events", [])
    if events:
        print(f"  PASS: Found {len(events)} log entries with source IP {management_ip}")
        return True

    print(f"  FAIL: No log entries found with source IP {management_ip}")
    return False


def output_diagnostics(
    client: boto3.client,
    log_group: str,
    start_time_ms: int,
    svm_name: str,
    volume_name: str,
) -> None:
    """Output diagnostic information on timeout.

    Prints API Gateway access log entries and suggests ONTAP EMS log commands
    for manual troubleshooting.

    Args:
        client: boto3 CloudWatch Logs client.
        log_group: CloudWatch Logs log group name.
        start_time_ms: Start time in milliseconds since epoch.
        svm_name: FSx for ONTAP SVM name.
        volume_name: FSx for ONTAP volume name.
    """
    print("\n" + "=" * 70)
    print("DIAGNOSTICS — ARP Event Not Received Within Timeout")
    print("=" * 70)

    # Attempt to fetch recent logs from the Lambda log group
    print("\n[1] Recent Lambda CloudWatch Logs entries:")
    print("-" * 50)
    try:
        response = client.filter_log_events(
            logGroupName=log_group,
            startTime=start_time_ms,
            limit=20,
        )
        events = response.get("events", [])
        if events:
            for event in events[-10:]:
                ts = datetime.fromtimestamp(
                    event["timestamp"] / 1000, tz=timezone.utc
                ).isoformat()
                print(f"  [{ts}] {event['message'].strip()}")
        else:
            print("  No log entries found in the specified time range.")
    except Exception as e:
        print(f"  ERROR fetching logs: {e}")

    # ONTAP EMS log diagnostic commands
    print("\n[2] ONTAP EMS Log — Run these commands via SSH to FSx for ONTAP CLI:")
    print("-" * 50)
    print(f"  event log show -vserver {svm_name} -event arw.volume.state*")
    print(f"  event log show -vserver {svm_name} -time >30m")
    print()

    # API Gateway access log suggestion
    print("[3] API Gateway Access Log — Check for incoming requests:")
    print("-" * 50)
    print("  Look for the API Gateway access log group (typically named")
    print("  /aws/apigateway/<api-id>/access-log or similar).")
    print("  Check if any POST /ems requests were received in the time window.")
    print()

    # Additional troubleshooting
    print("[4] Additional Troubleshooting Steps:")
    print("-" * 50)
    print(f"  1. Verify EMS webhook destination is configured:")
    print(f"     event notification destination show -vserver {svm_name}")
    print(f"  2. Verify event notification is active:")
    print(f"     event notification show -vserver {svm_name}")
    print(f"  3. Verify ARP is enabled on the volume:")
    print(f"     security anti-ransomware volume show -vserver {svm_name} -volume {volume_name}")
    print(f"  4. Re-run the attack simulation:")
    print(f"     security anti-ransomware volume attack simulate -vserver {svm_name} -volume {volume_name}")
    print()


def print_result(passed: bool, details: dict) -> None:
    """Print structured test result output.

    Args:
        passed: Whether the test passed overall.
        details: Dict containing test details and field verification results.
    """
    print("\n" + "=" * 70)
    status = "PASS" if passed else "FAIL"
    print(f"TEST RESULT: {status}")
    print("=" * 70)
    print(f"  Test:        ARP Ransomware Detection E2E")
    print(f"  Event:       {EXPECTED_EVENT_NAME}")
    print(f"  Severity:    {EXPECTED_SEVERITY}")
    print(f"  Timestamp:   {datetime.now(timezone.utc).isoformat()}")
    print()

    if "field_checks" in details:
        print("  Field Verification:")
        for field, result in details["field_checks"].items():
            mark = "✓" if result else "✗"
            print(f"    [{mark}] {field}")
        print()

    if "source_ip_match" in details:
        mark = "✓" if details["source_ip_match"] else "✗"
        print(f"  [{mark}] Source IP matches management IP")
        print()

    if "log_excerpt" in details:
        print("  Log Excerpt:")
        print(f"    {details['log_excerpt'][:200]}")
        print()

    if "error" in details:
        print(f"  Error: {details['error']}")
        print()


def main() -> None:
    """Main entry point for the ARP E2E test script."""
    args = parse_args()

    print("=" * 70)
    print("ARP Ransomware Detection — E2E Test")
    print("=" * 70)
    print(f"  Region:        {args.region}")
    print(f"  Log Group:     {args.log_group}")
    print(f"  SVM:           {args.svm_name}")
    print(f"  Volume:        {args.volume_name}")
    print(f"  Management IP: {args.management_ip}")
    print()
    print("ONTAP CLI command to trigger ARP event (run manually):")
    print(f"  security anti-ransomware volume attack simulate "
          f"-vserver {args.svm_name} -volume {args.volume_name}")
    print()
    print("Verify management IP with:")
    print(f"  network interface show -vserver {args.svm_name} -role management")
    print()

    # Record start time for log filtering
    start_time_ms = int(time.time() * 1000)

    # Create CloudWatch Logs client
    client = get_cloudwatch_logs_client(args.region)

    # Poll CloudWatch Logs for the ARP event
    log_event = poll_cloudwatch_logs(
        client=client,
        log_group=args.log_group,
        start_time_ms=start_time_ms,
        volume_name=args.volume_name,
    )

    if log_event is None:
        # Timeout — output diagnostics
        output_diagnostics(
            client=client,
            log_group=args.log_group,
            start_time_ms=start_time_ms,
            svm_name=args.svm_name,
            volume_name=args.volume_name,
        )
        print_result(
            passed=False,
            details={"error": f"Timeout ({POLL_TIMEOUT_SECONDS}s): ARP event not found in CloudWatch Logs"},
        )
        sys.exit(1)

    # Verify log content
    log_message = log_event.get("message", "")
    field_checks = verify_log_content(log_message, args.volume_name)

    # Verify source IP
    source_ip_match = verify_source_ip(
        client=client,
        log_group=args.log_group,
        start_time_ms=start_time_ms,
        management_ip=args.management_ip,
    )

    # Determine overall result
    all_fields_pass = all(field_checks.values())
    overall_pass = all_fields_pass and source_ip_match

    # Print result
    print_result(
        passed=overall_pass,
        details={
            "field_checks": field_checks,
            "source_ip_match": source_ip_match,
            "log_excerpt": log_message,
        },
    )

    sys.exit(0 if overall_pass else 1)


if __name__ == "__main__":
    main()
