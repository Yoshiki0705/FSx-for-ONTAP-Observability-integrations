#!/bin/bash
# Deploy the Pattern 2 verification environment: Prometheus + remote_write.
#
# What it does, in order (spec requirement 1-3, 1-5, 3-1):
#   1. Resolve the Reuse_Reference — confirm shared/templates/s3-access-point.yaml
#      exists in this repository before anything is created (the archive target
#      is REUSED as a nested stack, not copied).
#   2. Preflight — run the shared preflight-check.sh with the pipeline-pattern-2
#      profile. VPC endpoint conflicts stop the standup here, not mid-deploy.
#   3. Resolve the account ID at runtime with `aws sts get-caller-identity`
#      (never hardcoded).
#   4. Upload the reused shared template with `aws s3 cp` (so the nested-stack
#      TemplateURL is a real HTTPS S3 URL) and `aws cloudformation deploy`.
#
# Reused (NOT copied): shared/templates/s3-access-point.yaml as the remote_write
# archive target, referenced via the nested RemoteWriteArchiveStack.
#
# Guardrail: Prometheus's local TSDB is on a LOCAL EBS volume. NFS/EFS is
# unsupported for the TSDB; this deployment never points --storage.tsdb.path at
# an FSx for ONTAP NFS mount.
#
# Usage:
#   export ONTAP_MGMT_IP="198.51.100.10"
#   export FILE_SYSTEM_ID="fs-0123456789abcdef0"
#   export VPC_ID="vpc-0123456789abcdef0"
#   export SUBNET_IDS="subnet-0123456789abcdef0,subnet-0123456789abcdef1"
#   export ARCHIVE_BUCKET_NAME="my-remote-write-archive-bucket"
#   export PACKAGE_BUCKET="my-cfn-package-bucket"   # for the reused template upload
#   bash integrations/pipeline-verification/pattern-2-prometheus/scripts/deploy.sh --profile pipeline-pattern-2
#
# Options:
#   --profile NAME   Preflight profile (default: pipeline-pattern-2)
#   --skip-preflight Set PREFLIGHT_SKIP=1 equivalent (not recommended)
#   -h, --help       Show usage
#
# Exit codes (BSD sysexits.h):
#   0  deployed
#   78 (EX_CONFIG)      required configuration missing or reuse ref unresolved
#   69 (EX_UNAVAILABLE) preflight failed
set -euo pipefail

# SECURITY: never enable xtrace — parameter values would leak.

PROFILE="pipeline-pattern-2"
SKIP_PREFLIGHT="false"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile) PROFILE="$2"; shift 2 ;;
    --skip-preflight) SKIP_PREFLIGHT="true"; shift ;;
    -h|--help)
      sed -n '2,38p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "Unknown option: $1 (try --help)"; exit 2 ;;
  esac
done

AWS_REGION="${AWS_REGION:-ap-northeast-1}"
STACK_NAME="${STACK_NAME:-fsxn-pattern-2-prometheus}"

# Required
ONTAP_MGMT_IP="${ONTAP_MGMT_IP:-}"
FILE_SYSTEM_ID="${FILE_SYSTEM_ID:-}"
VPC_ID="${VPC_ID:-}"
SUBNET_IDS="${SUBNET_IDS:-}"

# Optional (archive target). When ARCHIVE_BUCKET_NAME/PACKAGE_BUCKET are unset,
# the nested archive stack is skipped and only the Prometheus server is created.
ARCHIVE_BUCKET_NAME="${ARCHIVE_BUCKET_NAME:-}"
PACKAGE_BUCKET="${PACKAGE_BUCKET:-}"
SVM_ID="${SVM_ID:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PATTERN_DIR="$(dirname "$SCRIPT_DIR")"
# integrations/pipeline-verification/pattern-2-prometheus -> repo root is 3 up.
REPO_ROOT="$(cd "${PATTERN_DIR}/../../.." && pwd)"
TEMPLATE="${PATTERN_DIR}/template.yaml"
SHARED_ARCHIVE_TEMPLATE="${REPO_ROOT}/shared/templates/s3-access-point.yaml"
PREFLIGHT="${REPO_ROOT}/shared/scripts/preflight-check.sh"

echo "============================================================"
echo "Pattern 2 (Prometheus + remote_write) — Deployment"
echo "============================================================"
echo "Region:   ${AWS_REGION}"
echo "Stack:    ${STACK_NAME}"
echo "Profile:  ${PROFILE}"
echo "============================================================"
echo ""

# --- Step 1: Resolve the Reuse_Reference ------------------------------------
echo "--- Step 1: Resolve Reuse_Reference (shared archive template) ---"
if [ ! -f "${SHARED_ARCHIVE_TEMPLATE}" ]; then
  echo "  ❌ Reuse_Reference does not resolve: ${SHARED_ARCHIVE_TEMPLATE}"
  echo "     The remote_write archive target is REUSED from this path (nested"
  echo "     stack), not copied. Run from a full checkout."
  exit 78
fi
echo "  ✅ Resolved: shared/templates/s3-access-point.yaml"
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
    echo "     conflict — set the relevant CreateXxxEndpoint=false) and re-run."
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
)
[ -n "${SVM_ID}" ] && DEPLOY_PARAMS+=("SvmId=${SVM_ID}")

if [ -n "${ARCHIVE_BUCKET_NAME}" ] && [ -n "${PACKAGE_BUCKET}" ]; then
  # Upload the reused shared template with `aws s3 cp` and pass its HTTPS URL
  # through as the nested-stack TemplateURL. This is the IaC-level reuse: the S3
  # Access Point is defined only in the shared template, not copied here.
  ARCHIVE_URL="s3://${PACKAGE_BUCKET}/pattern-2/s3-access-point.yaml"
  aws s3 cp "${SHARED_ARCHIVE_TEMPLATE}" "${ARCHIVE_URL}" --region "${AWS_REGION}" >/dev/null
  # `aws cloudformation deploy` needs an HTTPS TemplateURL for nested stacks.
  ARCHIVE_HTTPS_URL="https://${PACKAGE_BUCKET}.s3.${AWS_REGION}.amazonaws.com/pattern-2/s3-access-point.yaml"
  DEPLOY_PARAMS+=(
    "RemoteWriteArchiveTemplateUrl=${ARCHIVE_HTTPS_URL}"
    "ArchiveBucketName=${ARCHIVE_BUCKET_NAME}"
  )
  echo "  Archive target ENABLED (nested stack): ${ARCHIVE_HTTPS_URL}"
else
  echo "  Archive target skipped (set ARCHIVE_BUCKET_NAME and PACKAGE_BUCKET to enable)."
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
echo "  - EC2 Prometheus server: billed while running"
echo "  - FSx for ONTAP file system: billed continuously while running"
echo "  - Interface VPC Endpoint (if any created): ~7.20 USD/month per AZ"
echo ""
echo "Next:"
echo "  bash ${SCRIPT_DIR}/verify.sh"
echo "Teardown (does NOT delete the FSx for ONTAP file system by default):"
echo "  bash ${SCRIPT_DIR}/teardown.sh"
