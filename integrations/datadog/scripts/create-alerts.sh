#!/bin/bash
# Create FSx for ONTAP Monitors (Alerts) via Datadog API
#
# Creates three monitors:
#   1. Failed Access Spike (>10 failures in 5 minutes)
#   2. Pipeline Health (Lambda errors detected)
#   3. DLQ Alert (messages appearing in Dead Letter Queue)
#
# Prerequisites:
#   - Datadog API Key and Application Key
#   - Logs flowing to Datadog with source:fsxn
#
# Usage:
#   export DD_API_KEY="your-api-key"
#   export DD_APP_KEY="your-app-key"
#   export DD_SITE="ap1.datadoghq.com"
#   bash integrations/datadog/scripts/create-alerts.sh
#
# Named create-alerts.sh to match the other vendor integrations. Datadog's own
# term for these objects is "monitor", which is what the API creates.
#
# Reference:
#   https://docs.datadoghq.com/api/latest/monitors/

set -euo pipefail

DD_API_KEY="${DD_API_KEY:-}"
DD_APP_KEY="${DD_APP_KEY:-}"
DD_SITE="${DD_SITE:-ap1.datadoghq.com}"

if [ -z "${DD_API_KEY}" ] || [ -z "${DD_APP_KEY}" ]; then
  echo "ERROR: DD_API_KEY and DD_APP_KEY must be set."
  echo "  export DD_API_KEY='...'"
  echo "  export DD_APP_KEY='...'"
  exit 1
fi

DD_API_URL="https://api.${DD_SITE}"

echo "=== Creating FSx for ONTAP Monitors ==="
echo "Datadog Site: ${DD_SITE}"
echo ""

create_monitor() {
  local name="$1"
  local payload="$2"

  RESPONSE=$(curl -s -w "\n%{http_code}" \
    -X POST "${DD_API_URL}/api/v1/monitor" \
    -H "DD-API-KEY: ${DD_API_KEY}" \
    -H "DD-APPLICATION-KEY: ${DD_APP_KEY}" \
    -H "Content-Type: application/json" \
    -d "${payload}")

  HTTP_CODE=$(echo "$RESPONSE" | tail -1)
  BODY=$(echo "$RESPONSE" | sed '$d')

  if [ "$HTTP_CODE" = "200" ]; then
    MONITOR_ID=$(echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('id',''))" 2>/dev/null || echo "")
    echo "  [OK] ${name} (ID: ${MONITOR_ID})"
  else
    echo "  [FAIL] ${name} (HTTP ${HTTP_CODE})"
    echo "         ${BODY}" | head -1
  fi
}

# Monitor 1: Failed Access Spike
echo "Creating monitors..."

create_monitor "Failed Access Spike" '{
  "name": "[FSx-ONTAP] Failed Access Spike (>10 in 5min)",
  "type": "log alert",
  "query": "logs(\"source:fsxn @attributes.result:Failure\").index(\"*\").rollup(\"count\").last(\"5m\") > 10",
  "message": "More than 10 failed access attempts detected on FSx for ONTAP in the last 5 minutes.\n\nThis may indicate:\n- Brute-force access attempts\n- Permission misconfiguration\n- Unauthorized access attempts\n\n@slack-security-alerts",
  "tags": ["source:fsxn", "team:security", "severity:warning"],
  "options": {
    "thresholds": {"critical": 10, "warning": 5},
    "notify_no_data": false,
    "renotify_interval": 60,
    "include_tags": true
  }
}'

# Monitor 2: Pipeline Lambda Errors
create_monitor "Pipeline Lambda Errors" '{
  "name": "[FSx-ONTAP] Pipeline Lambda Errors Detected",
  "type": "metric alert",
  "query": "sum(last_5m):sum:aws.lambda.errors{functionname:fsxn-*}.as_count() > 0",
  "message": "Lambda errors detected in the FSx for ONTAP log shipping pipeline.\n\nCheck CloudWatch Logs for the affected function.\nDLQ may contain failed events for replay.\n\n@slack-ops-alerts",
  "tags": ["source:fsxn", "team:ops", "severity:warning"],
  "options": {
    "thresholds": {"critical": 5, "warning": 1},
    "notify_no_data": false,
    "renotify_interval": 120,
    "include_tags": true
  }
}'

# Monitor 3: DLQ Messages
create_monitor "DLQ Messages Appearing" '{
  "name": "[FSx-ONTAP] Dead Letter Queue Messages Detected",
  "type": "metric alert",
  "query": "avg(last_5m):avg:aws.sqs.approximate_number_of_messages_visible{queuename:fsxn-*-dlq} > 0",
  "message": "Messages detected in the FSx for ONTAP DLQ.\n\nThis means log delivery failed and events are queued for replay.\nFollow the DLQ replay runbook to reprocess failed events.\n\n@slack-ops-alerts",
  "tags": ["source:fsxn", "team:ops", "severity:critical"],
  "options": {
    "thresholds": {"critical": 1},
    "notify_no_data": false,
    "renotify_interval": 60,
    "include_tags": true
  }
}'

echo ""
echo "=== Done ==="
echo "View monitors: https://${DD_SITE}/monitors/manage"
echo ""
echo "Next steps:"
echo "  - Configure notification channels (@slack, @pagerduty, @email)"
echo "  - Adjust thresholds based on your environment"
echo "  - Add the monitors to a Datadog team for ownership"
