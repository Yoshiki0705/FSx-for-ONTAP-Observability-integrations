#!/usr/bin/env python3
"""FPolicy External Engine E2E test script.

Verifies the FPolicy External Engine → ECS Fargate → SQS → EventBridge path
by checking ECS task health, triggering file operations, and validating
CloudWatch Logs output.

Architecture (Verified Working):
    ONTAP FPolicy → TCP:9898 → ECS Fargate (FPolicy Server) → SQS → EventBridge

Prerequisites:
    - FPolicy stack deployed (shared/templates/fpolicy-apigw.yaml)
    - FSx for ONTAP SVM with FPolicy configured (port 9898, async mode)
    - AWS credentials with CloudWatch Logs, ECS, and SQS read access

Usage:
    python e2e_test_fpolicy.py \
        --region ap-northeast-1 \
        --ecs-log-group /ecs/fsxn-fpolicy-server \
        --cluster-name fsxn-fpolicy \
        --service-name fsxn-fpolicy-server \
        --svm-name FPolicySMB
"""

import argparse
import re
import sys
import time
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError

# =============================================================================
# Constants
# =============================================================================

CONNECTION_TIMEOUT_SECONDS = 60
LOG_POLL_TIMEOUT_SECONDS = 30
LOG_POLL_INTERVAL_SECONDS = 5

# ONTAP's default keep_alive_interval is PT2M. The measured interval on
# 9.18.1P3D1 was 120 s (4,694 KeepAlive lines, widest gap 120.4 s):
# docs/en/verification-results-fpolicy-s3ap-and-session.md
DEFAULT_KEEPALIVE_INTERVAL_SECONDS = 120

# =============================================================================
# ONTAP CLI Commands (documented for manual execution)
# =============================================================================

ONTAP_CLI_COMMANDS = """
# =============================================================================
# FPolicy Configuration Commands (execute via ONTAP CLI / SSH)
# Verified working configuration — port 9898, asynchronous, no TLS
# =============================================================================

# Step 1: Create FPolicy External Engine
# Replace <FARGATE_TASK_IP> with the ECS Fargate task private IP
# Port 9898, asynchronous mode, no SSL (ONTAP connects directly to Fargate task)
vserver fpolicy policy external-engine create -vserver {svm_name} \\
    -engine-name fpolicy_lambda_engine \\
    -primary-servers {fargate_task_ip} \\
    -port 9898 \\
    -extern-engine-type asynchronous

# Step 2: Create FPolicy Event (monitor file create operations)
vserver fpolicy policy event create -vserver {svm_name} \\
    -event-name fpolicy_file_create_event \\
    -protocol cifs \\
    -file-operations create,write,rename,delete

# Step 3: Create FPolicy Policy
vserver fpolicy policy create -vserver {svm_name} \\
    -policy-name fpolicy_lambda_policy \\
    -events fpolicy_file_create_event \\
    -engine fpolicy_lambda_engine \\
    -is-mandatory false

# Step 4: Enable FPolicy Policy
vserver fpolicy enable -vserver {svm_name} \\
    -policy-name fpolicy_lambda_policy \\
    -sequence-number 1

# Step 5: Verify External Engine connection status
vserver fpolicy show-engine -vserver {svm_name} -engine-name fpolicy_lambda_engine

# Step 6: Verify FPolicy is enabled
vserver fpolicy show -vserver {svm_name} -policy-name fpolicy_lambda_policy
"""

# =============================================================================
# Test Result Helpers
# =============================================================================


def print_result(step: str, status: str, details: str = "") -> None:
    """Print structured test result.

    Args:
        step: Test step name.
        status: PASS or FAIL.
        details: Additional details.
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    print(f"[{timestamp}] [{status}] {step}")
    if details:
        for line in details.strip().split("\n"):
            print(f"    {line}")


def print_section(title: str) -> None:
    """Print section header."""
    print(f"\n{'=' * 72}")
    print(f"  {title}")
    print(f"{'=' * 72}\n")


# =============================================================================
# AWS API Helpers
# =============================================================================


def get_cloudwatch_logs_client(region: str) -> Any:
    """Create CloudWatch Logs client."""
    return boto3.client("logs", region_name=region)


def get_ecs_client(region: str) -> Any:
    """Create ECS client."""
    return boto3.client("ecs", region_name=region)


def get_sqs_client(region: str) -> Any:
    """Create SQS client."""
    return boto3.client("sqs", region_name=region)


def poll_cloudwatch_logs(
    client: Any,
    log_group: str,
    start_time_ms: int,
    timeout_seconds: int,
    filter_pattern: str,
) -> list[dict[str, Any]]:
    """Poll CloudWatch Logs for matching events within timeout.

    Args:
        client: CloudWatch Logs boto3 client.
        log_group: Log group name.
        start_time_ms: Start time in milliseconds since epoch.
        timeout_seconds: Maximum time to wait for events.
        filter_pattern: CloudWatch Logs filter pattern.

    Returns:
        List of matching log events, empty if timeout reached.
    """
    deadline = time.time() + timeout_seconds
    events: list[dict[str, Any]] = []

    while time.time() < deadline:
        try:
            response = client.filter_log_events(
                logGroupName=log_group,
                startTime=start_time_ms,
                filterPattern=filter_pattern,
                limit=10,
            )
            events = response.get("events", [])
            if events:
                return events
        except ClientError as e:
            print_result(
                "CloudWatch Logs poll",
                "FAIL",
                f"API error: {e.response['Error']['Message']}",
            )
            return []

        time.sleep(LOG_POLL_INTERVAL_SECONDS)

    return events


# =============================================================================
# Diagnostic Functions
# =============================================================================


def diagnose_ecs_task_health(
    ecs_client: Any, cluster_name: str, service_name: str
) -> str:
    """Check ECS Fargate task health status.

    Args:
        ecs_client: ECS boto3 client.
        cluster_name: ECS cluster name.
        service_name: ECS service name.

    Returns:
        Formatted health check results including task IP.
    """
    try:
        # Describe service to get task ARNs
        service_response = ecs_client.describe_services(
            cluster=cluster_name, services=[service_name]
        )
        services = service_response.get("services", [])
        if not services:
            return f"Service not found: {service_name} in cluster {cluster_name}"

        service = services[0]
        running_count = service.get("runningCount", 0)
        desired_count = service.get("desiredCount", 0)

        lines = [
            "ECS Service Health:",
            f"  Service: {service_name}",
            f"  Cluster: {cluster_name}",
            f"  Running/Desired: {running_count}/{desired_count}",
            f"  Status: {service.get('status', 'UNKNOWN')}",
        ]

        # List tasks
        tasks_response = ecs_client.list_tasks(
            cluster=cluster_name, serviceName=service_name
        )
        task_arns = tasks_response.get("taskArns", [])

        if task_arns:
            tasks_detail = ecs_client.describe_tasks(
                cluster=cluster_name, tasks=task_arns
            )
            for task in tasks_detail.get("tasks", []):
                task_arn = task.get("taskArn", "")
                last_status = task.get("lastStatus", "UNKNOWN")
                health_status = task.get("healthStatus", "UNKNOWN")

                # Get task IP from ENI attachment
                task_ip = "N/A"
                for attachment in task.get("attachments", []):
                    if attachment.get("type") == "ElasticNetworkInterface":
                        for detail in attachment.get("details", []):
                            if detail.get("name") == "privateIPv4Address":
                                task_ip = detail["value"]

                lines.append(
                    f"  Task: {task_arn.split('/')[-1]} | "
                    f"Status: {last_status} | Health: {health_status} | "
                    f"IP: {task_ip}"
                )
        else:
            lines.append("  No running tasks found")

        return "\n".join(lines)
    except ClientError as e:
        return f"Failed to describe ECS service: {e.response['Error']['Message']}"


def keepalive_lookback_seconds(keepalive_interval_seconds: int) -> int:
    """Derive the log query window from the engine's keep_alive_interval.

    The window has to be wider than the interval, or a healthy pipeline reports
    as unhealthy. The previous default was a fixed 30 s, chosen when the
    interval was believed to be about 6 seconds. Against the measured 120 s
    interval that window contains a KeepAlive on roughly one run in four, so
    three runs in four reported a working pipeline as "ONTAP is NOT connected"
    and sent the reader to investigate the engine IP and the security group.

    The factor is above 2 deliberately. At exactly 2 an execution whose window
    lands between two KeepAlives can still contain none of them; 2.5 leaves
    room for the jitter between ONTAP's send and the log ingestion timestamp.

    Args:
        keepalive_interval_seconds: The engine's ``keep_alive_interval``, in
            seconds. ONTAP's default is ``PT2M``.

    Returns:
        Lookback window in seconds.
    """
    return max(int(2.5 * keepalive_interval_seconds), 60)


def _observed_keepalive_intervals(messages: list[str]) -> list[float]:
    """Pull the ``since_prev=<n>s`` values the FPolicy server logs.

    Reading the interval out of the log rather than off a documented constant
    is the point: the constant is what went stale. See ``_log_keepalive`` in
    ``shared/fpolicy-server/fpolicy_server.py``.

    Args:
        messages: Raw log message strings.

    Returns:
        Observed intervals in seconds, in the order they appear.
    """
    intervals = []
    for message in messages:
        match = re.search(r"since_prev=([0-9]+(?:\.[0-9]+)?)s", message)
        if match:
            intervals.append(float(match.group(1)))
    return intervals


def diagnose_keepalive_messages(
    logs_client: Any,
    log_group: str,
    keepalive_interval_seconds: int = DEFAULT_KEEPALIVE_INTERVAL_SECONDS,
    lookback_seconds: int | None = None,
) -> tuple[bool, str]:
    """Check for ONTAP KeepAlive messages in ECS logs.

    Args:
        logs_client: CloudWatch Logs boto3 client.
        log_group: ECS task log group name.
        keepalive_interval_seconds: The engine's configured
            ``keep_alive_interval``, used to derive the query window.
        lookback_seconds: Explicit window, overriding the derived one. Present
            so the window can be driven directly in tests.

    Returns:
        ``(found, report)``. The caller branches on ``found`` rather than
        matching on the text, so rewording the report cannot change the
        verdict.
    """
    if lookback_seconds is None:
        lookback_seconds = keepalive_lookback_seconds(keepalive_interval_seconds)
    start_time_ms = int((time.time() - lookback_seconds) * 1000)
    try:
        response = logs_client.filter_log_events(
            logGroupName=log_group,
            startTime=start_time_ms,
            filterPattern="KeepAlive",
            limit=5,
        )
        events = response.get("events", [])
        if events:
            messages = [event.get("message", "").strip() for event in events]
            lines = [
                f"KeepAlive messages found ({len(events)} in last {lookback_seconds}s):"
            ]
            for message in messages[-3:]:
                lines.append(f"  {message}")
            intervals = _observed_keepalive_intervals(messages)
            if intervals:
                rendered = ", ".join(f"{value:.1f}s" for value in intervals)
                lines.append(
                    f"  Observed interval: {rendered} | "
                    f"configured keep_alive_interval: {keepalive_interval_seconds}s"
                )
            else:
                lines.append(
                    "  Observed interval: not reported by the server log "
                    "(no since_prev= field; the first KeepAlive after a "
                    "handshake has no predecessor)"
                )
            return True, "\n".join(lines)
        return False, (
            f"No KeepAlive messages were observed in the last {lookback_seconds}s.\n"
            "  This is not by itself evidence that ONTAP is disconnected.\n"
            "  Check, in this order:\n"
            "    1. The engine's keep_alive_interval. If it exceeds "
            f"{keepalive_interval_seconds}s, pass the real value via "
            "--keepalive-interval; the window is derived from it.\n"
            "    2. Whether the Fargate task IP is registered in the ONTAP "
            "external engine, and whether TCP 9898 is open.\n"
            "  Note: STATUS_REQ (status_request_interval, default PT10S) is a "
            "different message and is logged at DEBUG, so it neither appears "
            "here nor stands in for a KeepAlive."
        )
    except ClientError as e:
        return False, f"Failed to query logs: {e.response['Error']['Message']}"


# =============================================================================
# Test Steps
# =============================================================================


def step_print_ontap_commands(svm_name: str, fargate_task_ip: str) -> bool:
    """Print ONTAP CLI commands for FPolicy configuration.

    Args:
        svm_name: SVM name.
        fargate_task_ip: ECS Fargate task private IP.

    Returns:
        Always True (informational step).
    """
    print_section("Step 1: ONTAP CLI Commands for FPolicy Configuration")
    commands = ONTAP_CLI_COMMANDS.format(
        svm_name=svm_name, fargate_task_ip=fargate_task_ip
    )
    print(commands)
    print_result(
        "ONTAP CLI commands documented",
        "PASS",
        "Execute the above commands via ONTAP CLI (SSH) before proceeding.\n"
        "Note: Use 'vserver fpolicy policy external-engine create' (full command).",
    )
    return True


def step_verify_ecs_health(
    ecs_client: Any,
    logs_client: Any,
    cluster_name: str,
    service_name: str,
    ecs_log_group: str,
    keepalive_interval_seconds: int = DEFAULT_KEEPALIVE_INTERVAL_SECONDS,
) -> bool:
    """Verify ECS Fargate task is running and receiving KeepAlive.

    Args:
        ecs_client: ECS boto3 client.
        logs_client: CloudWatch Logs boto3 client.
        cluster_name: ECS cluster name.
        service_name: ECS service name.
        ecs_log_group: ECS task CloudWatch log group.
        keepalive_interval_seconds: The engine's ``keep_alive_interval``.

    Returns:
        True if task is healthy and KeepAlive messages are present.
    """
    print_section("Step 2: Verify ECS Fargate Task Health")

    # Check ECS task status
    health_info = diagnose_ecs_task_health(ecs_client, cluster_name, service_name)
    print(health_info)
    print()

    # Check for KeepAlive messages (indicates ONTAP is connected)
    print("--- ONTAP KeepAlive Check ---")
    keepalive_found, keepalive_info = diagnose_keepalive_messages(
        logs_client,
        ecs_log_group,
        keepalive_interval_seconds=keepalive_interval_seconds,
    )
    print(keepalive_info)
    print()

    # Determine pass/fail
    if "No running tasks" in health_info:
        print_result(
            "ECS task health",
            "FAIL",
            "No running ECS tasks. Deploy the FPolicy stack first.",
        )
        return False

    if not keepalive_found:
        print_result(
            "ECS task health",
            "FAIL",
            "Task is running, but no KeepAlive was observed in the query window.\n"
            "This step cannot distinguish a disconnected engine from a window "
            "narrower than the engine's keep_alive_interval. Confirm the "
            "interval before treating this as a connection fault.",
        )
        return False

    print_result(
        "ECS task health",
        "PASS",
        "Fargate task running and receiving ONTAP KeepAlive messages.",
    )
    return True


def step_verify_sqs_event(
    logs_client: Any,
    ecs_log_group: str,
    start_time_ms: int,
) -> bool:
    """Verify FPolicy event appears in ECS logs as SQS send.

    Polls CloudWatch Logs for the pattern '[SQS] Sent:' which indicates
    the FPolicy server received a file operation and sent it to SQS.

    Args:
        logs_client: CloudWatch Logs boto3 client.
        ecs_log_group: ECS task CloudWatch log group.
        start_time_ms: Start time for log search (epoch ms).

    Returns:
        True if matching log entry found within timeout.
    """
    print_section("Step 3: Verify FPolicy Event in ECS Logs")
    print(f"Log group: {ecs_log_group}")
    print(f"Timeout: {LOG_POLL_TIMEOUT_SECONDS} seconds")
    print("Filter: '[SQS] Sent:'")
    print()

    events = poll_cloudwatch_logs(
        client=logs_client,
        log_group=ecs_log_group,
        start_time_ms=start_time_ms,
        timeout_seconds=LOG_POLL_TIMEOUT_SECONDS,
        filter_pattern="[SQS] Sent:",
    )

    if not events:
        print_result(
            "SQS event verification",
            "FAIL",
            f"No '[SQS] Sent:' message found in ECS logs within {LOG_POLL_TIMEOUT_SECONDS}s.\n"
            "Ensure a file creation operation was triggered on the monitored share.\n"
            "Expected pattern: '[SQS] Sent: <filename> (create)'",
        )
        return False

    # Show the matching log entries
    for event in events[:3]:
        message = event.get("message", "").strip()
        print(f"  Found: {message}")

    print()
    print_result(
        "SQS event verification",
        "PASS",
        "FPolicy server received file operation and sent to SQS.",
    )
    return True


def step_diagnose_connection_failure(
    ecs_client: Any,
    logs_client: Any,
    cluster_name: str,
    service_name: str,
    ecs_log_group: str,
) -> None:
    """Output diagnostic information on connection failure.

    Args:
        ecs_client: ECS boto3 client.
        logs_client: CloudWatch Logs boto3 client.
        cluster_name: ECS cluster name.
        service_name: ECS service name.
        ecs_log_group: ECS task log group.
    """
    print_section("Diagnostics: Connection Failure")

    # ECS task health
    print("--- ECS Task Health ---")
    health_info = diagnose_ecs_task_health(ecs_client, cluster_name, service_name)
    print(health_info)
    print()

    # KeepAlive check
    print("--- ONTAP KeepAlive Messages (last 60s) ---")
    keepalive_info = diagnose_keepalive_messages(logs_client, ecs_log_group, 60)
    print(keepalive_info)
    print()

    # Recent error logs
    print("--- Recent Error Logs ---")
    start_time_ms = int((time.time() - 300) * 1000)
    try:
        response = logs_client.filter_log_events(
            logGroupName=ecs_log_group,
            startTime=start_time_ms,
            filterPattern="ERROR",
            limit=5,
        )
        errors = response.get("events", [])
        if errors:
            for event in errors:
                print(f"  {event.get('message', '').strip()}")
        else:
            print("  No ERROR messages in last 5 minutes")
    except ClientError as e:
        print(f"  Failed to query error logs: {e.response['Error']['Message']}")
    print()

    print("--- Troubleshooting Checklist ---")
    print("  1. Is the Fargate task IP registered in ONTAP external engine?")
    print("     Check: vserver fpolicy show-engine -vserver <svm>")
    print("  2. Is Security Group <fpolicy-server-sg> allowing TCP 9898 inbound?")
    print("  3. Is the FSx for ONTAP SVM SG (<fsxn-svm-sg>) in the same VPC?")
    print("  4. Does the ECS task role have sqs:SendMessage permission?")
    print("  5. Has the Fargate task restarted? (IP changes on restart)")
    print("     The IP Auto-Updater Lambda should handle this automatically.")
    print()

    print_result(
        "Diagnostics output",
        "FAIL",
        "FPolicy connection failed. Review diagnostics above.",
    )


def step_cleanup(svm_name: str) -> bool:
    """Document and verify cleanup steps.

    Args:
        svm_name: SVM name.

    Returns:
        True if cleanup instructions are provided.
    """
    print_section("Step 4: Cleanup")
    print("Execute the following commands on ONTAP CLI to clean up:")
    print()
    print("    # Disable FPolicy policy")
    print(f"    vserver fpolicy disable -vserver {svm_name} \\")
    print("        -policy-name fpolicy_lambda_policy")
    print()
    print("    # Delete test files (adjust path as needed)")
    print("    # Example: delete test file created during verification")
    print()
    print("    # Verify policy is disabled")
    print(f"    vserver fpolicy show -vserver {svm_name} \\")
    print("        -policy-name fpolicy_lambda_policy")
    print()
    print("Expected: Policy status shows 'disabled', test files do not exist.")
    print()
    print_result(
        "Cleanup documented",
        "PASS",
        "Execute cleanup commands and verify results manually.",
    )
    return True


# =============================================================================
# Main Test Orchestration
# =============================================================================


def run_fpolicy_e2e_test(args: argparse.Namespace) -> int:
    """Run the FPolicy E2E test sequence.

    Args:
        args: Parsed CLI arguments.

    Returns:
        Exit code: 0 for all pass, 1 for any failure.
    """
    print_section("FPolicy External Engine E2E Test")
    print(f"Region:       {args.region}")
    print(f"ECS Log Group:{args.ecs_log_group}")
    print(f"Cluster:      {args.cluster_name}")
    print(f"Service:      {args.service_name}")
    print(f"SVM Name:     {args.svm_name}")
    print(f"Start Time:   {datetime.now(timezone.utc).isoformat()}")
    print()
    print("Architecture: ONTAP → TCP:9898 → ECS Fargate → SQS → EventBridge")

    # Initialize AWS clients
    logs_client = get_cloudwatch_logs_client(args.region)
    ecs_client = get_ecs_client(args.region)

    results: list[bool] = []

    # Step 1: Print ONTAP CLI commands
    # Get Fargate task IP for documentation
    fargate_ip = "<fargate-task-ip>"
    try:
        tasks_response = ecs_client.list_tasks(
            cluster=args.cluster_name, serviceName=args.service_name
        )
        task_arns = tasks_response.get("taskArns", [])
        if task_arns:
            tasks_detail = ecs_client.describe_tasks(
                cluster=args.cluster_name, tasks=task_arns[:1]
            )
            for task in tasks_detail.get("tasks", []):
                for attachment in task.get("attachments", []):
                    if attachment.get("type") == "ElasticNetworkInterface":
                        for detail in attachment.get("details", []):
                            if detail.get("name") == "privateIPv4Address":
                                fargate_ip = detail["value"]
    except ClientError:
        pass

    results.append(step_print_ontap_commands(args.svm_name, fargate_ip))

    # Step 2: Verify ECS task health and ONTAP connection
    ecs_health = step_verify_ecs_health(
        ecs_client=ecs_client,
        logs_client=logs_client,
        cluster_name=args.cluster_name,
        service_name=args.service_name,
        ecs_log_group=args.ecs_log_group,
        keepalive_interval_seconds=args.keepalive_interval,
    )
    results.append(ecs_health)

    # Step 3: Verify SQS event after file operation trigger
    start_time_ms = int(time.time() * 1000)
    print_section("Trigger File Operation")
    print("Trigger a file creation operation on the monitored CIFS/SMB share.")
    print("Example (from a Windows client or smbclient):")
    print()
    print("    # Using smbclient:")
    print("    smbclient //{server}/{share} -U {user} -c 'put testfile.txt'")
    print()
    print("    # Or from Windows Explorer:")
    print("    # Create a new file in the monitored share")
    print()
    print("Waiting for '[SQS] Sent:' message in ECS logs...")
    print()

    sqs_result = step_verify_sqs_event(
        logs_client, args.ecs_log_group, start_time_ms
    )
    results.append(sqs_result)

    # If verification failed, run diagnostics
    if not ecs_health or not sqs_result:
        step_diagnose_connection_failure(
            ecs_client=ecs_client,
            logs_client=logs_client,
            cluster_name=args.cluster_name,
            service_name=args.service_name,
            ecs_log_group=args.ecs_log_group,
        )

    # Step 4: Cleanup
    results.append(step_cleanup(args.svm_name))

    # Summary
    print_section("Test Summary")
    total = len(results)
    passed = sum(results)
    failed = total - passed

    print(f"Total steps: {total}")
    print(f"Passed:      {passed}")
    print(f"Failed:      {failed}")
    print()

    if all(results):
        print_result("FPolicy E2E Test", "PASS", "All steps completed successfully.")
        return 0
    else:
        print_result(
            "FPolicy E2E Test",
            "FAIL",
            f"{failed} step(s) failed. Review output above for details.",
        )
        return 1


# =============================================================================
# CLI Argument Parsing
# =============================================================================


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments.

    Returns:
        Parsed arguments namespace.
    """
    parser = argparse.ArgumentParser(
        description="FPolicy External Engine E2E Test Script (ECS Fargate + SQS)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Architecture:
    ONTAP FPolicy → TCP:9898 → ECS Fargate → SQS → EventBridge → Vendor Lambda

Examples:
    # Run with all required arguments
    python e2e_test_fpolicy.py \\
        --region ap-northeast-1 \\
        --ecs-log-group /ecs/fsxn-fpolicy-server \\
        --cluster-name fsxn-fpolicy \\
        --service-name fsxn-fpolicy-server \\
        --svm-name FPolicySMB

    # With custom log group
    python e2e_test_fpolicy.py \\
        --region ap-northeast-1 \\
        --ecs-log-group /ecs/my-stack-fpolicy-server \\
        --cluster-name my-stack-fpolicy \\
        --service-name my-stack-fpolicy-server \\
        --svm-name FPolicySMB
        """,
    )

    parser.add_argument(
        "--region",
        required=True,
        help="AWS region (e.g., ap-northeast-1)",
    )
    parser.add_argument(
        "--ecs-log-group",
        required=True,
        help="CloudWatch Logs log group for ECS FPolicy server task",
    )
    parser.add_argument(
        "--cluster-name",
        required=True,
        help="ECS cluster name running the FPolicy server",
    )
    parser.add_argument(
        "--service-name",
        required=True,
        help="ECS service name for the FPolicy server",
    )
    parser.add_argument(
        "--svm-name",
        required=True,
        help="FSx for ONTAP SVM name (e.g., FPolicySMB)",
    )
    parser.add_argument(
        "--keepalive-interval",
        type=int,
        default=DEFAULT_KEEPALIVE_INTERVAL_SECONDS,
        help=(
            "The engine's keep_alive_interval in seconds (default: "
            f"{DEFAULT_KEEPALIVE_INTERVAL_SECONDS}, matching ONTAP's PT2M). "
            "The KeepAlive log query window is derived from this value, so set "
            "it to the real interval if the engine is configured differently. "
            "Check with: vserver fpolicy policy external-engine show "
            "-fields keep-alive-interval"
        ),
    )

    return parser.parse_args()


# =============================================================================
# Entry Point
# =============================================================================

if __name__ == "__main__":
    args = parse_args()
    exit_code = run_fpolicy_e2e_test(args)
    sys.exit(exit_code)
