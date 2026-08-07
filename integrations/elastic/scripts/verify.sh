#!/bin/bash
# Elastic — Post-deployment E2E verification
# Sends a test log via Bulk API and confirms acceptance (HTTP 200).
#
# Usage:
#   export ELASTIC_URL="https://<cluster-id>.es.<region>.aws.cloud.es.io:9243"
#   export ELASTIC_API_KEY="<your-api-key>"
#   bash integrations/elastic/scripts/verify.sh

set -euo pipefail

# --- AWS-side deployment checks (shared) ------------------------------------
# The vendor-endpoint test below proves credentials and network reach the vendor.
# It passes even when the stack was never deployed or the NotImplementedError
# placeholder Lambda is still in place, so check the AWS side first.
#
# Set SKIP_AWS_CHECKS=1 to test only vendor reachability (e.g. before deploying).
STACK_NAME="${STACK_NAME:-fsxn-elastic-integration}"
LAMBDA_NAME="${LAMBDA_NAME:-fsxn-elastic-integration-shipper}"
SHARED_VERIFY="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../shared/scripts" && pwd)/verify-deployment.sh"
AWS_CHECKS_FAILED=0

if [ "${SKIP_AWS_CHECKS:-0}" != "1" ] && [ -f "$SHARED_VERIFY" ]; then
  STACK_NAME="$STACK_NAME" LAMBDA_NAME="$LAMBDA_NAME" \
    DEPLOY_HINT="bash integrations/elastic/scripts/deploy.sh" \
    bash "$SHARED_VERIFY" || AWS_CHECKS_FAILED=1
  echo ""
fi

ELASTIC_URL="${ELASTIC_URL:-}"
ELASTIC_API_KEY="${ELASTIC_API_KEY:-}"
ELASTIC_INDEX="${ELASTIC_INDEX:-fsxn-audit}"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " Elastic — E2E Verification"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [ -z "${ELASTIC_URL}" ] || [ -z "${ELASTIC_API_KEY}" ]; then
  echo "❌ ERROR: ELASTIC_URL and ELASTIC_API_KEY must be set."
  echo ""
  echo "  export ELASTIC_URL='https://<cluster>.es.<region>.aws.cloud.es.io:9243'"
  echo "  export ELASTIC_API_KEY='<base64-encoded-api-key>'"
  exit 1
fi

TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M:%S.000Z)

PAYLOAD=$(printf '{"index":{"_index":"%s"}}\n{"@timestamp":"%s","event.dataset":"fsxn","event.action":"ReadData","user.name":"CORP\\\\verify-test","file.path":"/share/test/verify-ok.txt","source.ip":"198.51.100.1","event.outcome":"success","observer.name":"VerifySVM","message":"E2E verification test log"}\n' "${ELASTIC_INDEX}" "${TIMESTAMP}")

echo "  Endpoint: ${ELASTIC_URL}/_bulk"
echo "  Index: ${ELASTIC_INDEX}"
echo "  Sending test log..."

RESPONSE=$(curl -s -w "\n%{http_code}" \
  -X POST "${ELASTIC_URL}/_bulk" \
  -H "Content-Type: application/x-ndjson" \
  -H "Authorization: ApiKey ${ELASTIC_API_KEY}" \
  -d "${PAYLOAD}")

HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | sed '$d')

echo ""
if [ "${HTTP_CODE}" = "200" ]; then
  ERRORS=$(echo "${BODY}" | python3 -c "import sys,json; print(json.load(sys.stdin).get('errors',True))" 2>/dev/null || echo "true")
  if [ "${ERRORS}" = "False" ]; then
    echo "  ✅ PASS — Test log indexed (HTTP ${HTTP_CODE}, errors=false)"
    echo ""
    echo "  Verify in Kibana:"
    echo "    Discover → index: ${ELASTIC_INDEX} → user.name: \"CORP\\verify-test\""
  else
    echo "  ⚠️  PARTIAL — HTTP 200 but bulk response has errors"
    echo "  Response: ${BODY}"
    exit 1
  fi
else
  echo "  ❌ FAIL — HTTP ${HTTP_CODE}"
  echo ""
  echo "  Troubleshooting:"
  echo "    401: Check ELASTIC_API_KEY"
  echo "    403: API key may lack index write permission"
  echo "    404: Check ELASTIC_URL"
  exit 1
fi

# Surface an AWS-side failure even when the vendor endpoint responded fine.
if [ "${AWS_CHECKS_FAILED}" = "1" ]; then
  echo "❌ AWS deployment checks failed — see above."
  exit 69
fi
