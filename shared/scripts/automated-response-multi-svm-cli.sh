#!/bin/bash
# =============================================================================
# automated-response-multi-svm-cli.sh — Multi-SVM fan-out for automated response
#
# Wraps automated-response-cli.sh to publish the same containment action
# across multiple SVMs in a single invocation. Useful when a compromised
# identity or IP is known to have access across several SVMs (e.g., a
# domain user with shares on both a production and DR SVM).
#
# This is the lightweight bash-loop alternative to the Step Functions
# fan-out pattern described in the Automated Response Guide FAQ
# ("Can I block a user across multiple SVMs simultaneously?"). Use this
# script for ad hoc / CLI-driven response; use Step Functions if you need
# parallel execution, retries, or Workflow visualization for a large SVM
# fleet.
#
# Usage:
#   export RESPONSE_TOPIC_ARN="arn:aws:sns:ap-northeast-1:123456789012:fsxn-automated-response-trigger"
#
#   ./automated-response-multi-svm-cli.sh contain-smb \
#     --svms "svm-prod-01,svm-prod-02,svm-dr-01" \
#     --domain CORP --user jdoe --volume vol_data \
#     --reason "ARP detection - multi-SVM block"
#
#   ./automated-response-multi-svm-cli.sh block-nfs \
#     --svms "svm-prod-01,svm-prod-02" \
#     --ip 203.0.113.99 --reason "Mass deletion from FPolicy"
#
#   # Dry run across all SVMs first
#   ./automated-response-multi-svm-cli.sh contain-smb \
#     --svms "svm-prod-01,svm-prod-02" \
#     --domain CORP --user jdoe --volume vol_data \
#     --reason "test" --dry-run
#
# Environment:
#   RESPONSE_TOPIC_ARN  (required unless --dry-run) SNS Topic ARN
#   AWS_REGION          (optional) Defaults to ap-northeast-1
#
# Exit code: non-zero if any SVM invocation failed (see summary output).
# =============================================================================
set -euo pipefail

SCRIPT_NAME="$(basename "$0")"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SINGLE_SVM_CLI="${SCRIPT_DIR}/automated-response-cli.sh"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

usage() {
    cat <<EOF
Usage: ${SCRIPT_NAME} <command> --svms "svm1,svm2,..." [options]

Commands (same as automated-response-cli.sh):
  block-smb, block-nfs, unblock-smb, unblock-nfs,
  snapshot, contain-smb, contain-nfs

Required:
  --svms "svm1,svm2,..."   Comma-separated list of SVM names to fan out to

All other options are passed through to automated-response-cli.sh
(--domain, --user, --ip, --volume, --policy, --reason, --dry-run).
Run '${SINGLE_SVM_CLI} --help' for the full option reference.

Examples:
  # Block a compromised user across production and DR SVMs
  ${SCRIPT_NAME} contain-smb --svms "svm-prod-01,svm-dr-01" \\
    --domain CORP --user jdoe --volume vol_data \\
    --reason "ARP detection - multi-SVM block"

  # Dry-run first to confirm the message payloads
  ${SCRIPT_NAME} block-nfs --svms "svm-prod-01,svm-prod-02" \\
    --ip 203.0.113.99 --reason "test" --dry-run
EOF
    exit 0
}

error() { echo -e "${RED}ERROR: $1${NC}" >&2; exit 1; }
info()  { echo -e "${GREEN}$1${NC}"; }
warn()  { echo -e "${YELLOW}$1${NC}"; }

if [[ ! -x "$SINGLE_SVM_CLI" ]]; then
    error "Required helper not found or not executable: ${SINGLE_SVM_CLI}"
fi

COMMAND="${1:-}"
shift 2>/dev/null || true

if [[ -z "$COMMAND" || "$COMMAND" == "-h" || "$COMMAND" == "--help" ]]; then
    usage
fi

SVMS=""
PASSTHROUGH_ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --svms) SVMS="$2"; shift 2 ;;
        -h|--help) usage ;;
        *) PASSTHROUGH_ARGS+=("$1"); shift ;;
    esac
done

if [[ -z "$SVMS" ]]; then
    error "--svms is required (comma-separated list, e.g. --svms \"svm-prod-01,svm-prod-02\")"
fi

IFS=',' read -ra SVM_LIST <<< "$SVMS"
if [[ ${#SVM_LIST[@]} -eq 0 ]]; then
    error "No SVMs parsed from --svms value: ${SVMS}"
fi

info "Fan-out target: ${#SVM_LIST[@]} SVM(s) — ${SVM_LIST[*]}"
info "Command: ${COMMAND}"
echo ""

FAILED_SVMS=()
SUCCEEDED_SVMS=()

for SVM in "${SVM_LIST[@]}"; do
    SVM_TRIMMED="$(echo "$SVM" | xargs)"
    if [[ -z "$SVM_TRIMMED" ]]; then
        continue
    fi

    info "--- ${SVM_TRIMMED} ---"
    if "$SINGLE_SVM_CLI" "$COMMAND" --svm "$SVM_TRIMMED" "${PASSTHROUGH_ARGS[@]}"; then
        SUCCEEDED_SVMS+=("$SVM_TRIMMED")
    else
        warn "Failed for SVM: ${SVM_TRIMMED}"
        FAILED_SVMS+=("$SVM_TRIMMED")
    fi
    echo ""
done

echo "============================================================"
info "Summary: ${#SUCCEEDED_SVMS[@]}/${#SVM_LIST[@]} succeeded"
if [[ ${#SUCCEEDED_SVMS[@]} -gt 0 ]]; then
    info "  Succeeded: ${SUCCEEDED_SVMS[*]}"
fi
if [[ ${#FAILED_SVMS[@]} -gt 0 ]]; then
    warn "  Failed: ${FAILED_SVMS[*]}"
    echo "============================================================"
    exit 1
fi
echo "============================================================"
