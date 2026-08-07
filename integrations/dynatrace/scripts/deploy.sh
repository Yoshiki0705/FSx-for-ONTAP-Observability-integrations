#!/bin/bash
# Deploy Dynatrace integration stacks for FSx for ONTAP.
set -euo pipefail

DEPLOY_MODE="all"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --audit-only) DEPLOY_MODE="audit-only"; shift ;;
    --all) DEPLOY_MODE="all"; shift ;;
    -h|--help)
      echo "Usage: bash deploy.sh [--audit-only|--all]"
      echo ""
      echo "Required env vars:"
      echo "  DT_SECRET_ARN    Secrets Manager ARN"
      echo "  S3_ACCESS_POINT_ARN   FSx for ONTAP S3 Access Point ARN"
      echo "  S3_BUCKET_NAME        S3 bucket name"
      echo "  DT_ENV_URL             Dynatrace environment URL"
      echo ""
      echo "Optional env vars:"
      echo "  ALARM_TOPIC_ARN       SNS topic ARN notified when this stack's"
      echo "                        CloudWatch alarms fire. Unset means the alarms"
      echo "                        are created without notification actions."
      exit 0 ;;
    *) echo "Unknown: $1"; exit 1 ;;
  esac
done

AWS_REGION="${AWS_REGION:-ap-northeast-1}"
STACK_PREFIX="${STACK_PREFIX:-fsxn-dynatrace}"
DT_SECRET_ARN="${DT_SECRET_ARN:-}"
S3_ACCESS_POINT_ARN="${S3_ACCESS_POINT_ARN:-}"
S3_BUCKET_NAME="${S3_BUCKET_NAME:-}"
DT_ENV_URL="${DT_ENV_URL:-}"
LOG_LEVEL="${LOG_LEVEL:-INFO}"
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

echo "=== FSx for ONTAP Dynatrace Deployment ==="
for var in DT_SECRET_ARN S3_ACCESS_POINT_ARN S3_BUCKET_NAME; do
  if [ -z "${!var:-}" ]; then echo "ERROR: $var not set"; exit 1; fi
done

echo "--- Deploying main audit log stack ---"
aws cloudformation deploy \
  --template-file "${INTEGRATION_DIR}/template.yaml" \
  --stack-name "${STACK_PREFIX}-integration" \
  --capabilities CAPABILITY_IAM --region "${AWS_REGION}" \
  --parameter-overrides \
    S3AccessPointArn="${S3_ACCESS_POINT_ARN}" \
    DynatraceApiTokenSecretArn="${DT_SECRET_ARN}" \
    S3BucketName="${S3_BUCKET_NAME}" \
    DynatraceEnvUrl="${DT_ENV_URL}" \
    LogLevel="${LOG_LEVEL}" \
    "${ALARM_PARAMS[@]+"${ALARM_PARAMS[@]}"}" \
  --no-fail-on-empty-changeset
echo "  ✅ Main stack: ${STACK_PREFIX}-integration"

if [ "$DEPLOY_MODE" = "all" ]; then
  if [ -f "${INTEGRATION_DIR}/lambda/ems_handler.py" ]; then
    echo "--- Deploying EMS stack ---"
    aws cloudformation deploy \
      --template-file "${INTEGRATION_DIR}/template-ems.yaml" \
      --stack-name "${STACK_PREFIX}-ems" \
      --capabilities CAPABILITY_NAMED_IAM --region "${AWS_REGION}" \
      --parameter-overrides \
        DynatraceApiTokenSecretArn="${DT_SECRET_ARN}" \
        DynatraceEnvUrl="${DT_ENV_URL}" LogLevel="${LOG_LEVEL}" \
        "${ALARM_PARAMS[@]+"${ALARM_PARAMS[@]}"}" \
      --no-fail-on-empty-changeset
    echo "  ✅ EMS stack: ${STACK_PREFIX}-ems"
  else
    SKIPPED_PATHS+=("EMS")
    echo "--- Skipping EMS stack ---"
    echo "  ⚠️  lambda/ems_handler.py does not exist for this vendor."
    echo "      template-ems.yaml only defines a placeholder that raises"
    echo "      NotImplementedError, so deploying it would silently discard"
    echo "      every EMS event sent to it. Not deploying is the safer default."
  fi

  if [ -f "${INTEGRATION_DIR}/lambda/fpolicy_handler.py" ]; then
    echo "--- Deploying FPolicy stack ---"
    aws cloudformation deploy \
      --template-file "${INTEGRATION_DIR}/template-fpolicy.yaml" \
      --stack-name "${STACK_PREFIX}-fpolicy" \
      --capabilities CAPABILITY_NAMED_IAM --region "${AWS_REGION}" \
      --parameter-overrides \
        DynatraceApiTokenSecretArn="${DT_SECRET_ARN}" \
        DynatraceEnvUrl="${DT_ENV_URL}" EventBusName="${FPOLICY_EVENT_BUS}" LogLevel="${LOG_LEVEL}" \
        "${FPOLICY_SQS_PARAMS[@]+"${FPOLICY_SQS_PARAMS[@]}"}" \
        "${ALARM_PARAMS[@]+"${ALARM_PARAMS[@]}"}" \
      --no-fail-on-empty-changeset
    echo "  ✅ FPolicy stack: ${STACK_PREFIX}-fpolicy"
  else
    SKIPPED_PATHS+=("FPolicy")
    echo "--- Skipping FPolicy stack ---"
    echo "  ⚠️  lambda/fpolicy_handler.py does not exist for this vendor."
    echo "      template-fpolicy.yaml only defines a placeholder that raises"
    echo "      NotImplementedError, so deploying it would silently discard"
    echo "      every FPolicy event sent to it. Not deploying is the safer default."
  fi
fi

echo "--- Updating Lambda code ---"
cd "${INTEGRATION_DIR}/lambda"
# Bundle the shared ONTAP audit parser alongside the handler. Without it the
# handler falls back to JSON-only parsing and XML/EVTX audit logs are not
# parsed into fields. -j flattens paths so the import resolves at runtime.
zip -q -j /tmp/dynatrace-handler.zip handler.py "${SHARED_PYTHON_DIR}/ontap_audit_parser.py"
aws lambda update-function-code --function-name "${STACK_PREFIX}-integration-shipper" \
  --zip-file fileb:///tmp/dynatrace-handler.zip --region "${AWS_REGION}" > /dev/null
rm -f /tmp/dynatrace-handler.zip
echo "  ✅ Handler updated"

# EMS and FPolicy handlers import the shared plumbing from shared/python, so the
# modules must travel in the same zip. -j flattens paths so the imports resolve.
if [ -f ems_handler.py ]; then
  zip -q -j /tmp/dynatrace-ems.zip ems_handler.py \
    "${SHARED_PYTHON_DIR}/ems_event.py" "${SHARED_PYTHON_DIR}/vendor_shipper.py"
  aws lambda update-function-code \
    --function-name "${STACK_PREFIX}-ems-ems-handler" \
    --zip-file fileb:///tmp/dynatrace-ems.zip --region "${AWS_REGION}" > /dev/null
  aws lambda wait function-updated \
    --function-name "${STACK_PREFIX}-ems-ems-handler" --region "${AWS_REGION}"
  rm -f /tmp/dynatrace-ems.zip
  echo "  ✅ EMS handler updated"
fi

if [ -f fpolicy_handler.py ]; then
  zip -q -j /tmp/dynatrace-fpolicy.zip fpolicy_handler.py \
    "${SHARED_PYTHON_DIR}/fpolicy_event.py" "${SHARED_PYTHON_DIR}/vendor_shipper.py"
  aws lambda update-function-code \
    --function-name "${STACK_PREFIX}-fpolicy-handler" \
    --zip-file fileb:///tmp/dynatrace-fpolicy.zip --region "${AWS_REGION}" > /dev/null
  aws lambda wait function-updated \
    --function-name "${STACK_PREFIX}-fpolicy-handler" --region "${AWS_REGION}"
  rm -f /tmp/dynatrace-fpolicy.zip
  echo "  ✅ FPolicy handler updated"
fi
echo ""

if [ ${#SKIPPED_PATHS[@]} -gt 0 ]; then
  echo "=== Not deployed: ${SKIPPED_PATHS[*]} ==="
  echo "The audit log path is deployed and working. The paths listed above have"
  echo "no handler implementation for this vendor yet — see the integration"
  echo "README for the current telemetry path coverage."
  echo ""
fi

echo '=== Done === Check Dynatrace: fetch logs | filter log.source == "fsxn-ontap"'
