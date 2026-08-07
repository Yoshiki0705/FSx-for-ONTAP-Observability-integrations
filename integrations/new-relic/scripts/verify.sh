#!/bin/bash
# New Relic — Post-deployment E2E verification
# Sends a test log via Log API and confirms acceptance (HTTP 202).
#
# Usage:
#   export NEW_RELIC_LICENSE_KEY="<your-license-key>"
#   export NEW_RELIC_REGION="US"  # or EU
#   bash integrations/new-relic/scripts/verify.sh

set -euo pipefail

# --- AWS-side deployment checks (shared) ------------------------------------
# The vendor-endpoint test below proves credentials and network reach the vendor.
# It passes even when the stack was never deployed or the NotImplementedError
# placeholder Lambda is still in place, so check the AWS side first.
#
# Set SKIP_AWS_CHECKS=1 to test only vendor reachability (e.g. before deploying).
STACK_NAME="${STACK_NAME:-fsxn-new-relic-integration}"
LAMBDA_NAME="${LAMBDA_NAME:-fsxn-new-relic-integration-shipper}"
SHARED_VERIFY="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../shared/scripts" && pwd)/verify-deployment.sh"
AWS_CHECKS_FAILED=0

if [ "${SKIP_AWS_CHECKS:-0}" != "1" ] && [ -f "$SHARED_VERIFY" ]; then
  STACK_NAME="$STACK_NAME" LAMBDA_NAME="$LAMBDA_NAME" \
    DEPLOY_HINT="bash integrations/new-relic/scripts/deploy.sh" \
    bash "$SHARED_VERIFY" || AWS_CHECKS_FAILED=1
  echo ""
fi

NEW_RELIC_LICENSE_KEY="${NEW_RELIC_LICENSE_KEY:-}"
NEW_RELIC_REGION="${NEW_RELIC_REGION:-US}"

if [ "${NEW_RELIC_REGION}" = "EU" ]; then
  LOG_ENDPOINT="https://log-api.eu.newrelic.com/log/v1"
else
  LOG_ENDPOINT="https://log-api.newrelic.com/log/v1"
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " New Relic — E2E Verification"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [ -z "${NEW_RELIC_LICENSE_KEY}" ]; then
  echo "❌ ERROR: NEW_RELIC_LICENSE_KEY must be set."
  echo ""
  echo "  export NEW_RELIC_LICENSE_KEY='<license-key>'"
  echo "  export NEW_RELIC_REGION='US'  # or EU"
  exit 1
fi

TIMESTAMP=$(date +%s)

PAYLOAD=$(cat <<EOF
[{
  "common": {"attributes": {"logtype": "fsxn-audit", "instrumentation.provider": "fsxn"}},
  "logs": [{
    "timestamp": ${TIMESTAMP},
    "message": "E2E verification test log",
    "attributes": {
      "event_type": "4663",
      "user": "CORP\\\\verify-test",
      "path": "/share/test/verify-ok.txt",
      "result": "Audit Success",
      "svm": "VerifySVM",
      "client_ip": "198.51.100.1",
      "operation": "ReadData"
    }
  }]
}]
EOF
)

echo "  Endpoint: ${LOG_ENDPOINT}"
echo "  Region: ${NEW_RELIC_REGION}"
echo "  Sending test log..."

HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
  -X POST "${LOG_ENDPOINT}" \
  -H "Content-Type: application/json" \
  -H "Api-Key: ${NEW_RELIC_LICENSE_KEY}" \
  -d "${PAYLOAD}")

echo ""
if [ "${HTTP_CODE}" = "202" ]; then
  echo "  ✅ PASS — Test log accepted (HTTP ${HTTP_CODE})"
  echo ""
  echo "  Verify in New Relic:"
  echo "    Logs → WHERE instrumentation.provider = 'fsxn' AND user = 'CORP\\verify-test'"
else
  echo "  ❌ FAIL — HTTP ${HTTP_CODE}"
  echo ""
  echo "  Troubleshooting:"
  echo "    403: Check NEW_RELIC_LICENSE_KEY"
  echo "    404: Check NEW_RELIC_REGION (US vs EU)"
  exit 1
fi

# Surface an AWS-side failure even when the vendor endpoint responded fine.
if [ "${AWS_CHECKS_FAILED}" = "1" ]; then
  echo "❌ AWS deployment checks failed — see above."
  exit 69
fi
