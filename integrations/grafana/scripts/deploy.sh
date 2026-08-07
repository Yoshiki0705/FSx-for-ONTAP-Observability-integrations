#!/bin/bash
# Deploy all Grafana Cloud integration stacks for FSx for ONTAP.
#
# This script deploys:
#   1. Main audit log stack (template.yaml)
#   2. EMS webhook stack (template-ems.yaml) + EMS parser Lambda Layer
#   3. FPolicy stack (template-fpolicy.yaml)
#   4. Updates Lambda function code for all handlers
#
# Prerequisites:
#   - AWS CLI v2 configured with appropriate permissions
#   - Grafana Cloud credentials stored in Secrets Manager
#   - S3 Access Point created (see docs/ja/prerequisites.md)
#   - FPolicy shared infra deployed (shared/templates/fpolicy-apigw.yaml)
#
# Usage:
#   export GRAFANA_SECRET_ARN="arn:aws:secretsmanager:..."
#   export S3_ACCESS_POINT_ARN="arn:aws:s3:..."
#   export LOKI_ENDPOINT="https://otlp-gateway-prod-..."
#   export S3_BUCKET_NAME="your-fsxn-audit-log-bucket"
#   bash integrations/grafana/scripts/deploy.sh
#
# Options:
#   --audit-only    Deploy only the audit log poller (template.yaml)
#   --all           Deploy all stacks (default)
#
# All parameters can be overridden via environment variables.

set -euo pipefail

# --- Parse arguments --------------------------------------------------------

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
      echo "  --all           Deploy all stacks: audit + EMS + FPolicy (default)"
      echo ""
      echo "Environment variables (required):"
      echo "  GRAFANA_SECRET_ARN    Secrets Manager ARN for Grafana credentials"
      echo "  S3_ACCESS_POINT_ARN   FSx for ONTAP S3 Access Point ARN"
      echo "  LOKI_ENDPOINT         Grafana Cloud OTLP Gateway URL"
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

# --- Configuration (override via environment variables) ---------------------

AWS_REGION="${AWS_REGION:-ap-northeast-1}"
STACK_PREFIX="${STACK_PREFIX:-fsxn-grafana}"

# Required parameters (no defaults — must be set by user)
GRAFANA_SECRET_ARN="${GRAFANA_SECRET_ARN:-}"
S3_ACCESS_POINT_ARN="${S3_ACCESS_POINT_ARN:-}"
LOKI_ENDPOINT="${LOKI_ENDPOINT:-}"
S3_BUCKET_NAME="${S3_BUCKET_NAME:-}"

# Optional parameters
S3_KEY_PREFIX="${S3_KEY_PREFIX:-}"
LOG_LEVEL="${LOG_LEVEL:-INFO}"
LAMBDA_MEMORY="${LAMBDA_MEMORY:-256}"
LAMBDA_TIMEOUT="${LAMBDA_TIMEOUT:-300}"
EMS_PARSER_LAYER_ARN="${EMS_PARSER_LAYER_ARN:-}"
FPOLICY_EVENT_BUS="${FPOLICY_EVENT_BUS:-fsxn-fpolicy-events}"

ALARM_TOPIC_ARN="${ALARM_TOPIC_ARN:-}"

# Appended to every stack's --parameter-overrides only when set, so the
# templates fall back to their empty default (alarms created without actions).
ALARM_PARAMS=()
[ -n "${ALARM_TOPIC_ARN}" ] && ALARM_PARAMS+=("AlarmNotificationTopicArn=${ALARM_TOPIC_ARN}")

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INTEGRATION_DIR="$(dirname "$SCRIPT_DIR")"
SHARED_PYTHON_DIR="$(cd "$(dirname "${INTEGRATION_DIR}")/../shared/python" && pwd)"

# --- Validation -------------------------------------------------------------

validate_required() {
  local var_name="$1"
  local var_value="${!var_name:-}"
  if [ -z "$var_value" ]; then
    echo "ERROR: $var_name is required but not set."
    echo "  export $var_name=\"<value>\""
    return 1
  fi
}

echo "=== FSx for ONTAP Grafana Cloud Integration Deployment ==="
echo "Region: ${AWS_REGION}"
echo "Stack prefix: ${STACK_PREFIX}"
echo ""

ERRORS=0
validate_required "GRAFANA_SECRET_ARN" || ERRORS=$((ERRORS + 1))
validate_required "S3_ACCESS_POINT_ARN" || ERRORS=$((ERRORS + 1))
validate_required "LOKI_ENDPOINT" || ERRORS=$((ERRORS + 1))
validate_required "S3_BUCKET_NAME" || ERRORS=$((ERRORS + 1))

if [ "$ERRORS" -gt 0 ]; then
  echo ""
  echo "Set the required environment variables and re-run."
  exit 1
fi

echo "Grafana Secret ARN: ${GRAFANA_SECRET_ARN}"
echo "S3 Access Point: ${S3_ACCESS_POINT_ARN}"
echo "Loki Endpoint: ${LOKI_ENDPOINT}"
echo "S3 Bucket: ${S3_BUCKET_NAME}"
echo ""

# --- Step 1: Deploy main audit log stack -----------------------------------

echo "--- Step 1/4: Deploying main audit log stack ---"

aws cloudformation deploy \
  --template-file "${INTEGRATION_DIR}/template.yaml" \
  --stack-name "${STACK_PREFIX}-integration" \
  --capabilities CAPABILITY_IAM \
  --region "${AWS_REGION}" \
  --parameter-overrides \
    S3AccessPointArn="${S3_ACCESS_POINT_ARN}" \
    GrafanaCredentialsSecretArn="${GRAFANA_SECRET_ARN}" \
    LokiEndpoint="${LOKI_ENDPOINT}" \
    S3BucketName="${S3_BUCKET_NAME}" \
    S3KeyPrefix="${S3_KEY_PREFIX}" \
    LogLevel="${LOG_LEVEL}" \
    LambdaMemorySize="${LAMBDA_MEMORY}" \
    LambdaTimeout="${LAMBDA_TIMEOUT}" \
    "${ALARM_PARAMS[@]+"${ALARM_PARAMS[@]}"}" \
  --no-fail-on-empty-changeset

echo "  ✅ Main stack deployed: ${STACK_PREFIX}-integration"

# --- Step 2: Deploy EMS stack (skip if --audit-only) ------------------------

if [ "$DEPLOY_MODE" = "all" ]; then

echo "--- Step 2/4: Deploying EMS webhook stack ---"

EMS_PARAMS=(
  "GrafanaCredentialsSecretArn=${GRAFANA_SECRET_ARN}"
  "LokiEndpoint=${LOKI_ENDPOINT}"
  "LogLevel=${LOG_LEVEL}"
)

if [ -n "${EMS_PARSER_LAYER_ARN}" ]; then
  EMS_PARAMS+=("EmsParserLayerArn=${EMS_PARSER_LAYER_ARN}")
fi

aws cloudformation deploy \
  --template-file "${INTEGRATION_DIR}/template-ems.yaml" \
  --stack-name "${STACK_PREFIX}-ems" \
  --capabilities CAPABILITY_NAMED_IAM \
  --region "${AWS_REGION}" \
  --parameter-overrides "${EMS_PARAMS[@]}" \
  "${ALARM_PARAMS[@]+"${ALARM_PARAMS[@]}"}" \
  --no-fail-on-empty-changeset

echo "  ✅ EMS stack deployed: ${STACK_PREFIX}-ems"

# --- Step 3: Deploy FPolicy stack ------------------------------------------

echo "--- Step 3/4: Deploying FPolicy stack ---"

aws cloudformation deploy \
  --template-file "${INTEGRATION_DIR}/template-fpolicy.yaml" \
  --stack-name "${STACK_PREFIX}-fpolicy" \
  --capabilities CAPABILITY_NAMED_IAM \
  --region "${AWS_REGION}" \
  --parameter-overrides \
    GrafanaCredentialsSecretArn="${GRAFANA_SECRET_ARN}" \
    LokiEndpoint="${LOKI_ENDPOINT}" \
    EventBusName="${FPOLICY_EVENT_BUS}" \
    LogLevel="${LOG_LEVEL}" \
    "${ALARM_PARAMS[@]+"${ALARM_PARAMS[@]}"}" \
  --no-fail-on-empty-changeset

echo "  ✅ FPolicy stack deployed: ${STACK_PREFIX}-fpolicy"

fi  # end DEPLOY_MODE=all

# --- Step 4: Update Lambda function code -----------------------------------

echo "--- Step 4/4: Updating Lambda function code ---"

cd "${INTEGRATION_DIR}/lambda"

# Main handler
echo "  Updating main handler..."
# Bundle the shared ONTAP audit parser alongside the handler. Without it the
# handler falls back to JSON-only parsing and XML/EVTX audit logs are not
# parsed into fields. -j flattens paths so the import resolves at runtime.
zip -q -j /tmp/grafana-handler.zip handler.py "${SHARED_PYTHON_DIR}/ontap_audit_parser.py"
aws lambda update-function-code \
  --function-name "${STACK_PREFIX}-integration-shipper" \
  --zip-file fileb:///tmp/grafana-handler.zip \
  --region "${AWS_REGION}" > /dev/null
rm -f /tmp/grafana-handler.zip
echo "  ✅ Main handler updated"

if [ "$DEPLOY_MODE" = "all" ]; then

# EMS handler
echo "  Updating EMS handler..."
zip -q /tmp/grafana-ems-handler.zip ems_handler.py
aws lambda update-function-code \
  --function-name "${STACK_PREFIX}-ems-ems-handler" \
  --zip-file fileb:///tmp/grafana-ems-handler.zip \
  --region "${AWS_REGION}" > /dev/null
rm -f /tmp/grafana-ems-handler.zip
echo "  ✅ EMS handler updated"

# FPolicy handler
echo "  Updating FPolicy handler..."
zip -q /tmp/grafana-fpolicy-handler.zip fpolicy_handler.py
aws lambda update-function-code \
  --function-name "${STACK_PREFIX}-fpolicy-handler" \
  --zip-file fileb:///tmp/grafana-fpolicy-handler.zip \
  --region "${AWS_REGION}" > /dev/null
rm -f /tmp/grafana-fpolicy-handler.zip
echo "  ✅ FPolicy handler updated"

fi  # end DEPLOY_MODE=all

# --- Summary ----------------------------------------------------------------

echo ""
echo "=== Deployment Complete ==="
echo ""
echo "Stacks deployed:"
echo "  1. ${STACK_PREFIX}-integration (audit log -> Loki)"
echo "  2. ${STACK_PREFIX}-ems (EMS webhook -> Loki)"
echo "  3. ${STACK_PREFIX}-fpolicy (FPolicy -> Loki)"
echo ""
echo "Next steps:"
echo "  - Deploy API Gateway for EMS: shared/templates/ems-webhook-apigw.yaml"
echo "  - Configure ONTAP EMS webhook destination"
echo "  - Configure ONTAP FPolicy external engine"
echo "  - Verify logs in Grafana Explore: {job=\"fsxn-audit\"}"
