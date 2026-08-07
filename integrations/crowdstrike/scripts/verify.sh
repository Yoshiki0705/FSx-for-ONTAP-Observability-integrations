#!/bin/bash
# CrowdStrike LogScale — post-deployment verification.
#
# Runs four checks in increasing depth. Each is reported independently so a
# partial failure tells you where the pipeline breaks:
#
#   1. Stack health       — CloudFormation stack exists and is in a good state
#   2. Real code deployed — the Lambda is not still the NotImplementedError stub
#   3. Live invocation    — invoke the shipper and inspect its response
#   4. Ingest reachability — send one synthetic event to the LogScale HEC endpoint
#
# Check 4 proves credentials and network reach LogScale. Check 3 proves the
# pipeline reads the S3 Access Point. Both matter: a passing ingest check with a
# failing invocation means the Lambda cannot see the audit volume.
#
# Usage:
#   export LOGSCALE_INGEST_TOKEN_SECRET_ID="fsxn-logscale-token"
#   export LOGSCALE_URL="https://cloud.us.humio.com"
#   bash integrations/crowdstrike/scripts/verify.sh
#
# Options:
#   --skip-invoke   Do not invoke the Lambda (read-only checks 1, 2, 4)
#   -h, --help      Show usage
#
# Exit codes (BSD sysexits.h):
#   0  all checks passed
#   69 (EX_UNAVAILABLE) one or more checks failed
#   78 (EX_CONFIG)      required configuration missing
set -uo pipefail

# SECURITY: never enable xtrace — the ingest token would leak.

SKIP_INVOKE="false"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-invoke) SKIP_INVOKE="true"; shift ;;
    -h|--help)
      sed -n '2,27p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "Unknown option: $1 (try --help)"; exit 78 ;;
  esac
done

AWS_REGION="${AWS_REGION:-ap-northeast-1}"
STACK_PREFIX="${STACK_PREFIX:-fsxn-crowdstrike}"
STACK_NAME="${STACK_NAME:-${STACK_PREFIX}-integration}"
LAMBDA_NAME="${LAMBDA_NAME:-${STACK_NAME}-shipper}"
LOGSCALE_URL="${LOGSCALE_URL:-https://cloud.us.humio.com}"
HEC_PATH="${HEC_PATH:-/api/v1/ingest/hec}"
TOKEN_SECRET_ID="${LOGSCALE_INGEST_TOKEN_SECRET_ID:-fsxn-logscale-token}"

PASS=0
FAIL=0
report_pass() { echo "  ✅ PASS — $1"; PASS=$((PASS + 1)); }
report_fail() { echo "  ❌ FAIL — $1"; FAIL=$((FAIL + 1)); }
report_skip() { echo "  ⏭️  SKIP — $1"; }

echo "============================================================"
echo "FSx for ONTAP → CrowdStrike LogScale — Verification"
echo "============================================================"
echo "Region:   ${AWS_REGION}"
echo "Stack:    ${STACK_NAME}"
echo "Lambda:   ${LAMBDA_NAME}"
echo "LogScale: ${LOGSCALE_URL}"
echo "============================================================"
echo ""

# --- Check 1: CloudFormation stack health -----------------------------------
echo "[1/4] CloudFormation stack health"
STACK_STATUS=$(aws cloudformation describe-stacks \
  --stack-name "${STACK_NAME}" --region "${AWS_REGION}" \
  --query 'Stacks[0].StackStatus' --output text 2>/dev/null)

if [ -z "${STACK_STATUS}" ] || [ "${STACK_STATUS}" = "None" ]; then
  report_fail "stack ${STACK_NAME} not found — run scripts/deploy.sh first"
elif [[ "${STACK_STATUS}" == *COMPLETE ]] && [[ "${STACK_STATUS}" != *ROLLBACK* ]]; then
  report_pass "stack status ${STACK_STATUS}"
else
  report_fail "stack status ${STACK_STATUS} — check the CloudFormation events"
fi
echo ""

# --- Check 2: real handler code deployed ------------------------------------
# The template ships a placeholder that only raises NotImplementedError. Its
# code package is a few hundred bytes; the real handler is tens of KB.
echo "[2/4] Real Lambda code deployed (not the placeholder)"
CODE_SIZE=$(aws lambda get-function-configuration \
  --function-name "${LAMBDA_NAME}" --region "${AWS_REGION}" \
  --query 'CodeSize' --output text 2>/dev/null)

if [ -z "${CODE_SIZE}" ] || [ "${CODE_SIZE}" = "None" ]; then
  report_fail "function ${LAMBDA_NAME} not found"
elif [ "${CODE_SIZE}" -lt 2000 ]; then
  report_fail "code size ${CODE_SIZE} bytes — placeholder is still deployed."
  echo "         Fix: bash $(dirname "${BASH_SOURCE[0]}")/deploy.sh --code-only"
else
  report_pass "code size ${CODE_SIZE} bytes"
fi
echo ""

# --- Check 3: live Lambda invocation ----------------------------------------
echo "[3/4] Live shipper invocation"
if [ "${SKIP_INVOKE}" = "true" ]; then
  report_skip "--skip-invoke requested"
else
  AUDIT_PREFIX=$(aws lambda get-function-configuration \
    --function-name "${LAMBDA_NAME}" --region "${AWS_REGION}" \
    --query 'Environment.Variables.AUDIT_LOG_PREFIX' --output text 2>/dev/null)
  [ "${AUDIT_PREFIX}" = "None" ] && AUDIT_PREFIX=""

  RESPONSE_FILE="$(mktemp)"
  INVOKE_STATUS=$(aws lambda invoke \
    --function-name "${LAMBDA_NAME}" \
    --region "${AWS_REGION}" \
    --cli-binary-format raw-in-base64-out \
    --payload "{\"source\":\"scheduler\",\"action\":\"process_audit_logs\",\"prefix\":\"${AUDIT_PREFIX}\"}" \
    --query 'FunctionError' --output text "${RESPONSE_FILE}" 2>/dev/null)

  RESPONSE_BODY=$(cat "${RESPONSE_FILE}" 2>/dev/null)
  rm -f "${RESPONSE_FILE}"

  if [ "${INVOKE_STATUS}" != "None" ] && [ -n "${INVOKE_STATUS}" ]; then
    report_fail "Lambda returned ${INVOKE_STATUS}"
    echo "         Response: ${RESPONSE_BODY}"
    echo "         Logs: aws logs tail /aws/lambda/${LAMBDA_NAME} --since 5m"
  elif echo "${RESPONSE_BODY}" | grep -q '"statusCode": *200'; then
    NEW_FILES=$(echo "${RESPONSE_BODY}" | sed -n 's/.*"new_files": *\([0-9]*\).*/\1/p')
    SHIPPED=$(echo "${RESPONSE_BODY}" | sed -n 's/.*"total_shipped": *\([0-9]*\).*/\1/p')
    report_pass "invocation succeeded (new_files=${NEW_FILES:-0}, shipped=${SHIPPED:-0})"
    if [ "${NEW_FILES:-0}" = "0" ]; then
      echo "         Note: 0 new files is expected if the checkpoint is already"
      echo "         current. Generate file activity on the audited volume, wait"
      echo "         for ONTAP to rotate the audit log, then re-run."
    fi
  elif echo "${RESPONSE_BODY}" | grep -q '"statusCode": *207'; then
    report_fail "partial failure (207) — some files could not be shipped"
    echo "         Response: ${RESPONSE_BODY}"
  else
    report_fail "unexpected response"
    echo "         Response: ${RESPONSE_BODY}"
  fi
fi
echo ""

# --- Check 4: LogScale HEC reachability -------------------------------------
echo "[4/4] LogScale HEC reachability"
TOKEN=$(aws secretsmanager get-secret-value \
  --secret-id "${TOKEN_SECRET_ID}" --region "${AWS_REGION}" \
  --query 'SecretString' --output text 2>/dev/null)

if [ -z "${TOKEN}" ]; then
  report_fail "could not read secret ${TOKEN_SECRET_ID}"
  echo "         Set LOGSCALE_INGEST_TOKEN_SECRET_ID to the secret name or ARN."
else
  # Support both plain-string and JSON secret formats
  if echo "${TOKEN}" | grep -q '"ingest_token"'; then
    TOKEN=$(echo "${TOKEN}" | sed -n 's/.*"ingest_token" *: *"\([^"]*\)".*/\1/p')
  elif echo "${TOKEN}" | grep -q '"token"'; then
    TOKEN=$(echo "${TOKEN}" | sed -n 's/.*"token" *: *"\([^"]*\)".*/\1/p')
  fi

  HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
    -X POST "${LOGSCALE_URL}${HEC_PATH}" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${TOKEN}" \
    -d '{
          "time": '"$(date +%s)"',
          "host": "verify-svm",
          "source": "fsxn-ontap",
          "sourcetype": "fsxn:audit",
          "event": {
            "timestamp": "'"$(date -u +%Y-%m-%dT%H:%M:%SZ)"'",
            "event_type": "4663",
            "svm": "verify-svm",
            "user": "CORP\\verify-test",
            "operation": "ReadData",
            "path": "/vol/audit/verify-ok.txt",
            "client_ip": "198.51.100.1",
            "result": "Audit Success"
          }
        }')

  if [ "${HTTP_CODE}" = "200" ]; then
    report_pass "HEC accepted the test event (HTTP 200)"
  else
    report_fail "HEC returned HTTP ${HTTP_CODE}"
    case "${HTTP_CODE}" in
      401|403) echo "         Invalid ingest token, or it lacks ingest permission." ;;
      404) echo "         Wrong HEC_PATH (current: ${HEC_PATH}) or LOGSCALE_URL." ;;
      000) echo "         No network path to ${LOGSCALE_URL} from this host." ;;
    esac
  fi
fi
echo ""

# --- Summary ----------------------------------------------------------------
echo "============================================================"
echo "Result: ${PASS} passed, ${FAIL} failed"
echo "============================================================"
if [ "${FAIL}" -gt 0 ]; then
  echo ""
  echo "Troubleshooting: integrations/crowdstrike/docs/en/setup-guide.md"
  exit 69
fi
echo ""
echo "Confirm in LogScale:"
echo "  #repo = \"fsxn_audit\" | user = \"CORP\\\\verify-test\""
