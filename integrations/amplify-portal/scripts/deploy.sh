#!/usr/bin/env bash
#
# Deploy the application-layer signal correlator.
#
# Two steps that both have to happen: CloudFormation creates the stack with a
# placeholder handler (a template cannot inline a handler that imports from a
# layer), then the real handler.py is uploaded. Running only the first leaves a
# function that raises NotImplementedError on every schedule tick.
#
# Every resource is tagged `cost=<COST_TAG_VALUE>` so the verification run can be
# billed back. `cost` specifically, because it is already an activated cost
# allocation tag; activating a new tag key only affects data recorded after
# activation.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INTEGRATION_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${INTEGRATION_DIR}/../.." && pwd)"

STACK_NAME="${STACK_NAME:-fsxn-obs-appsig-correlator}"
AWS_REGION="${AWS_REGION:-ap-northeast-1}"
COST_TAG_VALUE="${COST_TAG_VALUE:-fsxn-obs-appsig}"
ENVIRONMENT="${ENVIRONMENT:-dev}"
EMF_NAMESPACE="${EMF_NAMESPACE:-FSxONTAPAppSignals}"
SCHEDULE_RATE="${SCHEDULE_RATE:-rate(5 minutes)}"
AUDIT_LOG_PREFIX="${AUDIT_LOG_PREFIX:-}"
APP_SIGNAL_LOG_GROUP="${APP_SIGNAL_LOG_GROUP:-}"
JOIN_WINDOW_SECONDS="${JOIN_WINDOW_SECONDS:-120}"
LOG_RETENTION_DAYS="${LOG_RETENTION_DAYS:-30}"

die() { echo "ERROR: $*" >&2; exit 1; }

[[ -n "${FSX_S3_ACCESS_POINT_ARN:-}" ]] || die \
  "FSX_S3_ACCESS_POINT_ARN is required (the access point exposing the audit log volume)"
[[ -n "${OWNER_TAG:-}" ]] || die \
  "OWNER_TAG is required. The template has no default so that no individual's name is committed."

# The handler imports observability and ontap_audit_parser from the shared
# layer. Without it the function raises ImportError on the first invocation
# rather than at deploy time, so the absence is caught here instead.
if [[ -z "${SHARED_LAYER_ARN:-}" ]]; then
  die "SHARED_LAYER_ARN is required. Build it first:
  bash ${REPO_ROOT}/shared/python/build-layer.sh
then publish it and set SHARED_LAYER_ARN to the published version ARN."
fi

echo "=== Deploying ${STACK_NAME} to ${AWS_REGION} ==="

aws cloudformation deploy \
  --region "${AWS_REGION}" \
  --stack-name "${STACK_NAME}" \
  --template-file "${INTEGRATION_DIR}/template.yaml" \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-fail-on-empty-changeset \
  --tags \
    "cost=${COST_TAG_VALUE}" \
    "Project=fsxn-observability-integrations" \
    "Purpose=app-layer-signal-verification" \
    "AutoDelete=true" \
    "Owner=${OWNER_TAG}" \
  --parameter-overrides \
    "FsxS3AccessPointArn=${FSX_S3_ACCESS_POINT_ARN}" \
    "AuditLogPrefix=${AUDIT_LOG_PREFIX}" \
    "AppSignalLogGroupName=${APP_SIGNAL_LOG_GROUP}" \
    "EmfNamespace=${EMF_NAMESPACE}" \
    "JoinWindowSeconds=${JOIN_WINDOW_SECONDS}" \
    "ScheduleRate=${SCHEDULE_RATE}" \
    "SharedLayerArn=${SHARED_LAYER_ARN}" \
    "LogRetentionInDays=${LOG_RETENTION_DAYS}" \
    "Environment=${ENVIRONMENT}" \
    "CostTagValue=${COST_TAG_VALUE}" \
    "OwnerTag=${OWNER_TAG}"

FUNCTION_NAME="$(aws cloudformation describe-stacks \
  --region "${AWS_REGION}" \
  --stack-name "${STACK_NAME}" \
  --query "Stacks[0].Outputs[?OutputKey=='CorrelatorFunctionName'].OutputValue" \
  --output text)"

[[ -n "${FUNCTION_NAME}" && "${FUNCTION_NAME}" != "None" ]] || die \
  "Could not read CorrelatorFunctionName from the stack outputs"

echo "=== Uploading the real handler to ${FUNCTION_NAME} ==="

BUILD_DIR="$(mktemp -d)"
trap 'rm -rf "${BUILD_DIR}"' EXIT
cp "${INTEGRATION_DIR}/lambda/handler.py" "${BUILD_DIR}/handler.py"
(cd "${BUILD_DIR}" && zip -q handler.zip handler.py)

aws lambda update-function-code \
  --region "${AWS_REGION}" \
  --function-name "${FUNCTION_NAME}" \
  --zip-file "fileb://${BUILD_DIR}/handler.zip" \
  --output text --query 'LastModified'

aws lambda wait function-updated \
  --region "${AWS_REGION}" \
  --function-name "${FUNCTION_NAME}"

# A deploy that reports success while the placeholder is still live is the
# failure this checks for: update-function-code can succeed against the wrong
# function, or the zip can be built from a stale copy.
echo "=== Verifying the placeholder is gone ==="
RESULT_FILE="${BUILD_DIR}/invoke.json"
aws lambda invoke \
  --region "${AWS_REGION}" \
  --function-name "${FUNCTION_NAME}" \
  --payload '{"source":"deploy-verification"}' \
  --cli-binary-format raw-in-base64-out \
  "${RESULT_FILE}" >/dev/null

if grep -q "NotImplementedError" "${RESULT_FILE}"; then
  die "The placeholder handler is still deployed. update-function-code did not take effect."
fi
if grep -q "ImportError\|ModuleNotFoundError" "${RESULT_FILE}"; then
  die "The handler could not import its dependencies. Check that SHARED_LAYER_ARN points at a layer built from shared/python/. Response: $(cat "${RESULT_FILE}")"
fi

echo "Response: $(cat "${RESULT_FILE}")"
echo
echo "=== Deployed ==="
aws cloudformation describe-stacks \
  --region "${AWS_REGION}" \
  --stack-name "${STACK_NAME}" \
  --query 'Stacks[0].Outputs[].[OutputKey,OutputValue]' \
  --output table
