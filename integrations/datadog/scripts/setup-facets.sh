#!/bin/bash
# Setup Datadog Facets for FSx for ONTAP Audit Logs
#
# Datadog Facets cannot be created via API — they must be added through
# the Log Explorer UI. This script:
# 1. Sends sample logs to ensure fields exist in Datadog
# 2. Prints instructions for manual Facet creation
#
# Prerequisites:
#   - Datadog API key in Secrets Manager (fsxn-datadog-api-key)
#   - Python 3.12+ with boto3 and urllib3
#
# Usage:
#   bash integrations/datadog/scripts/setup-facets.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${SCRIPT_DIR}/../../.."

echo "============================================================"
echo "Datadog Facets Setup for FSx for ONTAP Audit Logs"
echo "============================================================"
echo ""

# Step 1: Send sample events to ensure all fields are present
echo "Step 1: Sending sample events to populate field catalog..."
python3 "${PROJECT_ROOT}/shared/scripts/test-xml-e2e.py" --vendor datadog 2>&1 | grep -E "(OK|FAIL|Error)"
echo ""

# Step 2: Print manual facet creation instructions
echo "Step 2: Create Facets manually in Datadog UI"
echo ""
echo "Open Log Explorer: https://app.datadoghq.com/logs?query=source%3Afsxn"
echo "(Replace app.datadoghq.com with your Datadog site, e.g. ap1.datadoghq.com)"
echo ""
echo "For each field below:"
echo "  1. Click on any log entry to open the detail panel"
echo "  2. Find the field in the 'Event Attributes' section"
echo "  3. Hover over the field → Click the gear/cog icon"
echo "  4. Select 'Create facet for @<field_name>'"
echo "  5. Accept defaults and click 'Add'"
echo ""
echo "┌──────────────────┬───────────────────────┬──────────────────────────────────┐"
echo "│ Field            │ Facet Group           │ Description                      │"
echo "├──────────────────┼───────────────────────┼──────────────────────────────────┤"
echo "│ @event_type      │ FSx for ONTAP Audit            │ Windows EventID (4663/4660/etc)  │"
echo "│ @operation_name  │ FSx for ONTAP Audit            │ Human-readable operation         │"
echo "│ @user            │ FSx for ONTAP Audit            │ DOMAIN\\username                  │"
echo "│ @svm             │ FSx for ONTAP Audit            │ Storage Virtual Machine name     │"
echo "│ @path            │ FSx for ONTAP Audit            │ File/folder path accessed        │"
echo "│ @client_ip       │ FSx for ONTAP Audit            │ Source IP address                │"
echo "│ @operation       │ FSx for ONTAP Audit            │ ONTAP operation type             │"
echo "│ @result          │ FSx for ONTAP Audit            │ Audit Success / Audit Failure    │"
echo "└──────────────────┴───────────────────────┴──────────────────────────────────┘"
echo ""
echo "Tip: Use 'FSx for ONTAP Audit' as the Facet Group to keep them organized"
echo "     in the left sidebar."
echo ""
echo "Step 3: Verify facets appear in the left sidebar"
echo ""
echo "After creating all facets, refresh Log Explorer."
echo "The left sidebar should show:"
echo "  FSx for ONTAP Audit"
echo "    ├── event_type     (e.g., 4663, 4660, 4656)"
echo "    ├── operation_name (e.g., Object Access, Object Delete)"
echo "    ├── user           (e.g., CORP\\user-finance-01)"
echo "    ├── svm            (e.g., ProductionSVM)"
echo "    ├── path           (e.g., /share/finance/...)"
echo "    ├── client_ip      (e.g., 198.51.100.50)"
echo "    ├── operation      (e.g., File)"
echo "    └── result         (e.g., Audit Success, Audit Failure)"
echo ""
echo "============================================================"
echo "Done! Facets enable one-click filtering in Log Explorer."
echo "============================================================"
