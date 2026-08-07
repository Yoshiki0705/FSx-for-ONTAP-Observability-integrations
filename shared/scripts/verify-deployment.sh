#!/bin/bash
# ============================================================================
# Shared AWS-side deployment verification for a vendor integration.
#
# Most vendors' scripts/verify.sh only proved that credentials and the network
# reach the vendor's ingest endpoint. That passes even when the stack was never
# deployed, or — far more commonly — when the placeholder Lambda is still in
# place because the handler was never uploaded. This script covers the AWS side
# so the two together mean something.
#
# Checks:
#   1. CloudFormation stack exists and is in a healthy state
#   2. The deployed Lambda is the real handler, not the NotImplementedError stub
#   3. The EventBridge Scheduler schedule (if the stack has one) is ENABLED
#   4. The SSM checkpoint parameter (if the stack has one) exists
#
# Checks 3 and 4 are skipped, not failed, when the stack does not create those
# resources — vendors on the EventBridge-rule trigger model have neither.
#
# Usage (from a vendor verify.sh):
#   STACK_NAME=fsxn-dynatrace-integration \
#   LAMBDA_NAME=fsxn-dynatrace-integration-shipper \
#   bash shared/scripts/verify-deployment.sh
#
# Environment variables:
#   STACK_NAME    (required) CloudFormation stack to inspect
#   LAMBDA_NAME   (required) Lambda function to inspect
#   AWS_REGION    AWS region (default: ap-northeast-1)
#   DEPLOY_HINT   Command suggested when the placeholder is still deployed
#
# Exit codes (BSD sysexits.h):
#   0  all applicable checks passed
#   69 (EX_UNAVAILABLE) one or more checks failed
#   78 (EX_CONFIG)      required configuration missing
# ============================================================================

set -euo pipefail

AWS_REGION="${AWS_REGION:-ap-northeast-1}"
STACK_NAME="${STACK_NAME:-}"
LAMBDA_NAME="${LAMBDA_NAME:-}"
DEPLOY_HINT="${DEPLOY_HINT:-bash scripts/deploy.sh}"

if [ -z "$STACK_NAME" ] || [ -z "$LAMBDA_NAME" ]; then
  echo "ERROR: STACK_NAME and LAMBDA_NAME are required." >&2
  exit 78
fi

FAILED=0
pass() { echo "  ✅ PASS — $1"; }
fail() { echo "  ❌ FAIL — $1"; FAILED=$((FAILED + 1)); }
skip() { echo "  ⏭️  SKIP — $1"; }

echo "--- AWS deployment checks ---"
echo "  Region: ${AWS_REGION}"
echo "  Stack:  ${STACK_NAME}"
echo "  Lambda: ${LAMBDA_NAME}"
echo ""

# --- Check 1: stack health --------------------------------------------------
echo "[1/4] CloudFormation stack health"
STACK_STATUS=$(aws cloudformation describe-stacks \
  --stack-name "${STACK_NAME}" --region "${AWS_REGION}" \
  --query 'Stacks[0].StackStatus' --output text 2>/dev/null || echo "")

if [ -z "${STACK_STATUS}" ] || [ "${STACK_STATUS}" = "None" ]; then
  fail "stack ${STACK_NAME} not found — run ${DEPLOY_HINT} first"
elif [[ "${STACK_STATUS}" == *COMPLETE ]] && [[ "${STACK_STATUS}" != *ROLLBACK* ]]; then
  pass "stack status ${STACK_STATUS}"
else
  fail "stack status ${STACK_STATUS} — check the CloudFormation events"
fi
echo ""

# --- Check 2: real handler code ---------------------------------------------
# The placeholder package is a few hundred bytes; the real handler is tens of KB.
echo "[2/4] Real Lambda code deployed (not the placeholder)"
CODE_SIZE=$(aws lambda get-function-configuration \
  --function-name "${LAMBDA_NAME}" --region "${AWS_REGION}" \
  --query 'CodeSize' --output text 2>/dev/null || echo "")

if [ -z "${CODE_SIZE}" ] || [ "${CODE_SIZE}" = "None" ]; then
  fail "function ${LAMBDA_NAME} not found"
elif [ "${CODE_SIZE}" -lt 2000 ]; then
  fail "code size ${CODE_SIZE} bytes — the NotImplementedError placeholder is still deployed."
  echo "         Every invocation fails and no telemetry is delivered."
  echo "         Fix: ${DEPLOY_HINT}"
else
  pass "code size ${CODE_SIZE} bytes"
fi
echo ""

# --- Check 3: scheduler enabled ---------------------------------------------
echo "[3/4] EventBridge Scheduler schedule enabled"
SCHEDULE_NAME=$(aws cloudformation describe-stack-resources \
  --stack-name "${STACK_NAME}" --region "${AWS_REGION}" \
  --query "StackResources[?ResourceType=='AWS::Scheduler::Schedule'].PhysicalResourceId | [0]" \
  --output text 2>/dev/null || echo "")

if [ -z "${SCHEDULE_NAME}" ] || [ "${SCHEDULE_NAME}" = "None" ]; then
  skip "this stack does not create an EventBridge Scheduler schedule"
else
  SCHEDULE_STATE=$(aws scheduler get-schedule \
    --name "${SCHEDULE_NAME}" --region "${AWS_REGION}" \
    --query 'State' --output text 2>/dev/null || echo "")
  if [ "${SCHEDULE_STATE}" = "ENABLED" ]; then
    pass "schedule ${SCHEDULE_NAME} is ENABLED"
  else
    fail "schedule ${SCHEDULE_NAME} state is ${SCHEDULE_STATE:-unknown} — nothing will poll"
  fi
fi
echo ""

# --- Check 4: checkpoint parameter ------------------------------------------
echo "[4/4] SSM checkpoint parameter present"
PARAM_NAME=$(aws cloudformation describe-stack-resources \
  --stack-name "${STACK_NAME}" --region "${AWS_REGION}" \
  --query "StackResources[?ResourceType=='AWS::SSM::Parameter'].PhysicalResourceId | [0]" \
  --output text 2>/dev/null || echo "")

if [ -z "${PARAM_NAME}" ] || [ "${PARAM_NAME}" = "None" ]; then
  skip "this stack does not use an SSM checkpoint"
else
  CHECKPOINT=$(aws ssm get-parameter --name "${PARAM_NAME}" --region "${AWS_REGION}" \
    --query 'Parameter.Value' --output text 2>/dev/null || echo "")
  if [ -n "${CHECKPOINT}" ]; then
    pass "checkpoint ${PARAM_NAME} = ${CHECKPOINT}"
  else
    fail "checkpoint parameter ${PARAM_NAME} could not be read"
  fi
fi
echo ""

if [ "${FAILED}" -gt 0 ]; then
  echo "--- AWS deployment checks: ${FAILED} failed ---"
  exit 69
fi
echo "--- AWS deployment checks: all passed ---"
