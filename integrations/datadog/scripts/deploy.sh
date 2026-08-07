#!/bin/bash
# Deploy the FSx for ONTAP → Datadog integration stacks.
#
# The CloudFormation templates ship with placeholder Lambda code (CloudFormation
# cannot inline a multi-hundred-line handler). This script closes that gap: it
# deploys the stacks AND uploads the real handler code, so the pipeline is
# actually functional when the script finishes.
#
# What it deploys:
#   1. Audit log stack (template.yaml)          → S3 AP → Scheduler → Lambda → Datadog
#   2. EMS + FPolicy stack (template-ems-fpolicy.yaml)  [--all only]
#   3. Real Lambda code for every deployed function
#
# Prerequisites:
#   - AWS CLI v2 configured with CloudFormation / Lambda / IAM permissions
#   - Datadog API key stored in Secrets Manager
#   - FSx for ONTAP S3 Access Point attached to the audit volume
#     (see docs/{en,ja}/setup-guide.md Step 2)
#   - For --all: FPolicy shared infra deployed (shared/templates/fpolicy-apigw.yaml)
#
# Usage:
#   export DATADOG_API_KEY_SECRET_ARN="arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:fsxn-datadog-api-key-XXXXXX"
#   export FSX_S3_ACCESS_POINT_ARN="arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit-ap"
#   bash integrations/datadog/scripts/deploy.sh
#
# Options:
#   --audit-only   Deploy only the audit log poller (default)
#   --all          Also deploy the EMS + FPolicy stack
#   --code-only    Skip CloudFormation; only re-upload Lambda code
#   -h, --help     Show usage
set -euo pipefail

# SECURITY: never enable xtrace — secret ARNs and parameter values would leak.
# Do not add 'set -x' to this script.

# --- Parse arguments --------------------------------------------------------
DEPLOY_MODE="audit-only"
CODE_ONLY="false"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --audit-only) DEPLOY_MODE="audit-only"; shift ;;
    --all)        DEPLOY_MODE="all"; shift ;;
    --code-only)  CODE_ONLY="true"; shift ;;
    -h|--help)
      sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "Unknown option: $1 (try --help)"; exit 2 ;;
  esac
done

# --- Configuration (override via environment variables) ---------------------
AWS_REGION="${AWS_REGION:-ap-northeast-1}"
STACK_PREFIX="${STACK_PREFIX:-fsxn-datadog}"
AUDIT_STACK="${STACK_PREFIX}-integration"
EMS_FPOLICY_STACK="${STACK_PREFIX}-ems-fpolicy"

# Required
DATADOG_API_KEY_SECRET_ARN="${DATADOG_API_KEY_SECRET_ARN:-}"
FSX_S3_ACCESS_POINT_ARN="${FSX_S3_ACCESS_POINT_ARN:-}"

# Optional — defaults match the CloudFormation template defaults
DATADOG_SITE="${DATADOG_SITE:-ap1.datadoghq.com}"
AUDIT_LOG_PREFIX="${AUDIT_LOG_PREFIX:-audit/}"
SCHEDULE_RATE="${SCHEDULE_RATE:-rate(5 minutes)}"
MAX_KEYS_PER_RUN="${MAX_KEYS_PER_RUN:-100}"
LOG_LEVEL="${LOG_LEVEL:-INFO}"
DD_ENV="${DD_ENV:-production}"
ENABLE_GZIP="${ENABLE_GZIP:-false}"
LAMBDA_MEMORY="${LAMBDA_MEMORY:-256}"
LAMBDA_TIMEOUT="${LAMBDA_TIMEOUT:-300}"
ALARM_TOPIC_ARN="${ALARM_TOPIC_ARN:-}"

# VPC (only needed when the Lambda must also reach the ONTAP REST API).
# See the network constraints table in AGENTS.md before enabling this.
VPC_ENABLED="${VPC_ENABLED:-false}"
VPC_SUBNET_IDS="${VPC_SUBNET_IDS:-}"
VPC_SECURITY_GROUP_IDS="${VPC_SECURITY_GROUP_IDS:-}"

# EMS / FPolicy (--all only)
EMS_PARSER_LAYER_ARN="${EMS_PARSER_LAYER_ARN:-}"
FPOLICY_SQS_QUEUE_ARN="${FPOLICY_SQS_QUEUE_ARN:-}"
FPOLICY_EVENT_BUS="${FPOLICY_EVENT_BUS:-default}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INTEGRATION_DIR="$(dirname "$SCRIPT_DIR")"
SHARED_PYTHON_DIR="$(cd "$(dirname "${INTEGRATION_DIR}")/../shared/python" && pwd)"

# --- Validation -------------------------------------------------------------
validate_required() {
  local var_name="$1"
  if [ -z "${!var_name:-}" ]; then
    echo "  ❌ $var_name is required but not set."
    echo "       export $var_name=\"<value>\""
    return 1
  fi
}

echo "============================================================"
echo "FSx for ONTAP → Datadog Integration Deployment"
echo "============================================================"
echo "Region:        ${AWS_REGION}"
echo "Mode:          ${DEPLOY_MODE}$([ "${CODE_ONLY}" = "true" ] && echo " (code-only)")"
echo "Audit stack:   ${AUDIT_STACK}"
[ "${DEPLOY_MODE}" = "all" ] && echo "EMS/FPolicy:   ${EMS_FPOLICY_STACK}"
echo "Datadog site:  ${DATADOG_SITE}"
echo "============================================================"
echo ""

ERRORS=0
if [ "${CODE_ONLY}" != "true" ]; then
  validate_required "DATADOG_API_KEY_SECRET_ARN" || ERRORS=$((ERRORS + 1))
  validate_required "FSX_S3_ACCESS_POINT_ARN" || ERRORS=$((ERRORS + 1))
  if [ "${VPC_ENABLED}" = "true" ]; then
    validate_required "VPC_SUBNET_IDS" || ERRORS=$((ERRORS + 1))
    validate_required "VPC_SECURITY_GROUP_IDS" || ERRORS=$((ERRORS + 1))
  fi
fi
if [ "${ERRORS}" -gt 0 ]; then
  echo ""
  echo "Set the required environment variables and re-run."
  echo "See docs/en/setup-guide.md Step 3 for how to obtain each value."
  exit 2
fi

# --- Step 1: Audit log stack ------------------------------------------------
if [ "${CODE_ONLY}" != "true" ]; then
  echo "--- Deploying audit log stack: ${AUDIT_STACK} ---"

  AUDIT_PARAMS=(
    "FsxS3AccessPointArn=${FSX_S3_ACCESS_POINT_ARN}"
    "DatadogApiKeySecretArn=${DATADOG_API_KEY_SECRET_ARN}"
    "DatadogSite=${DATADOG_SITE}"
    "AuditLogPrefix=${AUDIT_LOG_PREFIX}"
    "ScheduleRate=${SCHEDULE_RATE}"
    "MaxKeysPerRun=${MAX_KEYS_PER_RUN}"
    "LogLevel=${LOG_LEVEL}"
    "Environment=${DD_ENV}"
    "EnableGzip=${ENABLE_GZIP}"
    "LambdaMemorySize=${LAMBDA_MEMORY}"
    "LambdaTimeout=${LAMBDA_TIMEOUT}"
    "VpcEnabled=${VPC_ENABLED}"
  )
  [ -n "${ALARM_TOPIC_ARN}" ] && AUDIT_PARAMS+=("AlarmNotificationTopicArn=${ALARM_TOPIC_ARN}")
  if [ "${VPC_ENABLED}" = "true" ]; then
    AUDIT_PARAMS+=("VpcSubnetIds=${VPC_SUBNET_IDS}" "VpcSecurityGroupIds=${VPC_SECURITY_GROUP_IDS}")
  fi

  aws cloudformation deploy \
    --template-file "${INTEGRATION_DIR}/template.yaml" \
    --stack-name "${AUDIT_STACK}" \
    --capabilities CAPABILITY_NAMED_IAM \
    --region "${AWS_REGION}" \
    --parameter-overrides "${AUDIT_PARAMS[@]}" \
    --no-fail-on-empty-changeset
  echo "  ✅ Audit stack deployed"
  echo ""
fi

# --- Step 2: EMS + FPolicy stack -------------------------------------------
if [ "${DEPLOY_MODE}" = "all" ] && [ "${CODE_ONLY}" != "true" ]; then
  echo "--- Deploying EMS + FPolicy stack: ${EMS_FPOLICY_STACK} ---"

  if [ -z "${FPOLICY_SQS_QUEUE_ARN}" ]; then
    echo "  ⚠️  FPOLICY_SQS_QUEUE_ARN is empty — the SQS trigger (primary FPolicy"
    echo "      path) will NOT be created. Only the EventBridge path will work."
    echo "      Get the ARN from the fpolicy-apigw.yaml stack outputs."
  fi

  EMS_FPOLICY_PARAMS=(
    "DatadogApiKeySecretArn=${DATADOG_API_KEY_SECRET_ARN}"
    "DatadogSite=${DATADOG_SITE}"
    "EventBridgeBusName=${FPOLICY_EVENT_BUS}"
    "LogLevel=${LOG_LEVEL}"
    "Environment=${DD_ENV}"
    "EnableGzip=${ENABLE_GZIP}"
  )
  [ -n "${EMS_PARSER_LAYER_ARN}" ] && EMS_FPOLICY_PARAMS+=("EmsParserLayerArn=${EMS_PARSER_LAYER_ARN}")
  [ -n "${FPOLICY_SQS_QUEUE_ARN}" ] && EMS_FPOLICY_PARAMS+=("FPolicySqsQueueArn=${FPOLICY_SQS_QUEUE_ARN}")
  [ -n "${ALARM_TOPIC_ARN}" ] && EMS_FPOLICY_PARAMS+=("AlarmNotificationTopicArn=${ALARM_TOPIC_ARN}")

  aws cloudformation deploy \
    --template-file "${INTEGRATION_DIR}/template-ems-fpolicy.yaml" \
    --stack-name "${EMS_FPOLICY_STACK}" \
    --capabilities CAPABILITY_NAMED_IAM \
    --region "${AWS_REGION}" \
    --parameter-overrides "${EMS_FPOLICY_PARAMS[@]}" \
    --no-fail-on-empty-changeset
  echo "  ✅ EMS + FPolicy stack deployed"
  echo ""
fi

# --- Step 3: Upload the real Lambda code -----------------------------------
# Without this step the functions only raise NotImplementedError.
echo "--- Uploading Lambda function code ---"
cd "${INTEGRATION_DIR}/lambda"

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "${WORK_DIR}"' EXIT

upload_handler() {
  local source_file="$1"
  local function_name="$2"
  local label="$3"
  # Only the audit shipper reads audit files, so only it needs the ONTAP parser.
  local extra_files=()
  if [ "${source_file}" = "handler.py" ]; then
    extra_files+=("${SHARED_PYTHON_DIR}/ontap_audit_parser.py")
  fi

  if ! aws lambda get-function --function-name "${function_name}" \
       --region "${AWS_REGION}" >/dev/null 2>&1; then
    echo "  ⏭️  ${label}: function ${function_name} not found — skipping"
    echo "       (deploy its stack first, or check STACK_PREFIX)"
    return 0
  fi

  local zip_path="${WORK_DIR}/${source_file%.py}.zip"
  zip -q -j "${zip_path}" "${source_file}" "${extra_files[@]+"${extra_files[@]}"}"
  aws lambda update-function-code \
    --function-name "${function_name}" \
    --zip-file "fileb://${zip_path}" \
    --region "${AWS_REGION}" >/dev/null

  # Block until the new code is active, otherwise a verify run immediately
  # afterwards can still hit the placeholder.
  aws lambda wait function-updated \
    --function-name "${function_name}" \
    --region "${AWS_REGION}"
  echo "  ✅ ${label}: ${function_name}"
}

upload_handler "handler.py" "${AUDIT_STACK}-shipper" "Audit shipper"
if [ "${DEPLOY_MODE}" = "all" ]; then
  upload_handler "ems_handler.py" "${EMS_FPOLICY_STACK}-ems" "EMS handler"
  upload_handler "fpolicy_handler.py" "${EMS_FPOLICY_STACK}-fpolicy" "FPolicy handler"
fi
echo ""

# --- Summary ----------------------------------------------------------------
CHECKPOINT_PARAM=$(aws cloudformation describe-stacks \
  --stack-name "${AUDIT_STACK}" --region "${AWS_REGION}" \
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
if [ "${CODE_ONLY}" = "true" ]; then
  echo "Stacks were left untouched; only the Lambda code was replaced."
else
  echo "Deployed:"
  echo "  - ${AUDIT_STACK} (audit log → Datadog, every ${SCHEDULE_RATE})"
  [ "${DEPLOY_MODE}" = "all" ] && echo "  - ${EMS_FPOLICY_STACK} (EMS + FPolicy → Datadog)"
fi
echo ""
echo "Checkpoint parameter: ${CHECKPOINT_PARAM}"
echo "  Reset it to '__INIT__' to re-process the whole prefix (duplicates logs)."
echo ""
echo "Next steps:"
echo "  1. Verify delivery end to end:"
echo "       bash ${SCRIPT_DIR}/verify.sh"
echo "  2. Create the Datadog log pipeline, facets, monitors and dashboard:"
echo "       bash ${SCRIPT_DIR}/setup-full-observability.sh"
if [ "${DEPLOY_MODE}" = "all" ]; then
  echo "  3. Deploy the EMS API Gateway and point ONTAP at it:"
  echo "       shared/templates/ems-webhook-apigw.yaml"
  echo "       (LambdaFunctionArn = ${EMS_FPOLICY_STACK} output EmsLambdaFunctionArn)"
  echo "  4. Point the ONTAP FPolicy external engine at the Fargate task IP:"
  echo "       bash shared/scripts/fpolicy-update-engine-ip.sh --auto"
fi
echo ""
echo "Docs: integrations/datadog/docs/en/setup-guide.md"
