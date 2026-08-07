#!/bin/bash
# Deploy the optional snapshot remediation Lambda (template-snapshot-remediation.yaml).
#
# This is a containment action, not part of log shipping: a Datadog Workflow
# invokes it after an analyst confirms a mass-deletion or ransomware signal, and
# it creates a timestamped ONTAP snapshot for evidence preservation.
#
# Unlike the audit log shipper, this Lambda MUST run inside the VPC — the ONTAP
# management LIF is not reachable from the internet path.
#
# Prerequisites:
#   - ONTAP credentials in Secrets Manager as {"username": "...", "password": "..."}
#     with permission to create snapshots on the target volume
#   - Private subnets with a route to the ONTAP management LIF (TCP 443)
#   - Secrets Manager reachable from those subnets (NAT Gateway or interface
#     VPC endpoint for com.amazonaws.<region>.secretsmanager)
#
# Usage:
#   export ONTAP_MGMT_IP="198.51.100.10"
#   export ONTAP_CREDENTIALS_SECRET_ARN="arn:aws:secretsmanager:...:secret:fsxn-ontap-admin-XXXXXX"
#   export VPC_SUBNET_IDS="subnet-aaaa,subnet-bbbb"
#   export VPC_SECURITY_GROUP_IDS="sg-cccc"
#   bash integrations/datadog/scripts/deploy-snapshot-remediation.sh
#
# Options:
#   --code-only   Skip CloudFormation; only re-upload the Lambda code
#   --dry-run     Print what would be deployed and exit
#   -h, --help    Show usage
set -euo pipefail

# SECURITY: never enable xtrace — secret ARNs would leak.

CODE_ONLY="false"
DRY_RUN="false"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --code-only) CODE_ONLY="true"; shift ;;
    --dry-run)   DRY_RUN="true"; shift ;;
    -h|--help)
      sed -n '2,27p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "Unknown option: $1 (try --help)"; exit 2 ;;
  esac
done

AWS_REGION="${AWS_REGION:-ap-northeast-1}"
STACK_PREFIX="${STACK_PREFIX:-fsxn-datadog}"
STACK_NAME="${STACK_NAME:-${STACK_PREFIX}-snapshot-remediation}"

# Required
ONTAP_MGMT_IP="${ONTAP_MGMT_IP:-}"
ONTAP_CREDENTIALS_SECRET_ARN="${ONTAP_CREDENTIALS_SECRET_ARN:-}"
VPC_SUBNET_IDS="${VPC_SUBNET_IDS:-}"
VPC_SECURITY_GROUP_IDS="${VPC_SECURITY_GROUP_IDS:-}"

# Optional
DEFAULT_VOLUME="${DEFAULT_VOLUME:-}"
DEFAULT_SVM="${DEFAULT_SVM:-}"
COOLDOWN_MINUTES="${COOLDOWN_MINUTES:-15}"
ONTAP_TIMEOUT_SECONDS="${ONTAP_TIMEOUT_SECONDS:-10}"
CA_CERT_PATH="${CA_CERT_PATH:-}"
CA_CERT_LAYER_ARN="${CA_CERT_LAYER_ARN:-}"
INVOKER_ROLE_ARN="${INVOKER_ROLE_ARN:-}"
ALARM_TOPIC_ARN="${ALARM_TOPIC_ARN:-}"
LOG_LEVEL="${LOG_LEVEL:-INFO}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INTEGRATION_DIR="$(dirname "$SCRIPT_DIR")"

validate_required() {
  local var_name="$1"
  if [ -z "${!var_name:-}" ]; then
    echo "  ❌ $var_name is required but not set."
    return 1
  fi
}

echo "============================================================"
echo "FSx for ONTAP → Datadog: Snapshot Remediation Deployment"
echo "============================================================"
echo "Region: ${AWS_REGION}"
echo "Stack:  ${STACK_NAME}"
echo "============================================================"
echo ""

if [ "${CODE_ONLY}" != "true" ]; then
  ERRORS=0
  validate_required "ONTAP_MGMT_IP" || ERRORS=$((ERRORS + 1))
  validate_required "ONTAP_CREDENTIALS_SECRET_ARN" || ERRORS=$((ERRORS + 1))
  validate_required "VPC_SUBNET_IDS" || ERRORS=$((ERRORS + 1))
  validate_required "VPC_SECURITY_GROUP_IDS" || ERRORS=$((ERRORS + 1))
  if [ "${ERRORS}" -gt 0 ]; then
    echo ""
    echo "See docs/en/snapshot-remediation-setup.md for how to obtain each value."
    exit 2
  fi

  if [ -z "${CA_CERT_PATH}" ]; then
    echo "  ⚠️  CA_CERT_PATH is not set — the function will use CERT_NONE for the"
    echo "      ONTAP REST API connection. Acceptable for a PoC; set a CA cert"
    echo "      before production use (docs/en/production-checklist.md)."
    echo ""
  fi

  PARAMS=(
    "OntapManagementIp=${ONTAP_MGMT_IP}"
    "OntapCredentialsSecretArn=${ONTAP_CREDENTIALS_SECRET_ARN}"
    "VpcSubnetIds=${VPC_SUBNET_IDS}"
    "VpcSecurityGroupIds=${VPC_SECURITY_GROUP_IDS}"
    "CooldownMinutes=${COOLDOWN_MINUTES}"
    "OntapTimeoutSeconds=${ONTAP_TIMEOUT_SECONDS}"
    "LogLevel=${LOG_LEVEL}"
  )
  [ -n "${DEFAULT_VOLUME}" ]     && PARAMS+=("DefaultVolume=${DEFAULT_VOLUME}")
  [ -n "${DEFAULT_SVM}" ]        && PARAMS+=("DefaultSvm=${DEFAULT_SVM}")
  [ -n "${CA_CERT_PATH}" ]       && PARAMS+=("CaCertPath=${CA_CERT_PATH}")
  [ -n "${CA_CERT_LAYER_ARN}" ]  && PARAMS+=("CaCertLayerArn=${CA_CERT_LAYER_ARN}")
  [ -n "${INVOKER_ROLE_ARN}" ]   && PARAMS+=("InvokerRoleArn=${INVOKER_ROLE_ARN}")
  [ -n "${ALARM_TOPIC_ARN}" ]    && PARAMS+=("AlarmNotificationTopicArn=${ALARM_TOPIC_ARN}")

  if [ "${DRY_RUN}" = "true" ]; then
    echo "Would deploy ${STACK_NAME} with:"
    printf '  %s\n' "${PARAMS[@]}"
    exit 0
  fi

  aws cloudformation deploy \
    --template-file "${INTEGRATION_DIR}/template-snapshot-remediation.yaml" \
    --stack-name "${STACK_NAME}" \
    --capabilities CAPABILITY_NAMED_IAM \
    --region "${AWS_REGION}" \
    --parameter-overrides "${PARAMS[@]}" \
    --no-fail-on-empty-changeset
  echo "  ✅ Stack deployed"
  echo ""
fi

# --- Upload the real Lambda code -------------------------------------------
echo "--- Uploading snapshot_remediation.py ---"
FUNCTION_NAME="${STACK_NAME}-snapshot"
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "${WORK_DIR}"' EXIT

zip -q -j "${WORK_DIR}/snapshot_remediation.zip" \
  "${INTEGRATION_DIR}/lambda/snapshot_remediation.py"
aws lambda update-function-code \
  --function-name "${FUNCTION_NAME}" \
  --zip-file "fileb://${WORK_DIR}/snapshot_remediation.zip" \
  --region "${AWS_REGION}" >/dev/null
aws lambda wait function-updated \
  --function-name "${FUNCTION_NAME}" --region "${AWS_REGION}"
echo "  ✅ ${FUNCTION_NAME}"
echo ""

FUNCTION_ARN=$(aws cloudformation describe-stacks \
  --stack-name "${STACK_NAME}" --region "${AWS_REGION}" \
  --query "Stacks[0].Outputs[?OutputKey=='SnapshotRemediationFunctionArn'].OutputValue" \
  --output text)

echo "============================================================"
echo "Deployment Complete"
echo "============================================================"
echo ""
echo "Function ARN: ${FUNCTION_ARN}"
echo ""
echo "Test it (creates a real snapshot):"
echo "  aws lambda invoke --function-name ${FUNCTION_NAME} \\"
echo "    --region ${AWS_REGION} --cli-binary-format raw-in-base64-out \\"
echo "    --payload '{\"volume_name\":\"vol1\",\"svm_name\":\"svm-prod\",\"reason\":\"deploy-test\"}' \\"
echo "    /dev/stdout"
echo ""
echo "Then wire it into Datadog:"
echo "  Workflows → new workflow → AWS Lambda: Invoke function"
echo "  Target: ${FUNCTION_ARN}"
echo "  Guide: integrations/datadog/docs/en/snapshot-remediation-setup.md"
