#!/bin/bash
# Deploy all New Relic integration stacks for FSx for ONTAP.
#
# This script deploys:
#   1. Main audit log stack (template.yaml)
#   2. EMS webhook stack (template-ems.yaml)
#   3. FPolicy stack (template-fpolicy.yaml)
#   4. Updates Lambda function code for all handlers
#
# Prerequisites:
#   - AWS CLI v2 configured with appropriate permissions
#   - New Relic License Key stored in Secrets Manager
#   - S3 Access Point created (see docs/en/prerequisites.md)
#
# Usage:
#   export NR_SECRET_ARN="arn:aws:secretsmanager:..."
#   export S3_ACCESS_POINT_ARN="arn:aws:s3:..."
#   export S3_BUCKET_NAME="your-fsxn-audit-log-bucket"
#   bash integrations/new-relic/scripts/deploy.sh
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
      echo "  --audit-only    Deploy only the audit log poller"
      echo "  --all           Deploy all stacks: audit + EMS + FPolicy (default)"
      echo ""
      echo "Environment variables (required):"
      echo "  NR_SECRET_ARN         Secrets Manager ARN for New Relic License Key"
      echo "  S3_ACCESS_POINT_ARN   FSx for ONTAP S3 Access Point ARN"
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
STACK_PREFIX="${STACK_PREFIX:-fsxn-new-relic}"

NR_SECRET_ARN="${NR_SECRET_ARN:-}"
S3_ACCESS_POINT_ARN="${S3_ACCESS_POINT_ARN:-}"
S3_BUCKET_NAME="${S3_BUCKET_NAME:-}"
NR_REGION="${NR_REGION:-US}"
NR_ENDPOINT="${NR_ENDPOINT:-https://log-api.newrelic.com/log/v1}"
LOG_LEVEL="${LOG_LEVEL:-INFO}"
LAMBDA_MEMORY="${LAMBDA_MEMORY:-256}"
LAMBDA_TIMEOUT="${LAMBDA_TIMEOUT:-300}"
FPOLICY_EVENT_BUS="${FPOLICY_EVENT_BUS:-fsxn-fpolicy-events}"

ALARM_TOPIC_ARN="${ALARM_TOPIC_ARN:-}"

FPOLICY_SQS_QUEUE_ARN="${FPOLICY_SQS_QUEUE_ARN:-}"

# Passed to the FPolicy stack only when set. With it, the primary SQS trigger
# path is created; without it the stack uses the EventBridge rule only.
FPOLICY_SQS_PARAMS=()
[ -n "${FPOLICY_SQS_QUEUE_ARN}" ] && \
  FPOLICY_SQS_PARAMS+=("FPolicySqsQueueArn=${FPOLICY_SQS_QUEUE_ARN}")

# Appended to every stack's --parameter-overrides only when set, so the
# templates fall back to their empty default (alarms created without actions).
ALARM_PARAMS=()
[ -n "${ALARM_TOPIC_ARN}" ] && ALARM_PARAMS+=("AlarmNotificationTopicArn=${ALARM_TOPIC_ARN}")

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INTEGRATION_DIR="$(dirname "$SCRIPT_DIR")"
SHARED_PYTHON_DIR="$(cd "$(dirname "${INTEGRATION_DIR}")/../shared/python" && pwd)"

# Telemetry paths that exist as CloudFormation templates but have no handler
# source in this vendor directory. Reported again at the end so the skip is not
# lost in the deploy output.
SKIPPED_PATHS=()

validate_required() {
  local var_name="$1"
  local var_value="${!var_name:-}"
  if [ -z "$var_value" ]; then
    echo "ERROR: $var_name is required but not set."
    return 1
  fi
}

echo "=== FSx for ONTAP New Relic Integration Deployment ==="
echo "Region: ${AWS_REGION} | Stack prefix: ${STACK_PREFIX} | NR Region: ${NR_REGION}"

ERRORS=0
validate_required "NR_SECRET_ARN" || ERRORS=$((ERRORS + 1))
validate_required "S3_ACCESS_POINT_ARN" || ERRORS=$((ERRORS + 1))
validate_required "S3_BUCKET_NAME" || ERRORS=$((ERRORS + 1))

if [ "$ERRORS" -gt 0 ]; then
  echo "Set the required environment variables and re-run."
  exit 1
fi

# --- Step 1: Main audit log stack ---
echo "--- Step 1: Deploying main audit log stack ---"
aws cloudformation deploy \
  --template-file "${INTEGRATION_DIR}/template.yaml" \
  --stack-name "${STACK_PREFIX}-integration" \
  --capabilities CAPABILITY_IAM \
  --region "${AWS_REGION}" \
  --parameter-overrides \
    S3AccessPointArn="${S3_ACCESS_POINT_ARN}" \
    NewRelicLicenseKeySecretArn="${NR_SECRET_ARN}" \
    NewRelicRegion="${NR_REGION}" \
    S3BucketName="${S3_BUCKET_NAME}" \
    LogLevel="${LOG_LEVEL}" \
    LambdaMemorySize="${LAMBDA_MEMORY}" \
    LambdaTimeout="${LAMBDA_TIMEOUT}" \
    "${ALARM_PARAMS[@]+"${ALARM_PARAMS[@]}"}" \
  --no-fail-on-empty-changeset
echo "  ✅ Main stack: ${STACK_PREFIX}-integration"

if [ "$DEPLOY_MODE" = "all" ]; then

# --- Step 2: EMS stack ---
if [ -f "${INTEGRATION_DIR}/lambda/ems_handler.py" ]; then
  echo "--- Step 2: Deploying EMS webhook stack ---"
  aws cloudformation deploy \
    --template-file "${INTEGRATION_DIR}/template-ems.yaml" \
    --stack-name "${STACK_PREFIX}-ems" \
    --capabilities CAPABILITY_NAMED_IAM \
    --region "${AWS_REGION}" \
    --parameter-overrides \
      NewRelicLicenseKeySecretArn="${NR_SECRET_ARN}" \
      NewRelicEndpoint="${NR_ENDPOINT}" \
      LogLevel="${LOG_LEVEL}" \
      "${ALARM_PARAMS[@]+"${ALARM_PARAMS[@]}"}" \
    --no-fail-on-empty-changeset
  echo "  ✅ EMS stack: ${STACK_PREFIX}-ems"
else
  SKIPPED_PATHS+=("EMS")
  echo "--- Step 2: Skipping EMS webhook stack ---"
  echo "  ⚠️  lambda/ems_handler.py does not exist for this vendor."
  echo "      template-ems.yaml only defines a placeholder that raises"
  echo "      NotImplementedError, so deploying it would silently discard"
  echo "      every EMS event sent to it. Not deploying is the safer default."
fi

# --- Step 3: FPolicy stack ---
if [ -f "${INTEGRATION_DIR}/lambda/fpolicy_handler.py" ]; then
  echo "--- Step 3: Deploying FPolicy stack ---"
  aws cloudformation deploy \
    --template-file "${INTEGRATION_DIR}/template-fpolicy.yaml" \
    --stack-name "${STACK_PREFIX}-fpolicy" \
    --capabilities CAPABILITY_NAMED_IAM \
    --region "${AWS_REGION}" \
    --parameter-overrides \
      NewRelicLicenseKeySecretArn="${NR_SECRET_ARN}" \
      NewRelicEndpoint="${NR_ENDPOINT}" \
      EventBusName="${FPOLICY_EVENT_BUS}" \
      LogLevel="${LOG_LEVEL}" \
      "${FPOLICY_SQS_PARAMS[@]+"${FPOLICY_SQS_PARAMS[@]}"}" \
      "${ALARM_PARAMS[@]+"${ALARM_PARAMS[@]}"}" \
    --no-fail-on-empty-changeset
  echo "  ✅ FPolicy stack: ${STACK_PREFIX}-fpolicy"
else
  SKIPPED_PATHS+=("FPolicy")
  echo "--- Step 3: Skipping FPolicy stack ---"
  echo "  ⚠️  lambda/fpolicy_handler.py does not exist for this vendor."
  echo "      template-fpolicy.yaml only defines a placeholder that raises"
  echo "      NotImplementedError, so deploying it would silently discard"
  echo "      every FPolicy event sent to it. Not deploying is the safer default."
fi

fi

# --- Step 4: Update Lambda code ---
echo "--- Step 4: Updating Lambda function code ---"
cd "${INTEGRATION_DIR}/lambda"

# Bundle the shared ONTAP audit parser alongside the handler. Without it the
# handler falls back to JSON-only parsing and XML/EVTX audit logs are not
# parsed into fields. -j flattens paths so the import resolves at runtime.
zip -q -j /tmp/nr-handler.zip handler.py "${SHARED_PYTHON_DIR}/ontap_audit_parser.py"
aws lambda update-function-code \
  --function-name "${STACK_PREFIX}-integration-shipper" \
  --zip-file fileb:///tmp/nr-handler.zip \
  --region "${AWS_REGION}" > /dev/null
rm -f /tmp/nr-handler.zip
echo "  ✅ Main handler updated"

if [ "$DEPLOY_MODE" = "all" ]; then
  # Guarded on file existence: zip exits non-zero on a missing file, which
  # under `set -e` aborted the script here after the stacks were already
  # created — leaving a half-deployed system and a confusing error.
  if [ -f ems_handler.py ]; then
    # ems_handler imports the shared plumbing from shared/python, so those
    # modules must travel in the same zip. -j flattens paths for the import.
    zip -q -j /tmp/nr-ems-handler.zip ems_handler.py \
      "${SHARED_PYTHON_DIR}/ems_event.py" "${SHARED_PYTHON_DIR}/vendor_shipper.py"
    aws lambda update-function-code \
      --function-name "${STACK_PREFIX}-ems-ems-handler" \
      --zip-file fileb:///tmp/nr-ems-handler.zip \
      --region "${AWS_REGION}" > /dev/null
    aws lambda wait function-updated \
      --function-name "${STACK_PREFIX}-ems-ems-handler" --region "${AWS_REGION}"
    rm -f /tmp/nr-ems-handler.zip
    echo "  ✅ EMS handler updated"
  fi

  if [ -f fpolicy_handler.py ]; then
    zip -q -j /tmp/nr-fpolicy-handler.zip fpolicy_handler.py \
      "${SHARED_PYTHON_DIR}/fpolicy_event.py" "${SHARED_PYTHON_DIR}/vendor_shipper.py"
    aws lambda update-function-code \
      --function-name "${STACK_PREFIX}-fpolicy-handler" \
      --zip-file fileb:///tmp/nr-fpolicy-handler.zip \
      --region "${AWS_REGION}" > /dev/null
    aws lambda wait function-updated \
      --function-name "${STACK_PREFIX}-fpolicy-handler" --region "${AWS_REGION}"
    rm -f /tmp/nr-fpolicy-handler.zip
    echo "  ✅ FPolicy handler updated"
  fi
fi

echo ""

if [ ${#SKIPPED_PATHS[@]} -gt 0 ]; then
  echo "=== Not deployed: ${SKIPPED_PATHS[*]} ==="
  echo "The audit log path is deployed and working. The paths listed above have"
  echo "no handler implementation for this vendor yet — see the integration"
  echo "README for the current telemetry path coverage."
  echo ""
fi

echo "=== Done ==="
echo "Verify: SELECT count(*) FROM Log WHERE source='fsxn-ontap' SINCE 15 minutes ago"
