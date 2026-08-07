#!/bin/bash
# Deploy the FSx for ONTAP → CrowdStrike Falcon LogScale integration.
#
# The CloudFormation template ships with placeholder Lambda code (CloudFormation
# cannot inline a multi-hundred-line handler). This script closes that gap: it
# deploys the stack AND uploads the real handler, so the pipeline is actually
# functional when the script finishes.
#
# Prerequisites:
#   - AWS CLI v2 with CloudFormation / Lambda / IAM permissions
#   - LogScale ingest token in Secrets Manager
#   - FSx for ONTAP S3 Access Point attached to the audit volume
#
# Usage:
#   export FSX_S3_ACCESS_POINT_ARN="arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit-ap"
#   export LOGSCALE_INGEST_TOKEN_SECRET_ARN="arn:aws:secretsmanager:...:secret:fsxn-logscale-token-XXXXXX"
#   bash integrations/crowdstrike/scripts/deploy.sh
#
# Options:
#   --code-only   Skip CloudFormation; only re-upload the Lambda code
#   -h, --help    Show usage
set -euo pipefail

# SECURITY: never enable xtrace — secret ARNs would leak.

CODE_ONLY="false"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --code-only) CODE_ONLY="true"; shift ;;
    -h|--help)
      sed -n '2,21p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "Unknown option: $1 (try --help)"; exit 2 ;;
  esac
done

STACK_NAME="${STACK_PREFIX:-fsxn-crowdstrike}-integration"
REGION="${AWS_REGION:-ap-northeast-1}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INTEGRATION_DIR="$(dirname "$SCRIPT_DIR")"
SHARED_PYTHON_DIR="$(cd "$(dirname "${INTEGRATION_DIR}")/../shared/python" && pwd)"
TEMPLATE="${INTEGRATION_DIR}/template.yaml"

echo "============================================================"
echo "FSx for ONTAP → CrowdStrike LogScale Deployment"
echo "============================================================"
echo "Region: ${REGION}"
echo "Stack:  ${STACK_NAME}$([ "${CODE_ONLY}" = "true" ] && echo "  (code-only)")"
echo "============================================================"
echo ""

if [ "${CODE_ONLY}" != "true" ]; then
  ERRORS=0
  for var in FSX_S3_ACCESS_POINT_ARN LOGSCALE_INGEST_TOKEN_SECRET_ARN; do
    if [ -z "${!var:-}" ]; then
      echo "  ❌ ${var} is required but not set."
      ERRORS=$((ERRORS + 1))
    fi
  done
  if [ "${ERRORS}" -gt 0 ]; then
    echo ""
    echo "See docs/en/setup-guide.md for how to obtain each value."
    exit 2
  fi

  echo "--- Deploying stack (3-5 minutes on first run) ---"
  PARAMS=(
    "FsxS3AccessPointArn=${FSX_S3_ACCESS_POINT_ARN}"
    "LogScaleIngestTokenSecretArn=${LOGSCALE_INGEST_TOKEN_SECRET_ARN}"
    "LogScaleUrl=${LOGSCALE_URL:-https://cloud.us.humio.com}"
    "AuditLogPrefix=${AUDIT_LOG_PREFIX:-audit/}"
    "ScheduleInterval=${SCHEDULE_INTERVAL:-rate(5 minutes)}"
    "MaxKeysPerRun=${MAX_KEYS_PER_RUN:-100}"
    "LogLevel=${LOG_LEVEL:-INFO}"
  )
  [ -n "${ALARM_TOPIC_ARN:-}" ] && PARAMS+=("AlarmNotificationTopicArn=${ALARM_TOPIC_ARN}")
  [ -n "${HEC_PATH:-}" ] && PARAMS+=("HecPath=${HEC_PATH}")

  aws cloudformation deploy \
    --template-file "${TEMPLATE}" \
    --stack-name "${STACK_NAME}" \
    --region "${REGION}" \
    --parameter-overrides "${PARAMS[@]}" \
    --capabilities CAPABILITY_NAMED_IAM \
    --no-fail-on-empty-changeset
  echo "  ✅ Stack deployed"
  echo ""
fi

# --- Upload the real Lambda code -------------------------------------------
# Without this the function only raises NotImplementedError.
echo "--- Uploading Lambda function code ---"
FUNCTION_NAME="${STACK_NAME}-shipper"

if ! aws lambda get-function --function-name "${FUNCTION_NAME}" \
     --region "${REGION}" >/dev/null 2>&1; then
  echo "  ❌ Function ${FUNCTION_NAME} not found — deploy the stack first."
  exit 1
fi

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "${WORK_DIR}"' EXIT

# Bundle the shared ONTAP audit parser alongside the handler. Without it the
# handler falls back to JSON-only parsing and XML/EVTX audit logs are not
# parsed into fields. -j flattens paths so the import resolves at runtime.
zip -q -j "${WORK_DIR}/handler.zip" \
  "${INTEGRATION_DIR}/lambda/handler.py" \
  "${SHARED_PYTHON_DIR}/ontap_audit_parser.py"

aws lambda update-function-code \
  --function-name "${FUNCTION_NAME}" \
  --zip-file "fileb://${WORK_DIR}/handler.zip" \
  --region "${REGION}" >/dev/null

# Block until the new code is active, otherwise a verify run immediately
# afterwards can still hit the placeholder.
aws lambda wait function-updated \
  --function-name "${FUNCTION_NAME}" --region "${REGION}"
echo "  ✅ ${FUNCTION_NAME}"
echo ""

CHECKPOINT_PARAM=$(aws cloudformation describe-stacks \
  --stack-name "${STACK_NAME}" --region "${REGION}" \
  --query "Stacks[0].Outputs[?OutputKey=='CheckpointParameterName'].OutputValue" \
  --output text 2>/dev/null || echo "unknown")

echo "============================================================"
if [ "${CODE_ONLY}" = "true" ]; then
  echo "Code Upload Complete"
else
  echo "Deployment Complete"
fi
echo "============================================================"
echo ""
echo "Checkpoint parameter: ${CHECKPOINT_PARAM}"
echo "  Reset it to '__INIT__' to re-process the whole prefix (duplicates events)."
echo ""
echo "Next step — verify delivery end to end:"
echo "  bash ${SCRIPT_DIR}/verify.sh"
echo ""
echo "Docs: integrations/crowdstrike/docs/en/setup-guide.md"
