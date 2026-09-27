#!/bin/bash
# Deploy the Pattern 4 verification environment: Kafka cluster with AutoMQ
# diskless-Kafka and an FSx for ONTAP Generation 2 shared WAL layer.
#
# What it does, in order (spec requirement 1-3, 1-5, 3-1), mirroring pattern-2
# and pattern-5:
#   1. Resolve the Reuse_Reference — confirm shared/templates/vpc-endpoints.yaml
#      exists in this repository before anything is created (the VPC endpoints
#      are REUSED as a nested stack, not copied). The OTel Collector layer and
#      the ClickHouse consumer DDL are reused as documentation pointers only, so
#      their existence is not a deploy-time gate here.
#   2. Preflight — run the shared preflight-check.sh with the pipeline-pattern-4
#      profile. VPC endpoint conflicts stop the standup here, not mid-deploy.
#   3. Resolve the account ID at runtime with `aws sts get-caller-identity`
#      (never hardcoded).
#   4. Upload the reused shared template with `aws s3 cp` (so the nested-stack
#      TemplateURL is a real HTTPS S3 URL) and `aws cloudformation deploy`.
#
# Reused (NOT copied): shared/templates/vpc-endpoints.yaml as a nested stack;
# integrations/otel-collector/ (collector layer) and
# ontap-edge-to-cloud-ai:cloud/clickhouse/ddl/ (ClickHouse consumer DDL) as
# documentation pointers described in the setup guide.
#
# Guardrail (silly-rename): this deploy does NOT auto-mount the Kafka log/WAL
# directory on FSx for ONTAP NFS. A broker log directory on NFS depends on the
# volume setting -is-preserve-unlink-enabled=true (ONTAP 9.12.1+); FSx for ONTAP
# meets the ONTAP-version prerequisite from 9.18.1, but a specific volume being
# set and validated end-to-end is not confirmed (spec task 16, live). Mount is
# an operator step after validation, per the setup guide.
#
# Usage:
#   export ONTAP_MGMT_IP="198.51.100.10"
#   export FILE_SYSTEM_ID="fs-0123456789abcdef0"
#   export VPC_ID="vpc-0123456789abcdef0"
#   export SUBNET_IDS="subnet-0123456789abcdef0,subnet-0123456789abcdef1"
#   export PACKAGE_BUCKET="my-cfn-package-bucket"   # for the reused template upload
#   bash integrations/pipeline-verification/pattern-4-kafka-automq/scripts/deploy.sh --profile pipeline-pattern-4
#
# Options:
#   --profile NAME   Preflight profile (default: pipeline-pattern-4)
#   --mode NAME      Kafka storage mode: automq-wal (default) or traditional
#   --skip-preflight Set PREFLIGHT_SKIP=1 equivalent (not recommended)
#   -h, --help       Show usage
#
# Exit codes (BSD sysexits.h):
#   0  deployed
#   78 (EX_CONFIG)      required configuration missing or reuse ref unresolved
#   69 (EX_UNAVAILABLE) preflight failed
set -euo pipefail

# SECURITY: never enable xtrace — parameter values would leak.

PROFILE="pipeline-pattern-4"
KAFKA_MODE="automq-wal"
SKIP_PREFLIGHT="false"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile) PROFILE="$2"; shift 2 ;;
    --mode) KAFKA_MODE="$2"; shift 2 ;;
    --skip-preflight) SKIP_PREFLIGHT="true"; shift ;;
    -h|--help)
      sed -n '2,46p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "Unknown option: $1 (try --help)"; exit 2 ;;
  esac
done

AWS_REGION="${AWS_REGION:-ap-northeast-1}"
STACK_NAME="${STACK_NAME:-fsxn-pattern-4-kafka-automq}"

# Required
ONTAP_MGMT_IP="${ONTAP_MGMT_IP:-}"
FILE_SYSTEM_ID="${FILE_SYSTEM_ID:-}"
VPC_ID="${VPC_ID:-}"
SUBNET_IDS="${SUBNET_IDS:-}"

# Optional (nested VPC endpoints). When PACKAGE_BUCKET is unset, the nested
# endpoints stack is skipped and the broker fleet is created against the VPC's
# existing endpoints.
PACKAGE_BUCKET="${PACKAGE_BUCKET:-}"
GEN2_HIGH_THROUGHPUT="${GEN2_HIGH_THROUGHPUT:-true}"
SVM_ID="${SVM_ID:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PATTERN_DIR="$(dirname "$SCRIPT_DIR")"
# integrations/pipeline-verification/pattern-4-kafka-automq -> repo root is 3 up.
REPO_ROOT="$(cd "${PATTERN_DIR}/../../.." && pwd)"
TEMPLATE="${PATTERN_DIR}/template.yaml"
SHARED_VPC_ENDPOINTS_TEMPLATE="${REPO_ROOT}/shared/templates/vpc-endpoints.yaml"
PREFLIGHT="${REPO_ROOT}/shared/scripts/preflight-check.sh"

echo "============================================================"
echo "Pattern 4 (Kafka + AutoMQ WAL-on-FSx-for-ONTAP) — Deployment"
echo "============================================================"
echo "Region:   ${AWS_REGION}"
echo "Stack:    ${STACK_NAME}"
echo "Profile:  ${PROFILE}"
echo "Mode:     ${KAFKA_MODE}"
echo "============================================================"
echo ""

# --- Step 1: Resolve the Reuse_Reference ------------------------------------
echo "--- Step 1: Resolve Reuse_Reference (shared VPC endpoints template) ---"
if [ ! -f "${SHARED_VPC_ENDPOINTS_TEMPLATE}" ]; then
  echo "  ❌ Reuse_Reference does not resolve: ${SHARED_VPC_ENDPOINTS_TEMPLATE}"
  echo "     The VPC endpoints are REUSED from this path (nested stack), not"
  echo "     copied. Run from a full checkout."
  exit 78
fi
echo "  ✅ Resolved: shared/templates/vpc-endpoints.yaml"
echo "  ℹ️  Reused as doc pointers (not deploy-time gates): integrations/otel-collector/"
echo "     and ontap-edge-to-cloud-ai:cloud/clickhouse/ddl/ (see setup-guide.md)."
echo ""

# --- Validate required inputs -----------------------------------------------
ERRORS=0
for v in ONTAP_MGMT_IP FILE_SYSTEM_ID VPC_ID SUBNET_IDS; do
  if [ -z "${!v}" ]; then
    echo "  ❌ ${v} is required but not set. export ${v}=\"<value>\""
    ERRORS=$((ERRORS + 1))
  fi
done
if [ "${ERRORS}" -gt 0 ]; then
  echo ""
  echo "See docs/en/setup-guide.md for how to obtain each value."
  exit 78
fi

# --- Step 2: Preflight ------------------------------------------------------
echo "--- Step 2: Preflight (${PROFILE}) ---"
if [ "${SKIP_PREFLIGHT}" = "true" ]; then
  echo "  ⏭️  Skipped (--skip-preflight). Not recommended."
elif [ -x "${PREFLIGHT}" ] || [ -f "${PREFLIGHT}" ]; then
  if ! bash "${PREFLIGHT}" --profile "${PROFILE}" --vpc-id "${VPC_ID}" --region "${AWS_REGION}"; then
    echo "  ❌ Preflight failed. Fix the reported issue (commonly a VPC endpoint"
    echo "     conflict — leave VpcEndpointsTemplateUrl empty to reuse the VPC's"
    echo "     existing endpoints) and re-run."
    exit 69
  fi
  echo "  ✅ Preflight passed"
else
  echo "  ⚠️  Preflight script not found at ${PREFLIGHT}; continuing without it."
fi
echo ""

# --- Step 3: Resolve account ID at runtime ----------------------------------
echo "--- Step 3: Resolve account (aws sts get-caller-identity) ---"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
echo "  ✅ Account: ${ACCOUNT_ID}"
echo ""

# --- Step 4: Package (reuse) and deploy -------------------------------------
echo "--- Step 4: Package reused template and deploy ---"
DEPLOY_PARAMS=(
  "VpcId=${VPC_ID}"
  "SubnetIds=${SUBNET_IDS}"
  "FileSystemId=${FILE_SYSTEM_ID}"
  "OntapMgmtIp=${ONTAP_MGMT_IP}"
  "KafkaMode=${KAFKA_MODE}"
  "Gen2HighThroughput=${GEN2_HIGH_THROUGHPUT}"
)
[ -n "${SVM_ID}" ] && DEPLOY_PARAMS+=("SvmId=${SVM_ID}")

if [ -n "${PACKAGE_BUCKET}" ]; then
  # Upload the reused shared template and pass its HTTPS URL through as the
  # nested-stack TemplateURL. This is the IaC-level reuse: the VPC endpoints are
  # defined only in the shared template.
  VPCE_URL="s3://${PACKAGE_BUCKET}/pattern-4/vpc-endpoints.yaml"
  aws s3 cp "${SHARED_VPC_ENDPOINTS_TEMPLATE}" "${VPCE_URL}" --region "${AWS_REGION}" >/dev/null
  VPCE_HTTPS_URL="https://${PACKAGE_BUCKET}.s3.${AWS_REGION}.amazonaws.com/pattern-4/vpc-endpoints.yaml"
  DEPLOY_PARAMS+=("VpcEndpointsTemplateUrl=${VPCE_HTTPS_URL}")
  echo "  VPC endpoints ENABLED (nested stack): ${VPCE_HTTPS_URL}"
else
  echo "  VPC endpoints reuse skipped (set PACKAGE_BUCKET to create them; leave"
  echo "  unset when the VPC already has the S3 Gateway + Secrets Manager endpoints)."
fi

aws cloudformation deploy \
  --template-file "${TEMPLATE}" \
  --stack-name "${STACK_NAME}" \
  --capabilities CAPABILITY_NAMED_IAM \
  --region "${AWS_REGION}" \
  --parameter-overrides "${DEPLOY_PARAMS[@]}" \
  --no-fail-on-empty-changeset

echo ""
echo "============================================================"
echo "Deployment Complete"
echo "============================================================"
echo ""
echo "Cost drivers (see docs/{ja,en}/setup-guide.md for dated figures):"
echo "  - EC2 Kafka broker(s): billed while running"
echo "  - FSx for ONTAP Gen2 HIGH-THROUGHPUT WAL: costs MORE than the standard"
echo "    configuration (billed continuously while running)"
echo "  - S3 (AutoMQ durable tier): storage + request charges"
echo "  - Interface VPC Endpoint (if any created): ~7.20 USD/month per AZ"
echo ""
echo "Silly-rename guardrail: the NFS WAL/log mount is NOT auto-performed. Set and"
echo "validate -is-preserve-unlink-enabled=true on the volume first (setup guide)."
echo ""
echo "Next:"
echo "  bash ${SCRIPT_DIR}/verify.sh"
echo "Teardown (does NOT delete the FSx for ONTAP file system by default; warns"
echo "before dropping the Gen2 high-throughput WAL configuration):"
echo "  bash ${SCRIPT_DIR}/teardown.sh"
