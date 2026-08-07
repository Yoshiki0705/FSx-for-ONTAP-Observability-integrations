#!/bin/bash
# Deploy all Splunk Serverless integration stacks for FSx for ONTAP.
#
# This script deploys:
#   1. Main audit log stack (template.yaml)
#   2. Updates Lambda function code
#
# Prerequisites:
#   - AWS CLI v2 configured with appropriate permissions
#   - Splunk HEC token stored in Secrets Manager
#   - S3 Access Point created (see docs/en/prerequisites.md)
#
# Usage:
#   export SPLUNK_SECRET_ARN="arn:aws:secretsmanager:..."
#   export S3_ACCESS_POINT_ARN="arn:aws:s3:..."
#   export SPLUNK_HEC_ENDPOINT="https://splunk.example.com:8088"
#   export S3_BUCKET_NAME="your-fsxn-audit-log-bucket"
#   bash integrations/splunk-serverless/scripts/deploy.sh
#
# Options:
#   --audit-only    Deploy only the audit log poller (template.yaml)
#   --all           Deploy all stacks (default)

set -euo pipefail

DEPLOY_MODE="all"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --audit-only) DEPLOY_MODE="audit-only"; shift ;;
    --all)        DEPLOY_MODE="all"; shift ;;
    -h|--help)
      echo "Usage: bash deploy.sh [OPTIONS]"
      echo ""
      echo "Options:"
      echo "  --audit-only    Deploy only the audit log poller (template.yaml)"
      echo "  --all           Deploy all stacks: audit + Firehose (default)"
      echo ""
      echo "Environment variables (required):"
      echo "  SPLUNK_SECRET_ARN     Secrets Manager ARN for Splunk HEC token"
      echo "  S3_ACCESS_POINT_ARN   FSx for ONTAP S3 Access Point ARN"
      echo "  SPLUNK_HEC_ENDPOINT   Splunk HEC endpoint URL"
      echo "  S3_BUCKET_NAME        S3 bucket name for audit logs"
      echo ""
      echo "Optional env vars:"
      echo "  ALARM_TOPIC_ARN       SNS topic ARN notified when this stack's"
      echo "                        CloudWatch alarms fire. Unset means the alarms"
      echo "                        are created without notification actions."
      exit 0
      ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

AWS_REGION="${AWS_REGION:-ap-northeast-1}"
STACK_PREFIX="${STACK_PREFIX:-fsxn-splunk}"

SPLUNK_SECRET_ARN="${SPLUNK_SECRET_ARN:-}"
S3_ACCESS_POINT_ARN="${S3_ACCESS_POINT_ARN:-}"
SPLUNK_HEC_ENDPOINT="${SPLUNK_HEC_ENDPOINT:-}"
S3_BUCKET_NAME="${S3_BUCKET_NAME:-}"

SPLUNK_INDEX="${SPLUNK_INDEX:-fsxn_audit}"
SPLUNK_SOURCETYPE="${SPLUNK_SOURCETYPE:-fsxn:ontap:audit}"
LOG_LEVEL="${LOG_LEVEL:-INFO}"
LAMBDA_MEMORY="${LAMBDA_MEMORY:-256}"
LAMBDA_TIMEOUT="${LAMBDA_TIMEOUT:-300}"

ALARM_TOPIC_ARN="${ALARM_TOPIC_ARN:-}"

# Appended to every stack's --parameter-overrides only when set, so the
# templates fall back to their empty default (alarms created without actions).
ALARM_PARAMS=()
[ -n "${ALARM_TOPIC_ARN}" ] && ALARM_PARAMS+=("AlarmNotificationTopicArn=${ALARM_TOPIC_ARN}")

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INTEGRATION_DIR="$(dirname "$SCRIPT_DIR")"
SHARED_PYTHON_DIR="$(cd "$(dirname "${INTEGRATION_DIR}")/../shared/python" && pwd)"

validate_required() {
  local var_name="$1"
  local var_value="${!var_name:-}"
  if [ -z "$var_value" ]; then
    echo "ERROR: $var_name is required but not set."
    return 1
  fi
}

echo "=== FSx for ONTAP Splunk Serverless Deployment ==="
echo "Region: ${AWS_REGION} | Stack prefix: ${STACK_PREFIX}"

ERRORS=0
validate_required "SPLUNK_SECRET_ARN" || ERRORS=$((ERRORS + 1))
validate_required "S3_ACCESS_POINT_ARN" || ERRORS=$((ERRORS + 1))
validate_required "SPLUNK_HEC_ENDPOINT" || ERRORS=$((ERRORS + 1))
validate_required "S3_BUCKET_NAME" || ERRORS=$((ERRORS + 1))

if [ "$ERRORS" -gt 0 ]; then
  echo "Set the required environment variables and re-run."
  exit 1
fi

echo "--- Deploying main audit log stack ---"
aws cloudformation deploy \
  --template-file "${INTEGRATION_DIR}/template.yaml" \
  --stack-name "${STACK_PREFIX}-integration" \
  --capabilities CAPABILITY_IAM \
  --region "${AWS_REGION}" \
  --parameter-overrides \
    S3AccessPointArn="${S3_ACCESS_POINT_ARN}" \
    SplunkHecTokenSecretArn="${SPLUNK_SECRET_ARN}" \
    SplunkHecEndpoint="${SPLUNK_HEC_ENDPOINT}" \
    S3BucketName="${S3_BUCKET_NAME}" \
    SplunkIndex="${SPLUNK_INDEX}" \
    SplunkSourcetype="${SPLUNK_SOURCETYPE}" \
    LogLevel="${LOG_LEVEL}" \
    LambdaMemorySize="${LAMBDA_MEMORY}" \
    LambdaTimeout="${LAMBDA_TIMEOUT}" \
    "${ALARM_PARAMS[@]+"${ALARM_PARAMS[@]}"}" \
  --no-fail-on-empty-changeset

echo "  ✅ Main stack deployed: ${STACK_PREFIX}-integration"

if [ "$DEPLOY_MODE" = "all" ] && [ -f "${INTEGRATION_DIR}/template-firehose.yaml" ]; then
  echo "--- Deploying Firehose stack ---"
  aws cloudformation deploy \
    --template-file "${INTEGRATION_DIR}/template-firehose.yaml" \
    --stack-name "${STACK_PREFIX}-firehose" \
    --capabilities CAPABILITY_IAM \
    --region "${AWS_REGION}" \
    --parameter-overrides \
      SplunkHecTokenSecretArn="${SPLUNK_SECRET_ARN}" \
      SplunkHecEndpoint="${SPLUNK_HEC_ENDPOINT}" \
      SplunkIndex="${SPLUNK_INDEX}" \
      "${ALARM_PARAMS[@]+"${ALARM_PARAMS[@]}"}" \
    --no-fail-on-empty-changeset
  echo "  ✅ Firehose stack deployed: ${STACK_PREFIX}-firehose"
fi

echo "--- Updating Lambda function code ---"
cd "${INTEGRATION_DIR}/lambda"
# Bundle the shared ONTAP audit parser alongside the handler. Without it the
# handler falls back to JSON-only parsing and XML/EVTX audit logs are not
# parsed into fields. -j flattens paths so the import resolves at runtime.
zip -q -j /tmp/splunk-handler.zip handler.py "${SHARED_PYTHON_DIR}/ontap_audit_parser.py"
aws lambda update-function-code \
  --function-name "${STACK_PREFIX}-integration-shipper" \
  --zip-file fileb:///tmp/splunk-handler.zip \
  --region "${AWS_REGION}" > /dev/null
rm -f /tmp/splunk-handler.zip
echo "  ✅ Handler updated"

echo ""
echo "=== Done ==="
echo "Verify: index=${SPLUNK_INDEX} sourcetype=${SPLUNK_SOURCETYPE} earliest=-15m"
