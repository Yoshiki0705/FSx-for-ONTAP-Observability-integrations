#!/bin/bash
# Dynatrace — Post-deployment E2E verification
# Sends a test log via Log Ingest API and confirms acceptance (HTTP 204).
#
# Usage:
#   export DT_ENV_URL="https://<env-id>.live.dynatrace.com"
#   export DT_API_TOKEN="<your-api-token>"  # Scope: logs.ingest
#   bash integrations/dynatrace/scripts/verify.sh

set -euo pipefail

# --- AWS-side deployment checks (shared) ------------------------------------
# The vendor-endpoint test below proves credentials and network reach the vendor.
# It passes even when the stack was never deployed or the NotImplementedError
# placeholder Lambda is still in place, so check the AWS side first.
#
# Set SKIP_AWS_CHECKS=1 to test only vendor reachability (e.g. before deploying).
STACK_NAME="${STACK_NAME:-fsxn-dynatrace-integration}"
LAMBDA_NAME="${LAMBDA_NAME:-fsxn-dynatrace-integration-shipper}"
SHARED_VERIFY="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../shared/scripts" && pwd)/verify-deployment.sh"
AWS_CHECKS_FAILED=0

if [ "${SKIP_AWS_CHECKS:-0}" != "1" ] && [ -f "$SHARED_VERIFY" ]; then
  STACK_NAME="$STACK_NAME" LAMBDA_NAME="$LAMBDA_NAME" \
    DEPLOY_HINT="bash integrations/dynatrace/scripts/deploy.sh" \
    bash "$SHARED_VERIFY" || AWS_CHECKS_FAILED=1
  echo ""
fi

DT_ENV_URL="${DT_ENV_URL:-}"
DT_API_TOKEN="${DT_API_TOKEN:-}"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " Dynatrace — E2E Verification"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [ -z "${DT_ENV_URL}" ] || [ -z "${DT_API_TOKEN}" ]; then
  echo "❌ ERROR: DT_ENV_URL and DT_API_TOKEN must be set."
  echo ""
  echo "  export DT_ENV_URL='https://<env-id>.live.dynatrace.com'"
  echo "  export DT_API_TOKEN='<token>'  # Scope: logs.ingest"
  exit 1
fi

TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M:%S.000000000Z)

PAYLOAD=$(cat <<EOF
[{
  "content": "{\"event_type\":\"4663\",\"user\":\"CORP\\\\verify-test\",\"path\":\"/share/test/verify-ok.txt\",\"result\":\"Audit Success\",\"svm\":\"VerifySVM\",\"client_ip\":\"198.51.100.1\",\"operation\":\"ReadData\"}",
  "log.source": "fsxn",
  "severity": "info",
  "timestamp": "${TIMESTAMP}",
  "dt.entity.custom_device": "fsxn-verify"
}]
EOF
)

echo "  Endpoint: ${DT_ENV_URL}/api/v2/logs/ingest"
echo "  Sending test log..."

HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
  -X POST "${DT_ENV_URL}/api/v2/logs/ingest" \
  -H "Content-Type: application/json; charset=utf-8" \
  -H "Authorization: Api-Token ${DT_API_TOKEN}" \
  -d "${PAYLOAD}")

echo ""
if [ "${HTTP_CODE}" = "204" ]; then
  echo "  ✅ PASS — Test log accepted (HTTP ${HTTP_CODE})"
  echo ""
  echo "  Verify in Dynatrace:"
  echo "    Observe → Logs → DQL: fetch logs | filter matchesValue(log.source, \"fsxn\") | filter matchesValue(content, \"verify-test\")"
elif [ "${HTTP_CODE}" = "200" ]; then
  echo "  ✅ PASS — Test log accepted (HTTP ${HTTP_CODE})"
else
  echo "  ❌ FAIL — HTTP ${HTTP_CODE}"
  echo ""
  echo "  Troubleshooting:"
  echo "    401: Check DT_API_TOKEN"
  echo "    403: Token may lack 'logs.ingest' scope"
  echo "    413: Payload too large (should not happen with single log)"
  exit 1
fi

# Surface an AWS-side failure even when the vendor endpoint responded fine.
if [ "${AWS_CHECKS_FAILED}" = "1" ]; then
  echo "❌ AWS deployment checks failed — see above."
  exit 69
fi
