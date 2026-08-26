#!/usr/bin/env python3
"""Report FPolicy session continuity from both sides of the connection.

Answers "did the ONTAP session to the FPolicy server drop, and if so when and
why" over an arbitrary window, by reading two independent records:

  server side  CloudWatch Logs from the FPolicy server task. Gives the exact
               instant a socket closed, whether the close came from the peer or
               from the server's own idle timeout, and how long the session had
               been up. Requires the server build that emits `uptime=` on close
               and `since_prev=` on KeepAlive.
  ONTAP side   the EMS event log, which records fpolicy.server.connect and
               fpolicy.server.disconnect with a reason string. A reason is the
               part the server can never know.

Neither side alone is sufficient: a task restart looks like an ONTAP disconnect
in the server log, and an engine reconfiguration looks like a spontaneous drop
unless the EMS reason is read.

The ONTAP half is optional. Without --ontap-url the report still covers session
durations and keep-alive gaps, and says so rather than implying ONTAP was quiet.

Usage:
    fpolicy-session-report.py --log-group /ecs/<stack> --hours 72
    fpolicy-session-report.py --log-group /ecs/<stack> --since 2026-08-25T16:38:41Z \\
        --ontap-url https://127.0.0.1:8443 --secret-id fsx-ontap-fsxadmin-credentials
"""

from __future__ import annotations

import argparse
import json
import re
import ssl
import sys
import urllib.error
import urllib.request
from base64 import b64encode
from datetime import datetime, timedelta, timezone

import boto3

UPTIME_RE = re.compile(r"uptime=([0-9.]+)s")
SINCE_PREV_RE = re.compile(r"since_prev=([0-9.]+)s")
PEER_RE = re.compile(r"Connection closed by peer: \('([^']+)', (\d+)\)")
TIMEOUT_RE = re.compile(r"Server-side socket timeout after ([0-9.]+)s idle: \('([^']+)', (\d+)\)")
CONNECT_RE = re.compile(r"Connection from \('([^']+)', (\d+)\)")


def parse_when(value: str) -> datetime:
    text = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def fetch_log_events(
    log_group: str, region: str, start: datetime, end: datetime
) -> list[dict]:
    logs = boto3.client("logs", region_name=region)
    paginator = logs.get_paginator("filter_log_events")
    pages = paginator.paginate(
        logGroupName=log_group,
        startTime=int(start.timestamp() * 1000),
        endTime=int(end.timestamp() * 1000),
    )
    events: list[dict] = []
    for page in pages:
        events.extend(page.get("events", []))
    return events


def fetch_ems(
    base_url: str, secret_id: str, region: str, start: datetime, end: datetime
) -> list[dict] | None:
    """Read fpolicy EMS events. Returns None when ONTAP could not be reached."""
    secrets = boto3.client("secretsmanager", region_name=region)
    creds = json.loads(secrets.get_secret_value(SecretId=secret_id)["SecretString"])
    token = b64encode(f"{creds['username']}:{creds['password']}".encode()).decode()

    query = (
        "/api/support/ems/events"
        "?message.name=fpolicy*"
        "&fields=time,message.name,message.severity,log_message,node.name"
        "&max_records=1000&order_by=time+asc"
        f"&time=>={start.strftime('%Y-%m-%dT%H:%M:%SZ')}"
    )
    request = urllib.request.Request(base_url.rstrip("/") + query)
    request.add_header("Authorization", f"Basic {token}")
    request.add_header("Accept", "application/json")

    # The FSx management endpoint presents a self-signed certificate and is
    # normally reached through a localhost port-forward, so chain and hostname
    # verification cannot succeed on that hop.
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    try:
        with urllib.request.urlopen(request, context=ctx, timeout=60) as response:
            payload = json.loads(response.read().decode())
    except (urllib.error.URLError, TimeoutError, ssl.SSLError) as exc:
        print(f"  ONTAP unreachable ({exc}); EMS section omitted", file=sys.stderr)
        return None

    records = payload.get("records", [])
    return [r for r in records if parse_when(r["time"]) <= end]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-group", required=True)
    parser.add_argument("--region", default="ap-northeast-1")
    parser.add_argument("--hours", type=float, help="Window ending now")
    parser.add_argument("--since", help="Window start, ISO 8601 (overrides --hours)")
    parser.add_argument("--until", help="Window end, ISO 8601. Default: now")
    parser.add_argument("--ontap-url", help="e.g. https://127.0.0.1:8443")
    parser.add_argument("--secret-id", default="fsx-ontap-fsxadmin-credentials")
    parser.add_argument(
        "--keepalive-gap-threshold",
        type=float,
        default=0.0,
        help=(
            "Report keep-alive gaps at least this many seconds long. Default 0 "
            "reports the largest gap only, which is the figure that bounds an "
            "undetected stall."
        ),
    )
    args = parser.parse_args()

    end = parse_when(args.until) if args.until else datetime.now(timezone.utc)
    if args.since:
        start = parse_when(args.since)
    elif args.hours:
        start = end - timedelta(hours=args.hours)
    else:
        parser.error("one of --since or --hours is required")

    print("=" * 72)
    print("FPolicy session continuity report")
    print(f"  window     : {start.isoformat()}  ->  {end.isoformat()}")
    print(f"  duration   : {(end - start).total_seconds() / 3600:.2f} h")
    print(f"  log group  : {args.log_group}")
    print("=" * 72)

    events = fetch_log_events(args.log_group, args.region, start, end)
    print(f"\nserver-side log lines in window: {len(events)}")

    connects: list[tuple[datetime, str]] = []
    peer_closes: list[tuple[datetime, str, float]] = []
    timeouts: list[tuple[datetime, str, float]] = []
    keepalive_gaps: list[tuple[datetime, float]] = []
    task_starts: list[datetime] = []

    for event in events:
        when = datetime.fromtimestamp(event["timestamp"] / 1000, tz=timezone.utc)
        message = event["message"]

        if "FPolicy Server started on port" in message:
            task_starts.append(when)
        match = CONNECT_RE.search(message)
        if match:
            connects.append((when, f"{match.group(1)}:{match.group(2)}"))
        match = PEER_RE.search(message)
        if match:
            uptime = UPTIME_RE.search(message)
            peer_closes.append(
                (
                    when,
                    f"{match.group(1)}:{match.group(2)}",
                    float(uptime.group(1)) if uptime else float("nan"),
                )
            )
        match = TIMEOUT_RE.search(message)
        if match:
            uptime = UPTIME_RE.search(message)
            timeouts.append(
                (
                    when,
                    f"{match.group(2)}:{match.group(3)}",
                    float(uptime.group(1)) if uptime else float("nan"),
                )
            )
        match = SINCE_PREV_RE.search(message)
        if match:
            keepalive_gaps.append((when, float(match.group(1))))

    print(f"  server process starts     : {len(task_starts)}")
    for when in task_starts:
        print(f"      {when.isoformat()}  (a restart resets every session below)")
    print(f"  inbound connections       : {len(connects)}")
    print(f"  closed by peer (ONTAP)    : {len(peer_closes)}")
    print(f"  server-side idle timeouts : {len(timeouts)}")
    print(f"  keep-alives with interval : {len(keepalive_gaps)}")

    if peer_closes:
        print("\nsessions ended by the peer:")
        for when, who, uptime in peer_closes:
            print(f"  {when.isoformat()}  {who:24s}  uptime={uptime / 3600:.2f} h")
        intervals = [
            (b[0] - a[0]).total_seconds() / 3600
            for a, b in zip(peer_closes, peer_closes[1:])
        ]
        if intervals:
            print("  intervals between consecutive closes (h): "
                  + ", ".join(f"{i:.2f}" for i in intervals))
            print("  NOTE: periodicity needs at least two intervals to claim; "
                  f"there {'is' if len(intervals) == 1 else 'are'} {len(intervals)}.")
    else:
        print("\nno peer-initiated close in this window.")

    if timeouts:
        print("\nserver-side idle timeouts (NOT an ONTAP disconnect):")
        for when, who, uptime in timeouts:
            print(f"  {when.isoformat()}  {who:24s}  uptime={uptime / 3600:.2f} h")

    if keepalive_gaps:
        widest = max(keepalive_gaps, key=lambda item: item[1])
        print(f"\nkeep-alive interval: max {widest[1]:.1f}s at {widest[0].isoformat()}")
        if args.keepalive_gap_threshold > 0:
            over = [g for g in keepalive_gaps if g[1] >= args.keepalive_gap_threshold]
            print(f"  gaps >= {args.keepalive_gap_threshold:.0f}s: {len(over)}")
            for when, gap in over:
                print(f"      {when.isoformat()}  {gap:.1f}s")
    else:
        print("\nno keep-alive intervals recorded. Either the window contains no "
              "second keep-alive, or the server predates interval logging.")

    if args.ontap_url:
        print("\n" + "-" * 72)
        print("ONTAP side (EMS)")
        print("-" * 72)
        ems = fetch_ems(args.ontap_url, args.secret_id, args.region, start, end)
        if ems is None:
            print("  not collected")
        elif not ems:
            print("  no fpolicy EMS events in window")
        else:
            for record in ems:
                severity = record["message"]["severity"]
                name = record["message"]["name"]
                node = record.get("node", {}).get("name", "?")
                reason = ""
                match = re.search(r"reason: \"([^\"]+)\"", record.get("log_message", ""))
                if match:
                    reason = f'  reason="{match.group(1)}"'
                print(f"  {record['time']}  {severity:8s} {name:28s} {node}{reason}")
    else:
        print("\nONTAP side not collected (--ontap-url not given). A disconnect "
              "reason can only come from EMS, so a close below is unattributed.")

    print("\n" + "=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
