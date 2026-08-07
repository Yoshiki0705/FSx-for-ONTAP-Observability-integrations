#!/bin/bash
# Clean up Splunk Serverless integration resources for FSx for ONTAP.
#
# This is a thin wrapper around the shared cleanup script.
# See shared/scripts/cleanup-vendor.sh for full documentation.
#
# Usage:
#   bash integrations/splunk-serverless/scripts/cleanup.sh
#   bash integrations/splunk-serverless/scripts/cleanup.sh --all
#   bash integrations/splunk-serverless/scripts/cleanup.sh --all -y

set -euo pipefail

export STACK_PREFIX="${STACK_PREFIX:-fsxn-splunk}"
export SECRET_NAME="${SECRET_NAME:-splunk/fsxn-hec-token}"
export VENDOR_NAME="Splunk Serverless"

# deploy.sh --all also creates ${STACK_PREFIX}-firehose, which is not one of the
# four stacks the shared script knows about. Without this it is left running.
#
# The stack's BackupBucket is declared DeletionPolicy: Retain, so deleting the
# stack leaves the bucket (and any records Firehose could not deliver to Splunk)
# in place. Remove it manually once those records are recovered.
export EXTRA_STACKS="${EXTRA_STACKS:-firehose}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SHARED_SCRIPT="${SCRIPT_DIR}/../../../shared/scripts/cleanup-vendor.sh"

if [ ! -f "$SHARED_SCRIPT" ]; then
  echo "ERROR: Shared cleanup script not found: ${SHARED_SCRIPT}"
  echo "Run from the project root directory."
  exit 1
fi

exec bash "$SHARED_SCRIPT" "$@"
