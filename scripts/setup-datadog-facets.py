#!/usr/bin/env python3
"""Create Datadog Facets for FSx for ONTAP audit log attributes.

Creates the 6 required Facets in Datadog for structured log searching.
Uses the Datadog Logs API to create facets programmatically.

Note: Datadog does not have a public API for creating facets directly.
This script uses an alternative approach: it sends a log with all required
attributes, then provides instructions for creating facets from the UI.

For automated facet creation, use the Datadog Terraform provider:
  resource "datadog_logs_custom_pipeline" { ... }

Usage:
    python3 scripts/setup-datadog-facets.py

Environment variables:
    DD_API_KEY: Datadog API key
    DD_SITE: Datadog site (default: ap1.datadoghq.com)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Ensure project root is on sys.path
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

try:
    import urllib3
except ImportError:
    print("Error: urllib3 is required. Install with: pip3 install urllib3")
    sys.exit(1)


# ─── Configuration ──────────────────────────────────────────────────────────

DD_API_KEY = os.environ.get("DD_API_KEY", "")
DD_SITE = os.environ.get("DD_SITE", "ap1.datadoghq.com")

FACETS = [
    {"path": "@attributes.svm", "name": "SVM", "description": "Storage Virtual Machine name"},
    {"path": "@attributes.user", "name": "User", "description": "User who performed the operation"},
    {"path": "@attributes.operation", "name": "Operation", "description": "File operation type (ReadData, WriteData, Open, etc.)"},
    {"path": "@attributes.client_ip", "name": "Client IP", "description": "Client IP address"},
    {"path": "@attributes.result", "name": "Result", "description": "Operation result (Success/Failure)"},
    {"path": "@attributes.path", "name": "File Path", "description": "File or directory path"},
]

INTAKE_URL = f"https://http-intake.logs.{DD_SITE}/api/v2/logs"


def send_sample_log() -> bool:
    """Send a sample log with all attributes to ensure they exist in Datadog.

    This is a prerequisite for creating facets — Datadog needs to see the
    attributes in at least one log before facets can be created.
    """
    http = urllib3.PoolManager()

    sample_log = [
        {
            "ddsource": "fsxn",
            "ddtags": "source:fsxn,service:ontap-audit,env:setup",
            "hostname": "svm-setup",
            "service": "ontap-audit",
            "message": "Facet setup: sample log with all required attributes",
            "attributes": {
                "svm": "svm-prod-01",
                "user": "admin@corp.local",
                "operation": "ReadData",
                "client_ip": "198.51.100.50",
                "result": "Success",
                "path": "/vol/data/setup-test.txt",
                "event_type": "4663",
            },
        }
    ]

    response = http.request(
        "POST",
        INTAKE_URL,
        body=json.dumps(sample_log).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "DD-API-KEY": DD_API_KEY,
        },
        timeout=30.0,
    )

    return response.status == 202


def main() -> int:
    """Main entry point."""
    if not DD_API_KEY:
        print("Error: DD_API_KEY environment variable is required.")
        print("  export DD_API_KEY=your-api-key")
        return 1

    print(f"Datadog Site: {DD_SITE}")
    print(f"Intake URL: {INTAKE_URL}")
    print()

    # Step 1: Send sample log
    print("Step 1: Sending sample log with all attributes...")
    if send_sample_log():
        print("  ✔ Sample log sent successfully")
    else:
        print("  ✘ Failed to send sample log. Check your API key.")
        return 1

    print()

    # Step 2: Print facet creation instructions
    print("Step 2: Create Facets in Datadog UI")
    print("=" * 60)
    print()
    print("Navigate to: Logs → Search → click on a log entry →")
    print("expand attributes → click the gear icon next to each attribute →")
    print("'Create facet for @attributes.xxx'")
    print()
    print("Required Facets:")
    print("-" * 60)
    for facet in FACETS:
        print(f"  • {facet['name']}")
        print(f"    Path: {facet['path']}")
        print(f"    Description: {facet['description']}")
        print()

    print("=" * 60)
    print()
    print("Alternative: Use Datadog Terraform provider for automation:")
    print("  https://registry.terraform.io/providers/DataDog/datadog/latest/docs")
    print()

    # Step 3: Print Datadog UI URL
    # (a console_domain was computed here and never read; every branch below
    # builds console_url directly)
    if DD_SITE == "datadoghq.eu":
        console_url = "https://app.datadoghq.eu/logs"
    elif DD_SITE == "ddog-gov.com":
        console_url = "https://app.ddog-gov.com/logs"
    else:
        site_prefix = DD_SITE.split(".")[0] if "." in DD_SITE and DD_SITE != "datadoghq.com" else ""
        if site_prefix and site_prefix != "datadoghq":
            console_url = f"https://{site_prefix}.datadoghq.com/logs"
        else:
            console_url = "https://app.datadoghq.com/logs"

    print(f"Datadog Logs UI: {console_url}?query=source%3Afsxn")

    return 0


if __name__ == "__main__":
    sys.exit(main())
