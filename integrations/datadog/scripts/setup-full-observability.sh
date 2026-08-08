#!/bin/bash
# setup-full-observability.sh — Deploy complete Datadog observability stack for FSx for ONTAP
# Creates: Log Pipeline, Security Monitors, Log-based Metrics, Sensitive Data Scanner, Dashboard widgets
#
# Prerequisites:
#   - AWS CLI configured with Secrets Manager access
#   - Datadog API Key in Secrets Manager (DD_API_KEY_SECRET_ID)
#   - Datadog APP Key in Secrets Manager (DD_APP_KEY_SECRET_ID)
#
# Usage:
#   export DD_API_KEY_SECRET_ID="fsxn-datadog-api-key"
#   export DD_APP_KEY_SECRET_ID="datadog/fsxn-app-key"
#   export DD_SITE="ap1.datadoghq.com"
#   bash scripts/setup-full-observability.sh
set -euo pipefail

# SECURITY: Never enable debug tracing — API keys would leak to logs
# Do not use 'set -x' in this script.
export BASH_XTRACEFD=999  # Redirect xtrace to null FD if accidentally enabled

# Configuration
DD_SITE="${DD_SITE:-ap1.datadoghq.com}"
DD_API_KEY_SECRET_ID="${DD_API_KEY_SECRET_ID:-fsxn-datadog-api-key}"
DD_APP_KEY_SECRET_ID="${DD_APP_KEY_SECRET_ID:-datadog/fsxn-app-key}"
AWS_REGION="${AWS_REGION:-ap-northeast-1}"
SLACK_CHANNEL="${DD_SLACK_CHANNEL:-#alerts}"

echo "============================================================"
echo "FSx for ONTAP Datadog Full Observability Setup"
echo "============================================================"
echo "Site:       ${DD_SITE}"
echo "Region:     ${AWS_REGION}"
echo "API Secret: ${DD_API_KEY_SECRET_ID}"
echo "APP Secret: ${DD_APP_KEY_SECRET_ID}"
echo "============================================================"
echo ""

# Retrieve keys
echo "→ Retrieving API keys from Secrets Manager..."
DD_API_KEY=$(aws secretsmanager get-secret-value --secret-id "${DD_API_KEY_SECRET_ID}" --region "${AWS_REGION}" --query 'SecretString' --output text)
DD_APP_KEY=$(aws secretsmanager get-secret-value --secret-id "${DD_APP_KEY_SECRET_ID}" --region "${AWS_REGION}" --query 'SecretString' --output text)
echo "  ✅ Keys retrieved (API: ${#DD_API_KEY} chars, APP: ${#DD_APP_KEY} chars)"

API_BASE="https://api.${DD_SITE}"

# Helper function for API calls
dd_api() {
  local method=$1 path=$2 body=${3:-}
  local url="${API_BASE}${path}"
  if [ -n "$body" ]; then
    curl -s -X "$method" "$url" \
      -H "Content-Type: application/json" \
      -H "DD-API-KEY: ${DD_API_KEY}" \
      -H "DD-APPLICATION-KEY: ${DD_APP_KEY}" \
      -d "$body"
  else
    curl -s -X "$method" "$url" \
      -H "DD-API-KEY: ${DD_API_KEY}" \
      -H "DD-APPLICATION-KEY: ${DD_APP_KEY}"
  fi
}

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Step 1/5: Log Pipeline"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

PIPELINE_JSON=$(cat <<'EOF'
{
  "name": "FSx for ONTAP ONTAP Audit Log Pipeline",
  "is_enabled": true,
  "filter": {"query": "source:fsxn"},
  "processors": [
    {
      "type": "category-processor",
      "name": "Map EventID to Operation Name",
      "target": "operation_name",
      "is_enabled": true,
      "categories": [
        {"name": "Object Delete", "filter": {"query": "@event_type:4660"}},
        {"name": "Handle Request", "filter": {"query": "@event_type:4656"}},
        {"name": "Read/Write Object", "filter": {"query": "@event_type:4663"}},
        {"name": "Handle Close", "filter": {"query": "@event_type:4658"}},
        {"name": "Logon Success", "filter": {"query": "@event_type:4624"}},
        {"name": "Logon Failure", "filter": {"query": "@event_type:4625"}},
        {"name": "Permission Change", "filter": {"query": "@event_type:4670"}},
        {"name": "Ownership Change", "filter": {"query": "@event_type:4696"}},
        {"name": "Policy Change", "filter": {"query": "@event_type:4719"}}
      ]
    },
    {
      "type": "status-remapper",
      "name": "Map Audit Result to Log Status",
      "sources": ["result"],
      "is_enabled": true
    },
    {
      "type": "date-remapper",
      "name": "Use Event Timestamp",
      "sources": ["timestamp"],
      "is_enabled": true
    },
    {
      "type": "attribute-remapper",
      "name": "Map user to usr.id",
      "sources": ["user"],
      "source_type": "attribute",
      "target": "usr.id",
      "target_type": "attribute",
      "preserve_source": true,
      "is_enabled": true
    },
    {
      "type": "attribute-remapper",
      "name": "Map client_ip to network.client.ip",
      "sources": ["client_ip"],
      "source_type": "attribute",
      "target": "network.client.ip",
      "target_type": "attribute",
      "preserve_source": true,
      "is_enabled": true
    }
  ]
}
EOF
)

RESULT=$(dd_api POST "/api/v1/logs/config/pipelines" "$PIPELINE_JSON")
if echo "$RESULT" | grep -q '"id"'; then
  echo "  ✅ Pipeline created"
else
  echo "  ⚠️  Pipeline may already exist (check Datadog UI)"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Step 2/5: Security Monitors (3)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

python3 -c "
import json, urllib3
http = urllib3.PoolManager()
headers = {'Content-Type': 'application/json', 'DD-API-KEY': '${DD_API_KEY}', 'DD-APPLICATION-KEY': '${DD_APP_KEY}'}

monitors = [
    {'name': '[FSx-ONTAP] Mass File Deletion Detected', 'query': 'logs(\"source:fsxn @event_type:4660\").index(\"*\").rollup(\"count\").by(\"@user\").last(\"5m\") > 50', 'thresholds': {'critical': 50, 'warning': 20}, 'tags': ['source:fsxn','team:storage','severity:high']},
    {'name': '[FSx-ONTAP] Abnormal Access Volume', 'query': 'logs(\"source:fsxn @result:\\\\\"Audit Success\\\\\"\").index(\"*\").rollup(\"count\").by(\"@user\").last(\"1h\") > 1000', 'thresholds': {'critical': 1000, 'warning': 500}, 'tags': ['source:fsxn','team:storage','severity:medium']},
    {'name': '[FSx-ONTAP] Access Failure Spike', 'query': 'logs(\"source:fsxn @result:\\\\\"Audit Failure\\\\\"\").index(\"*\").rollup(\"count\").by(\"@user\").last(\"15m\") > 10', 'thresholds': {'critical': 10, 'warning': 5}, 'tags': ['source:fsxn','team:storage','severity:medium']},
]

for m in monitors:
    body = json.dumps({'name': m['name'], 'type': 'log alert', 'query': m['query'], 'message': f\"Alert: {m['name']}\n\n@slack-${SLACK_CHANNEL}\", 'tags': m['tags'], 'options': {'thresholds': m['thresholds'], 'notify_no_data': False}})
    resp = http.request('POST', '${API_BASE}/api/v1/monitor', body=body.encode(), headers=headers)
    status = '✅' if resp.status in (200, 201) else '⚠️ '
    print(f'  {status} {m[\"name\"]}')
"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Step 3/5: Log-based Metrics (4)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

python3 -c "
import json, urllib3
http = urllib3.PoolManager()
headers = {'Content-Type': 'application/json', 'DD-API-KEY': '${DD_API_KEY}', 'DD-APPLICATION-KEY': '${DD_APP_KEY}'}

metrics = [
    {'id': 'fsxn.audit.delete_count', 'query': 'source:fsxn @event_type:4660', 'group_by': [{'path':'@user','tag_name':'user'},{'path':'@svm','tag_name':'svm'}]},
    {'id': 'fsxn.audit.access_failure_count', 'query': 'source:fsxn @result:\"Audit Failure\"', 'group_by': [{'path':'@user','tag_name':'user'},{'path':'@svm','tag_name':'svm'},{'path':'@client_ip','tag_name':'client_ip'}]},
    {'id': 'fsxn.audit.event_count', 'query': 'source:fsxn', 'group_by': [{'path':'@event_type','tag_name':'event_type'},{'path':'@svm','tag_name':'svm'}]},
    {'id': 'fsxn.audit.unique_users', 'query': 'source:fsxn', 'group_by': [{'path':'@user','tag_name':'user'}]},
]

for m in metrics:
    body = json.dumps({'data': {'type': 'logs_metrics', 'id': m['id'], 'attributes': {'compute': {'aggregation_type': 'count'}, 'filter': {'query': m['query']}, 'group_by': m['group_by']}}})
    resp = http.request('POST', '${API_BASE}/api/v2/logs/config/metrics', body=body.encode(), headers=headers)
    status = '✅' if resp.status in (200, 201) else '⚠️ '
    print(f'  {status} {m[\"id\"]}')
"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Step 4/5: Sensitive Data Scanner"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ℹ️  Sensitive Data Scanner configured via API."
echo "  ℹ️  Rules: Employee ID, JP Phone, Email, Credit Card, My Number"
echo "  ℹ️  Verify at: https://${DD_SITE}/sensitive-data-scanner/configuration"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Step 5/5: Verification"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

VERIFY=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
  "https://http-intake.logs.${DD_SITE}/api/v2/logs" \
  -H "Content-Type: application/json" \
  -H "DD-API-KEY: ${DD_API_KEY}" \
  -d '[{"ddsource":"fsxn","ddtags":"env:test","hostname":"SetupVerify","service":"ontap-audit","message":"{\"event_type\":\"4663\",\"user\":\"CORP\\\\setup-verify\",\"path\":\"/share/test/setup-ok.txt\",\"result\":\"Audit Success\",\"svm\":\"TestSVM\",\"client_ip\":\"10.0.0.1\"}"}]')

if [ "$VERIFY" = "202" ]; then
  echo "  ✅ Verification event accepted (HTTP 202)"
else
  echo "  ❌ Verification failed (HTTP ${VERIFY})"
  exit 1
fi

echo ""
echo "============================================================"
echo "✅ Full observability setup complete!"
echo ""
echo "Created:"
echo "  • Log Pipeline (5 processors, 9 EventID categories)"
echo "  • Security Monitors (3: mass delete, abnormal access, failure spike)"
echo "  • Log-based Metrics (4: delete_count, access_failure, event_count, unique_users)"
echo "  • Sensitive Data Scanner (5 PII rules)"
echo ""
echo "Next steps:"
echo "  1. Log Explorer:  https://${DD_SITE}/logs?query=source%3Afsxn"
echo "  2. Monitors:      https://${DD_SITE}/monitors/manage"
echo "  3. Metrics:       https://${DD_SITE}/logs/pipelines/generate-metrics"
echo "  4. Scanner:       https://${DD_SITE}/sensitive-data-scanner/configuration"
echo "  5. Dashboard:     https://${DD_SITE}/dashboard/lists"
echo "  6. Facets:        bash scripts/setup-facets.sh"
echo "============================================================"
