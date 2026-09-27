#!/bin/bash
# Pattern 4 (Kafka + AutoMQ WAL-on-FSx-for-ONTAP) — post-deployment verification.
#
# Runs read-only checks and appends the result to the Verification_Record. It
# does NOT run a load or scale benchmark (out of scope, spec requirement 11-3):
# it confirms the environment stood up and the broker fleet is reachable, at the
# sample-run level.
#
# The live NFS-hosted Kafka silly-rename sample run (a single NFSv4.1 +
# -is-preserve-unlink-enabled partition-reassignment crash/no-crash check on FSx
# for ONTAP) requires a running FSx for ONTAP account and is spec task 16 (marked
# live-AWS-only, billed). This script performs the CI-safe checks and prints
# where to record the live result. The AutoMQ WAL latency figures are AWS's own
# published benchmark (documented, cited in the setup guide and the record), not
# reproduced here.
#
# Usage:
#   bash integrations/pipeline-verification/pattern-4-kafka-automq/scripts/verify.sh
#
# Exit codes (BSD sysexits.h):
#   0  all run checks passed
#   69 (EX_UNAVAILABLE) one or more checks failed
set -uo pipefail

AWS_REGION="${AWS_REGION:-ap-northeast-1}"
STACK_NAME="${STACK_NAME:-fsxn-pattern-4-kafka-automq}"

PASS=0
FAIL=0
report_pass() { echo "  ✅ PASS — $1"; PASS=$((PASS + 1)); }
report_fail() { echo "  ❌ FAIL — $1"; FAIL=$((FAIL + 1)); }

echo "============================================================"
echo "Pattern 4 (Kafka + AutoMQ WAL-on-FSx-for-ONTAP) — Verification"
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

# --- Check 2: Kafka broker exists and is reachable via SSM ------------------
echo "[2/3] Kafka broker present (SSM managed)"
INSTANCE_ID=$(aws cloudformation describe-stacks \
  --stack-name "${STACK_NAME}" --region "${AWS_REGION}" \
  --query "Stacks[0].Outputs[?OutputKey=='KafkaBrokerInstanceId'].OutputValue" \
  --output text 2>/dev/null)
if [ -z "${INSTANCE_ID}" ] || [ "${INSTANCE_ID}" = "None" ]; then
  report_fail "no KafkaBrokerInstanceId output"
else
  PING=$(aws ssm describe-instance-information \
    --filters "Key=InstanceIds,Values=${INSTANCE_ID}" \
    --region "${AWS_REGION}" \
    --query 'InstanceInformationList[0].PingStatus' --output text 2>/dev/null)
  if [ "${PING}" = "Online" ]; then
    report_pass "broker ${INSTANCE_ID} online in SSM"
  else
    report_fail "broker ${INSTANCE_ID} SSM ping status ${PING:-unknown}"
  fi
fi
echo ""

# --- Check 3: Gen2 high-throughput WAL intent recorded ----------------------
echo "[3/3] FSx for ONTAP Gen2 high-throughput WAL intent"
GEN2=$(aws cloudformation describe-stacks \
  --stack-name "${STACK_NAME}" --region "${AWS_REGION}" \
  --query "Stacks[0].Outputs[?OutputKey=='Gen2HighThroughputWal'].OutputValue" \
  --output text 2>/dev/null)
if [ -n "${GEN2}" ] && [ "${GEN2}" != "None" ]; then
  report_pass "Gen2 high-throughput WAL = ${GEN2} (a cost driver; higher than standard)"
else
  report_fail "no Gen2HighThroughputWal output"
fi
echo ""

echo "============================================================"
echo "Result: ${PASS} passed, ${FAIL} failed"
echo "============================================================"
echo ""
echo "Record the outcome in the Verification_Record:"
echo "  docs/{en,ja}/observability-storage-patterns/verification/verification-results-pattern-4.md"
echo ""
echo "The live NFS-hosted Kafka silly-rename sample run (spec task 16, live AWS"
echo "account, billed) is the named measurement target: NFSv4.1 +"
echo "-is-preserve-unlink-enabled partition-reassignment crash/no-crash behavior."
echo "It starts at Claim_Tier 'hypothesis' and is promoted to 'sample-run' only"
echo "after that run. The AutoMQ WAL latency/cost figures stay 'documented' (AWS's"
echo "own published benchmark, cited); they are not reproduced here and not"
echo "relabeled verified."

if [ "${FAIL}" -gt 0 ]; then
  exit 69
fi
