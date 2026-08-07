#!/bin/bash
# ============================================================================
# Shared vendor integration cleanup script for FSx for ONTAP Observability.
#
# This is a GENERIC cleanup script that works for any vendor integration.
# Each vendor's cleanup.sh should call this script with vendor-specific
# environment variables.
#
# ============================================================================
# ARCHITECTURE OVERVIEW
# ============================================================================
#
# Each vendor integration deploys up to 4 CloudFormation stacks:
#
#   ┌─────────────────────────────────────────────────────────────────────┐
#   │ Vendor-specific stacks (deleted by this script):                    │
#   │                                                                     │
#   │   ${STACK_PREFIX}-<EXTRA_STACKS>  Vendor-declared extra stacks      │
#   │   ${STACK_PREFIX}-fpolicy       EventBridge rule + vendor Lambda    │
#   │   ${STACK_PREFIX}-ems-webhook   API Gateway (references EMS Lambda) │
#   │   ${STACK_PREFIX}-ems           EMS Lambda function                 │
#   │   ${STACK_PREFIX}-integration   Main S3 audit log Lambda            │
#   │                                                                     │
#   │ Optional resources:                                                 │
#   │   Lambda Layer (EMS Parser)                                         │
#   │   Secrets Manager secret                                            │
#   │   S3 test data                                                      │
#   └─────────────────────────────────────────────────────────────────────┘
#
#   Any stack a vendor's deploy.sh creates but this script does not know about
#   is silently left behind, still costing money. Vendors with stacks beyond the
#   four above MUST declare them in EXTRA_STACKS — see the splunk-serverless
#   wrapper for an example.
#
#   ┌─────────────────────────────────────────────────────────────────────┐
#   │ Shared resources (NOT deleted — used by all vendors):               │
#   │                                                                     │
#   │   fsxn-fp-srv          FPolicy ECS Fargate + SQS + EventBridge     │
#   │   S3 Access Point      Audit log access control                    │
#   │   S3 Bucket            Audit log storage                           │
#   │   Prerequisites stack  IAM roles, VPC config                       │
#   └─────────────────────────────────────────────────────────────────────┘
#
# ============================================================================
# DELETION ORDER (dependency-safe)
# ============================================================================
#
#   0. EXTRA_STACKS          (vendor-declared leaf stacks, deleted first)
#          ↓
#   1. FPolicy stack     ─┐
#                          ├─→ No cross-dependency between these two
#   2. EMS Webhook stack ─┘
#          ↓
#   3. EMS Lambda stack      (API Gateway in step 2 references this ARN)
#          ↓
#   4. Main integration stack
#          ↓
#   5. [Optional] Lambda Layer, Secret, S3 data
#
# WHY THIS ORDER MATTERS:
#   - The EMS Webhook (API Gateway) stack has a Lambda integration that
#     references the EMS Lambda ARN. If you delete the Lambda first,
#     CloudFormation will fail to delete the API Gateway integration.
#   - The FPolicy stack's EventBridge rule targets its own Lambda, so
#     it has no external dependencies and can be deleted in any order.
#
# ============================================================================
# USAGE
# ============================================================================
#
# Direct usage (not recommended — use vendor-specific wrapper instead):
#   STACK_PREFIX=fsxn-grafana bash shared/scripts/cleanup-vendor.sh
#
# Via vendor wrapper (recommended):
#   bash integrations/grafana/scripts/cleanup.sh
#   bash integrations/datadog/scripts/cleanup.sh
#
# Options:
#   --delete-secret    Delete the Secrets Manager secret
#   --delete-layer     Delete the EMS Parser Lambda Layer (all versions)
#   --clean-s3         Delete test data from S3 (requires --s3-bucket and --s3-prefix)
#   --all              Delete everything (secret + layer + S3)
#   -y, --yes          Skip confirmation prompt (for CI/CD)
#   -h, --help         Show this help
#
# ============================================================================
# ENVIRONMENT VARIABLES
# ============================================================================
#
#   STACK_PREFIX        (REQUIRED) CloudFormation stack name prefix
#                       Examples: fsxn-grafana, fsxn-datadog, fsxn-elastic
#   AWS_REGION          AWS region (default: ap-northeast-1)
#   SECRET_NAME         Secrets Manager secret name
#   EMS_LAYER_NAME      Lambda Layer name (default: fsxn-ems-parser)
#   VENDOR_NAME         Human-readable vendor name for display
#   EXTRA_STACKS        Space-separated suffixes of additional stacks this
#                       vendor's deploy scripts create, deleted before the four
#                       standard stacks. Example: EXTRA_STACKS="firehose"
#                       deletes ${STACK_PREFIX}-firehose.
#
# ============================================================================

set -euo pipefail

# --- Configuration ----------------------------------------------------------

AWS_REGION="${AWS_REGION:-ap-northeast-1}"
STACK_PREFIX="${STACK_PREFIX:-}"
SECRET_NAME="${SECRET_NAME:-}"
EMS_LAYER_NAME="${EMS_LAYER_NAME:-fsxn-ems-parser}"
VENDOR_NAME="${VENDOR_NAME:-}"
EXTRA_STACKS="${EXTRA_STACKS:-}"

# Word-split into an array once so the deletion loop and the confirmation list
# agree on what will happen.
read -r -a EXTRA_STACK_LIST <<< "$EXTRA_STACKS"

# --- Parse arguments --------------------------------------------------------

DELETE_SECRET=false
DELETE_LAYER=false
CLEAN_S3=false
SKIP_CONFIRM=false
S3_BUCKET=""
S3_PREFIX=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --delete-secret) DELETE_SECRET=true; shift ;;
    --delete-layer)  DELETE_LAYER=true; shift ;;
    --clean-s3)      CLEAN_S3=true; shift ;;
    --s3-bucket)     S3_BUCKET="$2"; shift 2 ;;
    --s3-prefix)     S3_PREFIX="$2"; shift 2 ;;
    --all)           DELETE_SECRET=true; DELETE_LAYER=true; CLEAN_S3=true; shift ;;
    -y|--yes)        SKIP_CONFIRM=true; shift ;;
    -h|--help)
      echo "Usage: STACK_PREFIX=fsxn-<vendor> $0 [OPTIONS]"
      echo ""
      echo "Options:"
      echo "  --delete-secret    Delete the Secrets Manager secret"
      echo "  --delete-layer     Delete the EMS Parser Lambda Layer (all versions)"
      echo "  --clean-s3         Delete test data from S3"
      echo "  --s3-bucket NAME   S3 bucket name (required with --clean-s3)"
      echo "  --s3-prefix PATH   S3 key prefix to delete (required with --clean-s3)"
      echo "  --all              Delete everything (secret + layer + S3)"
      echo "  -y, --yes          Skip confirmation prompt"
      echo ""
      echo "Environment variables:"
      echo "  STACK_PREFIX   (required) Stack name prefix, e.g., fsxn-grafana"
      echo "  AWS_REGION     AWS region (default: ap-northeast-1)"
      echo "  SECRET_NAME    Secrets Manager secret name"
      echo "  EMS_LAYER_NAME Lambda Layer name (default: fsxn-ems-parser)"
      exit 0
      ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

# --- Validation -------------------------------------------------------------

if [ -z "$STACK_PREFIX" ]; then
  echo "ERROR: STACK_PREFIX is required."
  echo "  export STACK_PREFIX=fsxn-grafana  # or fsxn-datadog, fsxn-elastic, etc."
  exit 1
fi

# Derive vendor name from prefix if not set
if [ -z "$VENDOR_NAME" ]; then
  VENDOR_NAME="${STACK_PREFIX#fsxn-}"
fi

# --- Confirmation -----------------------------------------------------------

echo "=== FSx for ONTAP ${VENDOR_NAME} Integration Cleanup ==="
echo ""
echo "Region: ${AWS_REGION}"
echo "Stack prefix: ${STACK_PREFIX}"
echo ""
echo "Stacks to delete (in dependency-safe order):"
for suffix in "${EXTRA_STACK_LIST[@]+"${EXTRA_STACK_LIST[@]}"}"; do
  echo "  0. ${STACK_PREFIX}-${suffix}$(printf '%*s' $((14 - ${#suffix})) '')(vendor extra stack)"
done
echo "  1. ${STACK_PREFIX}-fpolicy          (FPolicy vendor Lambda)"
echo "  2. ${STACK_PREFIX}-ems-webhook      (EMS API Gateway)"
echo "  3. ${STACK_PREFIX}-ems              (EMS Lambda)"
echo "  4. ${STACK_PREFIX}-integration      (Main audit log Lambda)"
if [ "$DELETE_LAYER" = true ]; then
  echo "  5. Lambda Layer: ${EMS_LAYER_NAME} (all versions)"
fi
if [ "$DELETE_SECRET" = true ] && [ -n "$SECRET_NAME" ]; then
  echo "  6. Secret: ${SECRET_NAME}"
fi
if [ "$CLEAN_S3" = true ]; then
  echo "  7. S3: s3://${S3_BUCKET}/${S3_PREFIX}*"
fi
echo ""
echo "Shared resources NOT deleted:"
echo "  - fsxn-fp-srv (FPolicy ECS Fargate + SQS + EventBridge)"
echo "  - S3 Access Point / S3 Bucket"
echo ""

if [ "$SKIP_CONFIRM" != true ]; then
  read -r -p "Proceed with cleanup? (y/N): " CONFIRM
  if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 0
  fi
  echo ""
fi

# --- Helper: delete a CloudFormation stack ----------------------------------

delete_stack() {
  local stack_name="$1"
  local description="${2:-}"
  echo "  Checking: ${stack_name}${description:+ ($description)}..."

  if ! aws cloudformation describe-stacks \
    --stack-name "$stack_name" \
    --region "$AWS_REGION" > /dev/null 2>&1; then
    echo "  ⏭️  Does not exist, skipping."
    return 0
  fi

  local status
  status=$(aws cloudformation describe-stacks \
    --stack-name "$stack_name" \
    --region "$AWS_REGION" \
    --query "Stacks[0].StackStatus" --output text 2>/dev/null || echo "UNKNOWN")

  if [ "$status" = "DELETE_FAILED" ]; then
    echo "  ⚠️  Stack is in DELETE_FAILED state."
    echo "     Common causes: non-empty S3 bucket, Lambda still referenced, IAM role in use."
    echo "     Check CloudFormation console, then retry:"
    echo "     aws cloudformation delete-stack --stack-name ${stack_name} --region ${AWS_REGION}"
    return 1
  fi

  echo "  Deleting: ${stack_name} (status: ${status})..."
  aws cloudformation delete-stack \
    --stack-name "$stack_name" \
    --region "$AWS_REGION"

  echo "  Waiting (1-3 minutes)..."
  if ! aws cloudformation wait stack-delete-complete \
    --stack-name "$stack_name" \
    --region "$AWS_REGION" 2>/dev/null; then
    echo "  ❌ Deletion failed or timed out. Check:"
    echo "     aws cloudformation describe-stack-events --stack-name ${stack_name} --region ${AWS_REGION} --query 'StackEvents[?ResourceStatus==\`DELETE_FAILED\`]'"
    return 1
  fi

  echo "  ✅ Deleted: ${stack_name}"
}

# --- Execute cleanup --------------------------------------------------------

if [ ${#EXTRA_STACK_LIST[@]} -gt 0 ]; then
  echo "--- Step 0/4: Vendor extra stacks ---"
  for suffix in "${EXTRA_STACK_LIST[@]}"; do
    delete_stack "${STACK_PREFIX}-${suffix}" "vendor extra stack"
  done
fi

echo "--- Step 1/4: FPolicy vendor Lambda ---"
delete_stack "${STACK_PREFIX}-fpolicy" "EventBridge rule + Lambda"

echo "--- Step 2/4: EMS API Gateway ---"
echo "  (Must delete BEFORE EMS Lambda — API Gateway references Lambda ARN)"
delete_stack "${STACK_PREFIX}-ems-webhook" "API Gateway"

echo "--- Step 3/4: EMS Lambda ---"
delete_stack "${STACK_PREFIX}-ems" "EMS Lambda function"

echo "--- Step 4/4: Main integration ---"
delete_stack "${STACK_PREFIX}-integration" "S3 audit log Lambda"

# --- Optional: Lambda Layer -------------------------------------------------

if [ "$DELETE_LAYER" = true ]; then
  echo ""
  echo "--- Lambda Layer: ${EMS_LAYER_NAME} ---"
  LAYER_VERSIONS=$(aws lambda list-layer-versions \
    --layer-name "$EMS_LAYER_NAME" \
    --region "$AWS_REGION" \
    --query "LayerVersions[].Version" --output text 2>/dev/null || echo "")

  if [ -z "$LAYER_VERSIONS" ]; then
    echo "  ⏭️  No versions found, skipping."
  else
    for version in $LAYER_VERSIONS; do
      aws lambda delete-layer-version \
        --layer-name "$EMS_LAYER_NAME" \
        --version-number "$version" \
        --region "$AWS_REGION"
      echo "  ✅ Deleted: ${EMS_LAYER_NAME}:${version}"
    done
  fi
fi

# --- Optional: Secrets Manager ----------------------------------------------

if [ "$DELETE_SECRET" = true ] && [ -n "$SECRET_NAME" ]; then
  echo ""
  echo "--- Secret: ${SECRET_NAME} ---"
  if aws secretsmanager describe-secret \
    --secret-id "$SECRET_NAME" \
    --region "$AWS_REGION" > /dev/null 2>&1; then
    aws secretsmanager delete-secret \
      --secret-id "$SECRET_NAME" \
      --recovery-window-in-days 7 \
      --region "$AWS_REGION" > /dev/null
    echo "  ✅ Scheduled for deletion (7-day recovery): ${SECRET_NAME}"
  else
    echo "  ⏭️  Does not exist, skipping."
  fi
fi

# --- Optional: S3 test data -------------------------------------------------

if [ "$CLEAN_S3" = true ]; then
  echo ""
  echo "--- S3 test data ---"
  if [ -z "$S3_BUCKET" ] || [ -z "$S3_PREFIX" ]; then
    echo "  ERROR: --s3-bucket and --s3-prefix required with --clean-s3"
    exit 1
  fi
  # Safety guard: refuse dangerously short prefixes
  if [ ${#S3_PREFIX} -lt 5 ]; then
    echo "  ERROR: S3 prefix '${S3_PREFIX}' is too short (min 5 chars)."
    echo "  This guard prevents accidental deletion of broad key ranges."
    echo "  Use a specific prefix like 'audit/svm-prod-01/' or 'test/'."
    exit 1
  fi
  echo "  Deleting: s3://${S3_BUCKET}/${S3_PREFIX}..."
  aws s3 rm "s3://${S3_BUCKET}/${S3_PREFIX}" --recursive --region "$AWS_REGION"
  echo "  ✅ S3 test data cleaned."
fi

# --- Summary ----------------------------------------------------------------

echo ""
echo "=== ${VENDOR_NAME} Cleanup Complete ==="
echo ""
echo "To delete shared resources (after ALL vendors removed):"
echo "  bash shared/scripts/cleanup-shared.sh"
