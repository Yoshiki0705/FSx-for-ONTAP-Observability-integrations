#!/bin/bash
# OTel Collector — post-deployment verification.
#
# Runs the shared AWS-side checks against all three shipper functions, then
# optionally sends one synthetic OTLP log to the configured endpoint to prove the
# Collector is reachable and accepting.
#
# Usage:
#   bash integrations/otel-collector/scripts/verify.sh
#   OTLP_ENDPOINT=http://localhost:4318 bash integrations/otel-collector/scripts/verify.sh
#
# Environment variables:
#   AWS_REGION      Region (default: ap-northeast-1)
#   STACK_PREFIX    Stack name prefix (default: fsxn-otel)
#   OTLP_ENDPOINT   If set, a synthetic log is POSTed to /v1/logs
#   SKIP_AWS_CHECKS Set to 1 to test only endpoint reachability
#
# Exit codes (BSD sysexits.h):
#   0  all checks passed
#   69 (EX_UNAVAILABLE) one or more checks failed
#   78 (EX_CONFIG)      required configuration missing

set -euo pipefail

AWS_REGION="${AWS_REGION:-ap-northeast-1}"
STACK_PREFIX="${STACK_PREFIX:-fsxn-otel}"
STACK_NAME="${STACK_NAME:-${STACK_PREFIX}-integration}"
OTLP_ENDPOINT="${OTLP_ENDPOINT:-}"

SHARED_VERIFY="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../shared/scripts" && pwd)/verify-deployment.sh"
FAILED=0

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " OTel Collector — Deployment Verification"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Every function shares one deployment package, so a forgotten upload affects all
# three. They are still checked individually because a partial stack update can
# leave them on different versions.
if [ "${SKIP_AWS_CHECKS:-0}" != "1" ]; then
  if [ ! -f "$SHARED_VERIFY" ]; then
    echo "ERROR: shared verifier not found: ${SHARED_VERIFY}" >&2
    exit 78
  fi
  for fn in fsxn-otel-log-shipper fsxn-otel-ems-shipper fsxn-otel-fpolicy-shipper; do
    STACK_NAME="$STACK_NAME" LAMBDA_NAME="$fn" \
      DEPLOY_HINT="bash integrations/otel-collector/scripts/deploy.sh --code-only" \
      bash "$SHARED_VERIFY" || FAILED=1
    echo ""
  done
fi

# --- OTLP endpoint reachability ---------------------------------------------
if [ -z "${OTLP_ENDPOINT}" ]; then
  echo "--- OTLP endpoint check ---"
  echo "  ⏭️  SKIP — set OTLP_ENDPOINT to send a synthetic log"
else
  echo "--- OTLP endpoint check ---"
  echo "  Endpoint: ${OTLP_ENDPOINT}/v1/logs"
  NANOS="$(date -u +%s)000000000"
  PAYLOAD=$(cat <<EOF
{"resourceLogs":[{"resource":{"attributes":[
  {"key":"service.name","value":{"stringValue":"fsxn-verify"}}]},
  "scopeLogs":[{"scope":{"name":"verify"},"logRecords":[
  {"timeUnixNano":"${NANOS}","severityNumber":9,"severityText":"INFO",
   "body":{"stringValue":"fsxn-otel verify probe"},
   "attributes":[{"key":"user","value":{"stringValue":"verify-test"}}]}]}]}]}
EOF
)
  HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
    -X POST "${OTLP_ENDPOINT}/v1/logs" \
    -H "Content-Type: application/json" \
    -d "${PAYLOAD}" || echo "000")

  if [ "${HTTP_CODE}" = "200" ] || [ "${HTTP_CODE}" = "204" ]; then
    echo "  ✅ PASS — synthetic log accepted (HTTP ${HTTP_CODE})"
  elif [ "${HTTP_CODE}" = "000" ]; then
    echo "  ❌ FAIL — could not reach the endpoint (DNS, routing or TLS)"
    FAILED=1
  else
    echo "  ❌ FAIL — HTTP ${HTTP_CODE}"
    echo "     401/403: the Collector requires auth — set API_KEY_SECRET_ARN and AUTH_MODE"
    echo "     404:     wrong path; this posts to \${OTLP_ENDPOINT}/v1/logs"
    echo "     415:     the endpoint wants Protobuf — set OTLP_CONTENT_TYPE=protobuf"
    FAILED=1
  fi
fi
echo ""

if [ "${FAILED}" = "1" ]; then
  echo "❌ Verification failed — see above."
  exit 69
fi
echo "✅ All checks passed."
