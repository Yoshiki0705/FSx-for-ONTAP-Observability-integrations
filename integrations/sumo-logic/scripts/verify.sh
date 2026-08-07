#!/bin/bash
# Sumo Logic — Post-deployment E2E verification
# Sends a test log via HTTP Source and confirms acceptance (HTTP 200).
#
# Usage:
#   export SUMO_HTTP_SOURCE_URL="https://endpoint<N>.collection.sumologic.com/receiver/v1/http/<token>"
#   bash integrations/sumo-logic/scripts/verify.sh

set -euo pipefail

# --- AWS-side deployment checks (shared) ------------------------------------
# The vendor-endpoint test below proves credentials and network reach the vendor.
# It passes even when the stack was never deployed or the NotImplementedError
# placeholder Lambda is still in place, so check the AWS side first.
#
# Set SKIP_AWS_CHECKS=1 to test only vendor reachability (e.g. before deploying).
STACK_NAME="${STACK_NAME:-fsxn-sumo-logic-integration}"
LAMBDA_NAME="${LAMBDA_NAME:-fsxn-sumo-logic-integration-shipper}"
SHARED_VERIFY="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../shared/scripts" && pwd)/verify-deployment.sh"
AWS_CHECKS_FAILED=0

if [ "${SKIP_AWS_CHECKS:-0}" != "1" ] && [ -f "$SHARED_VERIFY" ]; then
  STACK_NAME="$STACK_NAME" LAMBDA_NAME="$LAMBDA_NAME" \
    DEPLOY_HINT="bash integrations/sumo-logic/scripts/deploy.sh" \
    bash "$SHARED_VERIFY" || AWS_CHECKS_FAILED=1
  echo ""
fi

SUMO_HTTP_SOURCE_URL="${SUMO_HTTP_SOURCE_URL:-}"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " Sumo Logic — E2E Verification"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [ -z "${SUMO_HTTP_SOURCE_URL}" ]; then
  echo "❌ ERROR: SUMO_HTTP_SOURCE_URL must be set."
  echo ""
  echo "  export SUMO_HTTP_SOURCE_URL='https://endpoint<N>.collection.sumologic.com/receiver/v1/http/<token>'"
  exit 1
fi

TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)

PAYLOAD=$(cat <<EOF
{"timestamp":"${TIMESTAMP}","event_type":"4663","user":"CORP\\\\verify-test","path":"/share/test/verify-ok.txt","result":"Audit Success","svm":"VerifySVM","client_ip":"198.51.100.1","operation":"ReadData","_source":"fsxn-verify"}
EOF
)

echo "  Endpoint: ${SUMO_HTTP_SOURCE_URL:0:60}..."
echo "  Sending test log..."

HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
  -X POST "${SUMO_HTTP_SOURCE_URL}" \
  -H "Content-Type: application/json" \
  -H "X-Sumo-Category: fsxn/verify" \
  -H "X-Sumo-Name: fsxn-verify-test" \
  -d "${PAYLOAD}")

echo ""
if [ "${HTTP_CODE}" = "200" ]; then
  echo "  ✅ PASS — Test log accepted (HTTP ${HTTP_CODE})"
  echo ""
  echo "  Verify in Sumo Logic:"
  echo "    Log Search → _sourceCategory=fsxn/verify user=\"CORP\\verify-test\""
  echo ""
  echo "  Note: Logs may take 30-60 seconds to appear in search results."
else
  echo "  ❌ FAIL — HTTP ${HTTP_CODE}"
  echo ""
  echo "  Troubleshooting:"
  echo "    401/403: HTTP Source URL may be invalid or disabled"
  echo "    404: Check the full HTTP Source URL including token"
  exit 1
fi

# Surface an AWS-side failure even when the vendor endpoint responded fine.
if [ "${AWS_CHECKS_FAILED}" = "1" ]; then
  echo "❌ AWS deployment checks failed — see above."
  exit 69
fi
