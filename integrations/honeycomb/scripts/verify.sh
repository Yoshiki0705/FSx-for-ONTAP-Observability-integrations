#!/bin/bash
# Honeycomb — Post-deployment E2E verification
# Sends a test event via Events API and confirms acceptance (HTTP 200).
#
# Usage:
#   export HONEYCOMB_API_KEY="<your-api-key>"  # hcaik_ prefix
#   export HONEYCOMB_DATASET="fsxn-audit"
#   bash integrations/honeycomb/scripts/verify.sh

set -euo pipefail

# --- AWS-side deployment checks (shared) ------------------------------------
# The vendor-endpoint test below proves credentials and network reach the vendor.
# It passes even when the stack was never deployed or the NotImplementedError
# placeholder Lambda is still in place, so check the AWS side first.
#
# Set SKIP_AWS_CHECKS=1 to test only vendor reachability (e.g. before deploying).
STACK_NAME="${STACK_NAME:-fsxn-honeycomb-integration}"
LAMBDA_NAME="${LAMBDA_NAME:-fsxn-honeycomb-integration-shipper}"
SHARED_VERIFY="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../shared/scripts" && pwd)/verify-deployment.sh"
AWS_CHECKS_FAILED=0

if [ "${SKIP_AWS_CHECKS:-0}" != "1" ] && [ -f "$SHARED_VERIFY" ]; then
  STACK_NAME="$STACK_NAME" LAMBDA_NAME="$LAMBDA_NAME" \
    DEPLOY_HINT="bash integrations/honeycomb/scripts/deploy.sh" \
    bash "$SHARED_VERIFY" || AWS_CHECKS_FAILED=1
  echo ""
fi

HONEYCOMB_API_KEY="${HONEYCOMB_API_KEY:-}"
HONEYCOMB_DATASET="${HONEYCOMB_DATASET:-fsxn-audit}"
HONEYCOMB_ENDPOINT="${HONEYCOMB_ENDPOINT:-https://api.honeycomb.io}"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " Honeycomb — E2E Verification"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [ -z "${HONEYCOMB_API_KEY}" ]; then
  echo "❌ ERROR: HONEYCOMB_API_KEY must be set."
  echo ""
  echo "  export HONEYCOMB_API_KEY='hcaik_xxxx'"
  echo "  export HONEYCOMB_DATASET='fsxn-audit'"
  exit 1
fi

TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)

PAYLOAD=$(cat <<EOF
[{
  "time": "${TIMESTAMP}",
  "data": {
    "event_type": "4663",
    "user": "CORP\\\\verify-test",
    "path": "/share/test/verify-ok.txt",
    "result": "Audit Success",
    "svm": "VerifySVM",
    "client_ip": "198.51.100.1",
    "operation": "ReadData",
    "source": "fsxn-verify"
  }
}]
EOF
)

echo "  Endpoint: ${HONEYCOMB_ENDPOINT}/1/batch/${HONEYCOMB_DATASET}"
echo "  Dataset: ${HONEYCOMB_DATASET}"
echo "  Sending test event..."

HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
  -X POST "${HONEYCOMB_ENDPOINT}/1/batch/${HONEYCOMB_DATASET}" \
  -H "Content-Type: application/json" \
  -H "X-Honeycomb-Team: ${HONEYCOMB_API_KEY}" \
  -d "${PAYLOAD}")

echo ""
if [ "${HTTP_CODE}" = "200" ]; then
  echo "  ✅ PASS — Test event accepted (HTTP ${HTTP_CODE})"
  echo ""
  echo "  Verify in Honeycomb:"
  echo "    Query → Dataset: ${HONEYCOMB_DATASET} → WHERE user = \"CORP\\verify-test\""
else
  echo "  ❌ FAIL — HTTP ${HTTP_CODE}"
  echo ""
  echo "  Troubleshooting:"
  echo "    401: Check HONEYCOMB_API_KEY (must start with hcaik_)"
  echo "    404: Dataset '${HONEYCOMB_DATASET}' may not exist"
  exit 1
fi

# Surface an AWS-side failure even when the vendor endpoint responded fine.
if [ "${AWS_CHECKS_FAILED}" = "1" ]; then
  echo "❌ AWS deployment checks failed — see above."
  exit 69
fi
