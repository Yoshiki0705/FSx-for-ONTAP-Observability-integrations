#!/bin/bash
# Splunk — Post-deployment E2E verification
# Sends a test log via HEC and confirms acceptance (HTTP 200).
#
# Usage:
#   export SPLUNK_HEC_URL="https://<host>:8088"
#   export SPLUNK_HEC_TOKEN="<your-hec-token>"
#   bash integrations/splunk-serverless/scripts/verify.sh

set -euo pipefail

# --- AWS-side deployment checks (shared) ------------------------------------
# The vendor-endpoint test below proves credentials and network reach the vendor.
# It passes even when the stack was never deployed or the NotImplementedError
# placeholder Lambda is still in place, so check the AWS side first.
#
# Set SKIP_AWS_CHECKS=1 to test only vendor reachability (e.g. before deploying).
STACK_NAME="${STACK_NAME:-fsxn-splunk-integration}"
LAMBDA_NAME="${LAMBDA_NAME:-fsxn-splunk-integration-shipper}"
SHARED_VERIFY="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../shared/scripts" && pwd)/verify-deployment.sh"
AWS_CHECKS_FAILED=0

if [ "${SKIP_AWS_CHECKS:-0}" != "1" ] && [ -f "$SHARED_VERIFY" ]; then
  STACK_NAME="$STACK_NAME" LAMBDA_NAME="$LAMBDA_NAME" \
    DEPLOY_HINT="bash integrations/splunk-serverless/scripts/deploy.sh" \
    bash "$SHARED_VERIFY" || AWS_CHECKS_FAILED=1
  echo ""
fi

SPLUNK_HEC_URL="${SPLUNK_HEC_URL:-}"
SPLUNK_HEC_TOKEN="${SPLUNK_HEC_TOKEN:-}"
SPLUNK_INDEX="${SPLUNK_INDEX:-fsxn_audit}"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " Splunk — E2E Verification"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [ -z "${SPLUNK_HEC_URL}" ] || [ -z "${SPLUNK_HEC_TOKEN}" ]; then
  echo "❌ ERROR: SPLUNK_HEC_URL and SPLUNK_HEC_TOKEN must be set."
  echo ""
  echo "  export SPLUNK_HEC_URL='https://<host>:8088'"
  echo "  export SPLUNK_HEC_TOKEN='<hec-token>'"
  exit 1
fi

TIMESTAMP=$(date +%s)

PAYLOAD=$(cat <<EOF
{"event":{"event_type":"4663","user":"CORP\\\\verify-test","path":"/share/test/verify-ok.txt","result":"Audit Success","svm":"VerifySVM","client_ip":"198.51.100.1","operation":"ReadData"},"sourcetype":"fsxn:audit","source":"fsxn-verify","index":"${SPLUNK_INDEX}","time":${TIMESTAMP}}
EOF
)

echo "  Endpoint: ${SPLUNK_HEC_URL}/services/collector/event"
echo "  Index: ${SPLUNK_INDEX}"
echo "  Sending test log..."

HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -k \
  -X POST "${SPLUNK_HEC_URL}/services/collector/event" \
  -H "Authorization: Splunk ${SPLUNK_HEC_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "${PAYLOAD}")

echo ""
if [ "${HTTP_CODE}" = "200" ]; then
  echo "  ✅ PASS — Test log accepted (HTTP ${HTTP_CODE})"
  echo ""
  echo "  Verify in Splunk:"
  echo "    index=${SPLUNK_INDEX} user=\"CORP\\verify-test\" | head 1"
else
  echo "  ❌ FAIL — HTTP ${HTTP_CODE}"
  echo ""
  echo "  Troubleshooting:"
  echo "    403: HEC token disabled or invalid"
  echo "    400: Check index name exists and token has write access"
  echo "    Connection refused: Verify SPLUNK_HEC_URL and port 8088"
  exit 1
fi

# Surface an AWS-side failure even when the vendor endpoint responded fine.
if [ "${AWS_CHECKS_FAILED}" = "1" ]; then
  echo "❌ AWS deployment checks failed — see above."
  exit 69
fi
