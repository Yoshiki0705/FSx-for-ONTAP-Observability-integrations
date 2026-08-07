#!/bin/bash
# Grafana Cloud — Post-deployment E2E verification
# Sends a test log and confirms acceptance (HTTP 2xx).
#
# Usage:
#   export GRAFANA_INSTANCE_ID="<your-instance-id>"
#   export GRAFANA_API_KEY="<your-cloud-api-key>"
#   export GRAFANA_REGION="prod-ap-southeast-1"  # or prod-us-east-0, etc.
#   bash integrations/grafana/scripts/verify.sh
#
# The script sends one FSx for ONTAP-shaped log entry via OTLP Gateway.
# Success = HTTP 200 (log accepted by Grafana Cloud).

set -euo pipefail

# --- AWS-side deployment checks (shared) ------------------------------------
# The vendor-endpoint test below proves credentials and network reach the vendor.
# It passes even when the stack was never deployed or the NotImplementedError
# placeholder Lambda is still in place, so check the AWS side first.
#
# Set SKIP_AWS_CHECKS=1 to test only vendor reachability (e.g. before deploying).
STACK_NAME="${STACK_NAME:-fsxn-grafana-integration}"
LAMBDA_NAME="${LAMBDA_NAME:-fsxn-grafana-integration-shipper}"
SHARED_VERIFY="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../shared/scripts" && pwd)/verify-deployment.sh"
AWS_CHECKS_FAILED=0

if [ "${SKIP_AWS_CHECKS:-0}" != "1" ] && [ -f "$SHARED_VERIFY" ]; then
  STACK_NAME="$STACK_NAME" LAMBDA_NAME="$LAMBDA_NAME" \
    DEPLOY_HINT="bash integrations/grafana/scripts/deploy.sh" \
    bash "$SHARED_VERIFY" || AWS_CHECKS_FAILED=1
  echo ""
fi

GRAFANA_INSTANCE_ID="${GRAFANA_INSTANCE_ID:-}"
GRAFANA_API_KEY="${GRAFANA_API_KEY:-}"
GRAFANA_REGION="${GRAFANA_REGION:-prod-ap-southeast-1}"
OTLP_ENDPOINT="https://otlp-gateway-${GRAFANA_REGION}.grafana.net/otlp/v1/logs"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " Grafana Cloud — E2E Verification"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [ -z "${GRAFANA_INSTANCE_ID}" ] || [ -z "${GRAFANA_API_KEY}" ]; then
  echo "❌ ERROR: GRAFANA_INSTANCE_ID and GRAFANA_API_KEY must be set."
  echo ""
  echo "  export GRAFANA_INSTANCE_ID='<instance-id>'"
  echo "  export GRAFANA_API_KEY='<cloud-api-key>'"
  echo "  export GRAFANA_REGION='prod-ap-southeast-1'"
  exit 1
fi

AUTH=$(echo -n "${GRAFANA_INSTANCE_ID}:${GRAFANA_API_KEY}" | base64)
TIMESTAMP=$(date -u +%s)000000000

PAYLOAD=$(cat <<EOF
{
  "resourceLogs": [{
    "resource": {"attributes": [
      {"key": "service.name", "value": {"stringValue": "fsxn-audit"}},
      {"key": "deployment.environment", "value": {"stringValue": "verify"}}
    ]},
    "scopeLogs": [{"logRecords": [{
      "timeUnixNano": "${TIMESTAMP}",
      "body": {"stringValue": "{\"event_type\":\"4663\",\"user\":\"CORP\\\\verify-test\",\"path\":\"/share/test/verify-ok.txt\",\"result\":\"Audit Success\",\"svm\":\"VerifySVM\",\"client_ip\":\"198.51.100.1\",\"operation\":\"ReadData\"}"},
      "attributes": [
        {"key": "user", "value": {"stringValue": "CORP\\\\verify-test"}},
        {"key": "operation", "value": {"stringValue": "ReadData"}},
        {"key": "svm", "value": {"stringValue": "VerifySVM"}}
      ],
      "severityText": "INFO"
    }]}]
  }]
}
EOF
)

echo "  Endpoint: ${OTLP_ENDPOINT}"
echo "  Sending test log..."

HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
  -X POST "${OTLP_ENDPOINT}" \
  -H "Content-Type: application/json" \
  -H "Authorization: Basic ${AUTH}" \
  -d "${PAYLOAD}")

echo ""
if [ "${HTTP_CODE}" = "200" ] || [ "${HTTP_CODE}" = "204" ]; then
  echo "  ✅ PASS — Test log accepted (HTTP ${HTTP_CODE})"
  echo ""
  echo "  Verify in Grafana Cloud:"
  echo "    Explore → Loki → {service_name=\"fsxn-audit\"} | json | user=\"CORP\\verify-test\""
else
  echo "  ❌ FAIL — HTTP ${HTTP_CODE}"
  echo ""
  echo "  Troubleshooting:"
  echo "    401: Check GRAFANA_INSTANCE_ID and GRAFANA_API_KEY"
  echo "    403: API key may lack 'logs:write' scope"
  echo "    404: Check GRAFANA_REGION (current: ${GRAFANA_REGION})"
  exit 1
fi

# Surface an AWS-side failure even when the vendor endpoint responded fine.
if [ "${AWS_CHECKS_FAILED}" = "1" ]; then
  echo "❌ AWS deployment checks failed — see above."
  exit 69
fi
