#!/bin/bash
# Clean up Datadog integration resources for FSx for ONTAP.
#
# Datadog merges the EMS and FPolicy Lambdas into a single stack
# (${STACK_PREFIX}-ems-fpolicy), which the shared cleanup script does not know
# about — it expects separate "-ems" and "-fpolicy" stacks. This wrapper deletes
# the Datadog-specific stacks in dependency-safe order first, then delegates the
# rest (main integration stack, secret, layer, S3 data) to the shared script.
#
# Deletion order and why:
#   1. ${PREFIX}-ems-webhook  API Gateway — references the EMS Lambda ARN, so it
#                             must go before the stack that owns that Lambda.
#   2. ${PREFIX}-ems-fpolicy  EMS + FPolicy Lambdas, DLQ, EventBridge rule.
#   3. shared script          Main integration stack + optional resources.
#
# NOT deleted (opt in explicitly):
#   ${PREFIX}-log-archive     The archive bucket is DeletionPolicy: Retain and
#                             holds compliance data. Use --delete-log-archive.
#   ${PREFIX}-snapshot-remediation
#                             Created by deploy-snapshot-remediation.sh. It can
#                             take containment actions on the storage, so it is
#                             deliberately opt-in rather than removed by a
#                             blanket cleanup. Use --delete-snapshot-remediation.
#
# Usage:
#   bash integrations/datadog/scripts/cleanup.sh
#   bash integrations/datadog/scripts/cleanup.sh --delete-secret --delete-layer
#   bash integrations/datadog/scripts/cleanup.sh --all
#   bash integrations/datadog/scripts/cleanup.sh --all --s3-bucket my-bucket --s3-prefix audit/svm-prod-01/
#
# Datadog-specific options (all other options are passed to the shared script):
#   --delete-log-archive           Also delete the log archive stack
#                                  (bucket is retained)
#   --delete-snapshot-remediation  Also delete the snapshot remediation stack

set -euo pipefail

# Datadog-specific configuration.
# SECRET_NAME matches the name used by setup-full-observability.sh,
# setup-facets.sh and the E2E verification docs.
export STACK_PREFIX="${STACK_PREFIX:-fsxn-datadog}"
export SECRET_NAME="${SECRET_NAME:-fsxn-datadog-api-key}"
export VENDOR_NAME="Datadog"
AWS_REGION="${AWS_REGION:-ap-northeast-1}"

# --- Separate Datadog-only flags from shared-script flags -------------------
DELETE_LOG_ARCHIVE="false"
DELETE_SNAPSHOT_REMEDIATION="false"
ASSUME_YES="false"
SHARED_ARGS=()
for arg in "$@"; do
  case "$arg" in
    --delete-log-archive) DELETE_LOG_ARCHIVE="true" ;;
    --delete-snapshot-remediation) DELETE_SNAPSHOT_REMEDIATION="true" ;;
    --all)
      # --all is also meaningful to the shared script (secret + layer + S3), so
      # pass it through as well as acting on it here.
      DELETE_SNAPSHOT_REMEDIATION="true"; SHARED_ARGS+=("$arg") ;;
    -y|--yes) ASSUME_YES="true"; SHARED_ARGS+=("$arg") ;;
    *) SHARED_ARGS+=("$arg") ;;
  esac
done

# --- Helper: delete a stack if it exists ------------------------------------
delete_stack_if_exists() {
  local stack_name="$1"
  local description="$2"

  if ! aws cloudformation describe-stacks \
       --stack-name "$stack_name" --region "$AWS_REGION" >/dev/null 2>&1; then
    echo "  ⏭️  Not found, skipping: ${stack_name}"
    return 0
  fi

  echo "  🗑️  Deleting ${stack_name} (${description})..."
  aws cloudformation delete-stack --stack-name "$stack_name" --region "$AWS_REGION"

  if ! aws cloudformation wait stack-delete-complete \
       --stack-name "$stack_name" --region "$AWS_REGION" 2>/dev/null; then
    echo "  ❌ Deletion failed or timed out: ${stack_name}"
    echo "     Inspect the failure with:"
    echo "     aws cloudformation describe-stack-events --stack-name ${stack_name} \\"
    echo "       --region ${AWS_REGION} --query 'StackEvents[?ResourceStatus==\`DELETE_FAILED\`]'"
    return 1
  fi
  echo "  ✅ Deleted: ${stack_name}"
}

echo "============================================================"
echo "Datadog Integration Cleanup — Datadog-specific stacks"
echo "============================================================"
echo "Region:       ${AWS_REGION}"
echo "Stack prefix: ${STACK_PREFIX}"
echo ""

if [ "$ASSUME_YES" != "true" ]; then
  echo "About to delete:"
  echo "  - ${STACK_PREFIX}-ems-webhook (if present)"
  echo "  - ${STACK_PREFIX}-ems-fpolicy (if present)"
  [ "$DELETE_LOG_ARCHIVE" = "true" ] && echo "  - ${STACK_PREFIX}-log-archive (bucket is RETAINED)"
  [ "$DELETE_SNAPSHOT_REMEDIATION" = "true" ] && echo "  - ${STACK_PREFIX}-snapshot-remediation"
  echo "  ...then the shared cleanup will handle the main stack."
  echo ""
  read -r -p "Continue? [y/N] " reply
  case "$reply" in
    [yY]|[yY][eE][sS]) ;;
    *) echo "Aborted."; exit 0 ;;
  esac
  echo ""
fi

echo "--- Step 1: EMS API Gateway ---"
echo "  (Must be deleted BEFORE the EMS Lambda it integrates with)"
delete_stack_if_exists "${STACK_PREFIX}-ems-webhook" "API Gateway"

echo "--- Step 2: EMS + FPolicy Lambdas ---"
delete_stack_if_exists "${STACK_PREFIX}-ems-fpolicy" "EMS + FPolicy Lambdas, DLQ, EventBridge rule"

if [ "$DELETE_LOG_ARCHIVE" = "true" ]; then
  echo "--- Step 3: Log archive ---"
  echo "  ⚠️  The archive S3 bucket has DeletionPolicy: Retain and will survive."
  echo "      Delete its contents and the bucket manually if that is intended."
  delete_stack_if_exists "${STACK_PREFIX}-log-archive" "Datadog log archive role + bucket policy"
else
  echo "--- Log archive: skipped (use --delete-log-archive to include it) ---"
fi

if [ "$DELETE_SNAPSHOT_REMEDIATION" = "true" ]; then
  echo "--- Step 4: Snapshot remediation ---"
  delete_stack_if_exists "${STACK_PREFIX}-snapshot-remediation" \
    "automated snapshot containment Lambda"
else
  echo "--- Snapshot remediation: skipped (use --delete-snapshot-remediation) ---"
  echo "    Left running it keeps a Lambda able to act on the storage."
fi
echo ""

# --- Delegate the remainder to the shared cleanup script --------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SHARED_SCRIPT="${SCRIPT_DIR}/../../../shared/scripts/cleanup-vendor.sh"

if [ ! -f "$SHARED_SCRIPT" ]; then
  echo "ERROR: Shared cleanup script not found: ${SHARED_SCRIPT}"
  echo "Run from the project root directory."
  exit 1
fi

echo "============================================================"
echo "Delegating to shared cleanup (main stack + optional resources)"
echo "============================================================"
exec bash "$SHARED_SCRIPT" "${SHARED_ARGS[@]+"${SHARED_ARGS[@]}"}"
