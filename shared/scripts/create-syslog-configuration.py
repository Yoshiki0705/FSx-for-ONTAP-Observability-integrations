#!/usr/bin/env python3
"""Create CloudWatch Logs Syslog Configuration.

Maps a syslog VPC endpoint to a CloudWatch Logs log group.
Required because PutSyslogConfiguration is not available in AWS CLI
or boto3 as of June 2026. This script uses raw SigV4 HTTP signing.

Usage:
    python3 create-syslog-configuration.py \
        --vpce-id vpce-0123456789abcdef0 \
        --log-group-arn arn:aws:logs:ap-northeast-1:123456789012:log-group:/syslog/fsxn-admin-audit \
        --region ap-northeast-1

Prerequisites:
    - AWS credentials configured (profile, env vars, or instance role)
    - pip install boto3 (for SigV4 signing only; the API call is manual)
"""

import argparse
import json
import sys
import urllib.request

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest


def put_syslog_configuration(
    vpce_id: str, log_group_arn: str, region: str
) -> dict:
    """Call PutSyslogConfiguration via raw HTTP with SigV4."""
    session = boto3.Session(region_name=region)
    credentials = session.get_credentials().get_frozen_credentials()

    url = f"https://logs.{region}.amazonaws.com"
    headers = {
        "Content-Type": "application/x-amz-json-1.1",
        "X-Amz-Target": "Logs_20140328.PutSyslogConfiguration",
    }
    body = json.dumps(
        {
            "vpcEndpointId": vpce_id,
            "logGroupIdentifier": log_group_arn,
            "allowAllSyslogSources": True,
        }
    )

    request = AWSRequest(method="POST", url=url, data=body, headers=headers)
    SigV4Auth(credentials, "logs", region).add_auth(request)

    req = urllib.request.Request(
        url, data=body.encode(), headers=dict(request.headers), method="POST"
    )
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        print(f"✅ Syslog Configuration created successfully (HTTP {resp.status})")
        response_body = resp.read().decode()
        if response_body:
            return json.loads(response_body)
        return {"status": "created"}
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()
        print(f"❌ Error (HTTP {e.code}): {error_body}", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Create CloudWatch Logs Syslog Configuration"
    )
    parser.add_argument(
        "--vpce-id",
        required=True,
        help="VPC Endpoint ID (e.g., vpce-0123456789abcdef0)",
    )
    parser.add_argument(
        "--log-group-arn",
        required=True,
        help="Log Group ARN (e.g., arn:aws:logs:region:account:log-group:name)",
    )
    parser.add_argument(
        "--region",
        default="ap-northeast-1",
        help="AWS Region (default: ap-northeast-1)",
    )
    args = parser.parse_args()

    # Input validation
    if not args.vpce_id.startswith("vpce-"):
        print(
            f"❌ Invalid VPC Endpoint ID: {args.vpce_id} (must start with 'vpce-')",
            file=sys.stderr,
        )
        sys.exit(1)
    if not args.log_group_arn.startswith("arn:aws:logs:"):
        print(
            f"❌ Invalid Log Group ARN: {args.log_group_arn} (must start with 'arn:aws:logs:')",
            file=sys.stderr,
        )
        sys.exit(1)

    print("Creating Syslog Configuration:")
    print(f"  VPC Endpoint: {args.vpce_id}")
    print(f"  Log Group:    {args.log_group_arn}")
    print(f"  Region:       {args.region}")
    print()

    result = put_syslog_configuration(args.vpce_id, args.log_group_arn, args.region)
    print(f"  Result: {json.dumps(result, indent=2)}")
    print()
    print("Next step: Configure ONTAP log-forwarding destination.")
    print("  SSH to FSx management endpoint and run:")
    print(
        "  security audit log-forwarding create "
        "-destination <VPCE_ENI_IP> -port 1514 "
        "-protocol tcp-unencrypted -facility local7"
    )


if __name__ == "__main__":
    main()
