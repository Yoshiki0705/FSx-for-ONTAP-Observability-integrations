#!/bin/bash
# Deploy the OTel Collector integration for FSx for ONTAP.
#
# Unlike the other vendors, this template takes the Lambda code from S3
# (LambdaCodeS3Bucket / LambdaCodeS3Key) rather than an inline placeholder. All
# three shipper functions share one package, so this script builds that package,
# uploads it, and then deploys the stack — in that order, because CloudFormation
# fails if the object does not exist yet.
#
# Usage:
#   export OTLP_ENDPOINT="http://collector.internal:4318"
#   export S3_BUCKET_NAME="my-fsxn-audit-logs"
#   export LAMBDA_CODE_S3_BUCKET="my-lambda-artifacts"
#   bash integrations/otel-collector/scripts/deploy.sh
#
# Options:
#   --code-only   Rebuild and upload the package, then update the three
#                 functions in place. Does not touch the stack.
#   -h, --help    Show this help

set -euo pipefail

CODE_ONLY=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --code-only) CODE_ONLY=true; shift ;;
    -h|--help)
      echo "Usage: bash deploy.sh [--code-only]"
      echo ""
      echo "Environment variables (required):"
      echo "  OTLP_ENDPOINT           OTLP/HTTP endpoint of the Collector"
      echo "  S3_BUCKET_NAME          S3 bucket holding the audit log files"
      echo "  LAMBDA_CODE_S3_BUCKET   S3 bucket to upload the Lambda package to"
      echo ""
      echo "Optional env vars:"
      echo "  LAMBDA_CODE_S3_KEY      Package key (default: otel-collector/lambda.zip)"
      echo "  API_KEY_SECRET_ARN      Secrets Manager ARN when the endpoint needs auth"
      echo "  AUTH_MODE               none|bearer|basic|header (default: none)"
      echo "  ALARM_TOPIC_ARN         SNS topic ARN notified when this stack's"
      echo "                          CloudWatch alarms fire. Unset means the alarms"
      echo "                          are created without notification actions."
      echo "  STACK_PREFIX            Stack name prefix (default: fsxn-otel)"
      echo "  AWS_REGION              Region (default: ap-northeast-1)"
      exit 0 ;;
    *) echo "Unknown option: $1 (try --help)"; exit 1 ;;
  esac
done

AWS_REGION="${AWS_REGION:-ap-northeast-1}"
STACK_PREFIX="${STACK_PREFIX:-fsxn-otel}"
STACK_NAME="${STACK_NAME:-${STACK_PREFIX}-integration}"
OTLP_ENDPOINT="${OTLP_ENDPOINT:-}"
S3_BUCKET_NAME="${S3_BUCKET_NAME:-}"
LAMBDA_CODE_S3_BUCKET="${LAMBDA_CODE_S3_BUCKET:-}"
LAMBDA_CODE_S3_KEY="${LAMBDA_CODE_S3_KEY:-otel-collector/lambda.zip}"
API_KEY_SECRET_ARN="${API_KEY_SECRET_ARN:-}"
AUTH_MODE="${AUTH_MODE:-none}"
LOG_LEVEL="${LOG_LEVEL:-INFO}"
ALARM_TOPIC_ARN="${ALARM_TOPIC_ARN:-}"

# Appended to --parameter-overrides only when set, so the template falls back to
# its empty default (alarms created without actions).
ALARM_PARAMS=()
[ -n "${ALARM_TOPIC_ARN}" ] && ALARM_PARAMS+=("AlarmNotificationTopicArn=${ALARM_TOPIC_ARN}")

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INTEGRATION_DIR="$(dirname "$SCRIPT_DIR")"
SHARED_PYTHON_DIR="$(cd "$(dirname "${INTEGRATION_DIR}")/../shared/python" && pwd)"

# The three functions the template creates. Names are fixed in template.yaml.
FUNCTIONS=(
  "fsxn-otel-log-shipper"
  "fsxn-otel-ems-shipper"
  "fsxn-otel-fpolicy-shipper"
)

echo "=== FSx for ONTAP → OTel Collector Deployment ==="

ERRORS=0
require() {
  if [ -z "${!1:-}" ]; then echo "ERROR: $1 is required but not set."; ERRORS=$((ERRORS + 1)); fi
}
require LAMBDA_CODE_S3_BUCKET
if [ "$CODE_ONLY" != true ]; then
  require OTLP_ENDPOINT
  require S3_BUCKET_NAME
fi
[ "$ERRORS" -gt 0 ] && { echo "Run with --help for the full list."; exit 1; }

# --- Build the package ------------------------------------------------------
# All three handlers plus their shared modules go in one zip; the template points
# every function at the same object and selects the entry point via Handler.
echo "--- Building the Lambda package ---"
BUILD_DIR="$(mktemp -d)"
trap 'rm -rf "${BUILD_DIR}"' EXIT

cp "${INTEGRATION_DIR}/lambda/handler.py" \
   "${INTEGRATION_DIR}/lambda/ems_handler.py" \
   "${INTEGRATION_DIR}/lambda/fpolicy_handler.py" \
   "${INTEGRATION_DIR}/lambda/otlp_auth.py" \
   "${INTEGRATION_DIR}/lambda/otlp_protobuf.py" \
   "${BUILD_DIR}/"

# Without the shared audit parser the handler falls back to JSON-only parsing and
# every XML/EVTX audit log — which is every real ONTAP audit log — arrives with
# no parsed fields.
cp "${SHARED_PYTHON_DIR}/ontap_audit_parser.py" "${BUILD_DIR}/"

ZIP_PATH="${BUILD_DIR}/lambda.zip"
(cd "${BUILD_DIR}" && zip -q -r "${ZIP_PATH}" ./*.py)
echo "  ✅ Package built ($(wc -c < "${ZIP_PATH}" | tr -d ' ') bytes)"

echo "--- Uploading to s3://${LAMBDA_CODE_S3_BUCKET}/${LAMBDA_CODE_S3_KEY} ---"
aws s3 cp "${ZIP_PATH}" "s3://${LAMBDA_CODE_S3_BUCKET}/${LAMBDA_CODE_S3_KEY}" \
  --region "${AWS_REGION}" > /dev/null
echo "  ✅ Uploaded"

# --- Code-only path ---------------------------------------------------------
if [ "$CODE_ONLY" = true ]; then
  echo "--- Updating function code in place ---"
  for fn in "${FUNCTIONS[@]}"; do
    if ! aws lambda get-function-configuration --function-name "$fn" \
         --region "${AWS_REGION}" > /dev/null 2>&1; then
      echo "  ⏭️  ${fn} does not exist, skipping"
      continue
    fi
    aws lambda update-function-code \
      --function-name "$fn" \
      --s3-bucket "${LAMBDA_CODE_S3_BUCKET}" \
      --s3-key "${LAMBDA_CODE_S3_KEY}" \
      --region "${AWS_REGION}" > /dev/null
    aws lambda wait function-updated --function-name "$fn" --region "${AWS_REGION}"
    echo "  ✅ ${fn} updated"
  done
  echo ""
  echo "=== Done (code only) ==="
  exit 0
fi

# --- Deploy the stack -------------------------------------------------------
echo "--- Deploying stack ${STACK_NAME} ---"
aws cloudformation deploy \
  --template-file "${INTEGRATION_DIR}/template.yaml" \
  --stack-name "${STACK_NAME}" \
  --capabilities CAPABILITY_NAMED_IAM \
  --region "${AWS_REGION}" \
  --parameter-overrides \
    OtlpEndpoint="${OTLP_ENDPOINT}" \
    S3BucketName="${S3_BUCKET_NAME}" \
    LambdaCodeS3Bucket="${LAMBDA_CODE_S3_BUCKET}" \
    LambdaCodeS3Key="${LAMBDA_CODE_S3_KEY}" \
    ApiKeySecretArn="${API_KEY_SECRET_ARN}" \
    AuthMode="${AUTH_MODE}" \
    LogLevel="${LOG_LEVEL}" \
    "${ALARM_PARAMS[@]+"${ALARM_PARAMS[@]}"}" \
  --no-fail-on-empty-changeset
echo "  ✅ Stack: ${STACK_NAME}"

# CloudFormation only picks up a new S3 object when the key or version changes,
# so refresh the code explicitly after an update to an existing stack.
echo "--- Refreshing function code from the uploaded package ---"
for fn in "${FUNCTIONS[@]}"; do
  aws lambda update-function-code \
    --function-name "$fn" \
    --s3-bucket "${LAMBDA_CODE_S3_BUCKET}" \
    --s3-key "${LAMBDA_CODE_S3_KEY}" \
    --region "${AWS_REGION}" > /dev/null
  aws lambda wait function-updated --function-name "$fn" --region "${AWS_REGION}"
  echo "  ✅ ${fn}"
done

echo ""
echo "=== Done ==="
echo "Verify with: bash integrations/otel-collector/scripts/verify.sh"
