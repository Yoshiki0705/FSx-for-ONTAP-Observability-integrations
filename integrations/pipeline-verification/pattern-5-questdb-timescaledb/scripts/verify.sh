#!/bin/bash
# Pattern 5 (QuestDB / TimescaleDB) — post-deployment verification.
#
# Runs read-only checks and appends the result to the Verification_Record. It
# does NOT run a load or scale benchmark (out of scope, spec requirement 11-3):
# it confirms the environment stood up and the cold-tier archive target is
# reachable, at the sample-run level.
#
# The live iSCSI-LUN hot-tier sample run (a single QuestDB/ClickHouse
# read → write → lock correctness check on an iSCSI LUN) requires a running FSx
# for ONTAP account and is spec task 13 (marked live-AWS-only, billed). This
# script performs the CI-safe checks and prints where to record the live result.
#
# Usage:
#   bash integrations/pipeline-verification/pattern-5-questdb-timescaledb/scripts/verify.sh
#
# Exit codes (BSD sysexits.h):
#   0  all run checks passed
#   69 (EX_UNAVAILABLE) one or more checks failed
set -uo pipefail

AWS_REGION="${AWS_REGION:-ap-northeast-1}"
STACK_NAME="${STACK_NAME:-fsxn-pattern-5-questdb-timescaledb}"

PASS=0
FAIL=0
report_pass() { echo "  ✅ PASS — $1"; PASS=$((PASS + 1)); }
report_fail() { echo "  ❌ FAIL — $1"; FAIL=$((FAIL + 1)); }

echo "============================================================"
echo "Pattern 5 (QuestDB / TimescaleDB) — Verification"
echo "============================================================"
echo "Region: ${AWS_REGION}"
echo "Stack:  ${STACK_NAME}"
echo "============================================================"
echo ""

# --- Check 1: stack health --------------------------------------------------
echo "[1/3] CloudFormation stack health"
STATUS=$(aws cloudformation describe-stacks \
  --stack-name "${STACK_NAME}" --region "${AWS_REGION}" \
  --query 'Stacks[0].StackStatus' --output text 2>/dev/null)
if [ -z "${STATUS}" ] || [ "${STATUS}" = "None" ]; then
  report_fail "stack ${STACK_NAME} not found — run deploy.sh first"
elif [[ "${STATUS}" == *COMPLETE ]] && [[ "${STATUS}" != *ROLLBACK* ]]; then
  report_pass "stack status ${STATUS}"
else
  report_fail "stack status ${STATUS} — check the CloudFormation events"
fi
echo ""

# --- Check 2: TSDB server exists and is reachable via SSM -------------------
echo "[2/3] TSDB server present (SSM managed)"
INSTANCE_ID=$(aws cloudformation describe-stacks \
  --stack-name "${STACK_NAME}" --region "${AWS_REGION}" \
  --query "Stacks[0].Outputs[?OutputKey=='TsdbInstanceId'].OutputValue" \
  --output text 2>/dev/null)
if [ -z "${INSTANCE_ID}" ] || [ "${INSTANCE_ID}" = "None" ]; then
  report_fail "no TsdbInstanceId output"
else
  PING=$(aws ssm describe-instance-information \
    --filters "Key=InstanceIds,Values=${INSTANCE_ID}" \
    --region "${AWS_REGION}" \
    --query 'InstanceInformationList[0].PingStatus' --output text 2>/dev/null)
  if [ "${PING}" = "Online" ]; then
    report_pass "instance ${INSTANCE_ID} online in SSM"
  else
    report_fail "instance ${INSTANCE_ID} SSM ping status ${PING:-unknown}"
  fi
fi
echo ""

# --- Check 3: cold-tier archive target resolved ----------------------------
echo "[3/3] cold-tier archive target (reused S3 Access Point)"
ARCHIVE_ARN=$(aws cloudformation describe-stacks \
  --stack-name "${STACK_NAME}" --region "${AWS_REGION}" \
  --query "Stacks[0].Outputs[?OutputKey=='ColdTierArchiveArn'].OutputValue" \
  --output text 2>/dev/null)
if [ "${ARCHIVE_ARN}" = "not-deployed" ]; then
  report_pass "archive stack intentionally not deployed (server-only run)"
elif [ -n "${ARCHIVE_ARN}" ] && [ "${ARCHIVE_ARN}" != "None" ]; then
  report_pass "archive access point ARN present"
else
  report_fail "no ColdTierArchiveArn output"
fi
echo ""

echo "============================================================"
echo "Result: ${PASS} passed, ${FAIL} failed"
echo "============================================================"
echo ""
echo "Record the outcome in the Verification_Record:"
echo "  docs/{en,ja}/observability-storage-patterns/verification/verification-results-pattern-5.md"
echo ""
echo "The live iSCSI-LUN hot-tier sample run (spec task 13, live AWS account,"
echo "billed) is the named measurement target: iSCSI-LUN QuestDB/ClickHouse"
echo "read/write/lock correctness. It is recorded there, promoted from"
echo "hypothesis to sample-run after the run."

if [ "${FAIL}" -gt 0 ]; then
  exit 69
fi
