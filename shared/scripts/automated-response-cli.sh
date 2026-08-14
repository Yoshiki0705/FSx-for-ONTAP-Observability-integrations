#!/bin/bash
# =============================================================================
# automated-response-cli.sh — CLI helper for FSx for ONTAP automated response
#
# Wrapper around `aws sns publish` that formats JSON messages for the
# automated response Lambda. Simplifies manual operations and testing.
#
# Usage:
#   export RESPONSE_TOPIC_ARN="arn:aws:sns:ap-northeast-1:123456789012:fsxn-automated-response-trigger"
#   export DEFAULT_SVM="svm-prod-01"
#
#   ./automated-response-cli.sh block-smb --domain CORP --user jdoe --reason "ARP alert"
#   ./automated-response-cli.sh block-nfs --ip 203.0.113.99 --reason "Mass deletion"
#   ./automated-response-cli.sh contain-smb --domain CORP --user jdoe --volume vol_data
#   ./automated-response-cli.sh contain-nfs --ip 203.0.113.99 --volume vol_data
#   ./automated-response-cli.sh unblock-smb --domain CORP --user jdoe
#   ./automated-response-cli.sh unblock-nfs --ip 203.0.113.99
#   ./automated-response-cli.sh snapshot --volume vol_data --reason "Evidence"
#   ./automated-response-cli.sh test  # Dry-run test message
#
# Environment:
#   RESPONSE_TOPIC_ARN  (required) SNS Topic ARN from CloudFormation outputs
#   DEFAULT_SVM         (optional) Default SVM name
#   AWS_REGION          (optional) Defaults to ap-northeast-1
# =============================================================================
set -euo pipefail

SCRIPT_NAME="$(basename "$0")"
AWS_REGION="${AWS_REGION:-ap-northeast-1}"

# Colors for terminal output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

usage() {
    cat <<EOF
Usage: ${SCRIPT_NAME} <command> [options]

Commands:
  block-smb       Block an SMB user (name-mapping)
  block-nfs       Block an NFS IP (export-policy rule)
  unblock-smb     Remove SMB user block
  unblock-nfs     Remove NFS IP block
  snapshot        Create protective snapshot
  contain-smb     Full SMB containment (snapshot + block + disconnect)
  contain-nfs     Full NFS containment (snapshot + block IP)
  test            Send a dry-run test message (no action taken)

Options:
  --svm NAME      SVM name (default: \$DEFAULT_SVM)
  --domain NAME   Windows domain (SMB actions)
  --user NAME     Windows username (SMB actions)
  --ip ADDR       Client IP address (NFS actions)
  --volume NAME   Volume name (snapshot/contain actions)
  --policy NAME   Export policy name (NFS, default: "default")
  --reason TEXT   Human-readable reason (logged)
  --dry-run       Print the SNS message without publishing
  -h, --help      Show this help

Environment:
  RESPONSE_TOPIC_ARN  SNS Topic ARN (required, from stack outputs)
  DEFAULT_SVM         Default SVM name (optional)
  AWS_REGION          AWS region (default: ap-northeast-1)

Examples:
  # Block a compromised user
  ${SCRIPT_NAME} contain-smb --domain CORP --user jdoe --volume vol_data --reason "ARP detection"

  # Block an attacker IP
  ${SCRIPT_NAME} block-nfs --ip 203.0.113.99 --reason "Mass deletion detected"

  # Unblock after investigation
  ${SCRIPT_NAME} unblock-smb --domain CORP --user jdoe

  # Test (dry-run)
  ${SCRIPT_NAME} test --svm svm-prod-01
EOF
    exit 0
}

error() {
    echo -e "${RED}ERROR: $1${NC}" >&2
    exit 1
}

info() {
    echo -e "${GREEN}$1${NC}"
}

warn() {
    echo -e "${YELLOW}$1${NC}"
}

# Parse global options
COMMAND="${1:-}"
shift 2>/dev/null || true

if [[ -z "$COMMAND" || "$COMMAND" == "-h" || "$COMMAND" == "--help" ]]; then
    usage
fi

# Parse options
SVM_NAME="${DEFAULT_SVM:-}"
DOMAIN=""
USERNAME=""
CLIENT_IP=""
VOLUME_NAME=""
POLICY_NAME="default"
REASON=""
DRY_RUN=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --svm)     SVM_NAME="$2";    shift 2 ;;
        --domain)  DOMAIN="$2";      shift 2 ;;
        --user)    USERNAME="$2";    shift 2 ;;
        --ip)      CLIENT_IP="$2";   shift 2 ;;
        --volume)  VOLUME_NAME="$2"; shift 2 ;;
        --policy)  POLICY_NAME="$2"; shift 2 ;;
        --reason)  REASON="$2";      shift 2 ;;
        --dry-run) DRY_RUN=true;     shift   ;;
        -h|--help) usage ;;
        *) error "Unknown option: $1" ;;
    esac
done

# Validate required environment
if [[ "$DRY_RUN" == false && "$COMMAND" != "test" && -z "${RESPONSE_TOPIC_ARN:-}" ]]; then
    error "RESPONSE_TOPIC_ARN not set. Export it from CloudFormation stack outputs:
    export RESPONSE_TOPIC_ARN=\$(aws cloudformation describe-stacks \\
      --stack-name fsxn-automated-response \\
      --query 'Stacks[0].Outputs[?OutputKey==\`TriggerTopicArn\`].OutputValue' \\
      --output text)"
fi

# Build JSON message based on command
build_message() {
    local action="$1"

    # Validate SVM
    if [[ -z "$SVM_NAME" ]]; then
        error "SVM name required. Use --svm or set DEFAULT_SVM environment variable."
    fi

    # Build JSON with jq if available, otherwise manual construction
    if command -v jq &>/dev/null; then
        local msg
        msg=$(jq -n \
            --arg action "$action" \
            --arg svm "$SVM_NAME" \
            --arg domain "$DOMAIN" \
            --arg user "$USERNAME" \
            --arg ip "$CLIENT_IP" \
            --arg volume "$VOLUME_NAME" \
            --arg policy "$POLICY_NAME" \
            --arg reason "$REASON" \
            '{action: $action, svm_name: $svm}
             + (if $domain != "" then {domain: $domain} else {} end)
             + (if $user != "" then {username: $user} else {} end)
             + (if $ip != "" then {client_ip: $ip} else {} end)
             + (if $volume != "" then {volume_name: $volume} else {} end)
             + (if $policy != "default" then {policy_name: $policy} else {} end)
             + (if $reason != "" then {reason: $reason} else {} end)')
        echo "$msg"
    else
        # Manual JSON construction (no jq)
        local parts="\"action\":\"${action}\",\"svm_name\":\"${SVM_NAME}\""
        [[ -n "$DOMAIN" ]]      && parts="${parts},\"domain\":\"${DOMAIN}\""
        [[ -n "$USERNAME" ]]    && parts="${parts},\"username\":\"${USERNAME}\""
        [[ -n "$CLIENT_IP" ]]   && parts="${parts},\"client_ip\":\"${CLIENT_IP}\""
        [[ -n "$VOLUME_NAME" ]] && parts="${parts},\"volume_name\":\"${VOLUME_NAME}\""
        [[ "$POLICY_NAME" != "default" ]] && parts="${parts},\"policy_name\":\"${POLICY_NAME}\""
        [[ -n "$REASON" ]]      && parts="${parts},\"reason\":\"${REASON}\""
        echo "{${parts}}"
    fi
}

publish_message() {
    local message="$1"
    local action_desc="$2"

    if [[ "$DRY_RUN" == true ]]; then
        warn "[DRY-RUN] Would publish to: ${RESPONSE_TOPIC_ARN:-<not set>}"
        echo "$message" | python3 -m json.tool 2>/dev/null || echo "$message"
        return 0
    fi

    info "Publishing ${action_desc}..."
    echo "$message" | python3 -m json.tool 2>/dev/null || true

    local msg_id
    msg_id=$(aws sns publish \
        --region "$AWS_REGION" \
        --topic-arn "$RESPONSE_TOPIC_ARN" \
        --message "$message" \
        --query 'MessageId' \
        --output text)

    info "✅ Published — MessageId: ${msg_id}"
    echo "   Monitor Lambda execution in CloudWatch Logs."
}

# Command dispatch
case "$COMMAND" in
    block-smb)
        [[ -z "$DOMAIN" ]]   && error "--domain required for block-smb"
        [[ -z "$USERNAME" ]] && error "--user required for block-smb"
        [[ -z "$REASON" ]]   && error "--reason required for blocking actions"
        msg=$(build_message "block_smb_user")
        publish_message "$msg" "SMB user block: ${DOMAIN}\\${USERNAME}"
        ;;

    block-nfs)
        [[ -z "$CLIENT_IP" ]] && error "--ip required for block-nfs"
        [[ -z "$REASON" ]]    && error "--reason required for blocking actions"
        msg=$(build_message "block_nfs_ip")
        publish_message "$msg" "NFS IP block: ${CLIENT_IP}"
        ;;

    unblock-smb)
        [[ -z "$DOMAIN" ]]   && error "--domain required for unblock-smb"
        [[ -z "$USERNAME" ]] && error "--user required for unblock-smb"
        msg=$(build_message "unblock_smb_user")
        publish_message "$msg" "SMB user unblock: ${DOMAIN}\\${USERNAME}"
        ;;

    unblock-nfs)
        [[ -z "$CLIENT_IP" ]] && error "--ip required for unblock-nfs"
        msg=$(build_message "unblock_nfs_ip")
        publish_message "$msg" "NFS IP unblock: ${CLIENT_IP}"
        ;;

    snapshot)
        [[ -z "$VOLUME_NAME" ]] && error "--volume required for snapshot"
        msg=$(build_message "create_snapshot")
        publish_message "$msg" "Protective snapshot: ${VOLUME_NAME}"
        ;;

    contain-smb)
        [[ -z "$DOMAIN" ]]   && error "--domain required for contain-smb"
        [[ -z "$USERNAME" ]] && error "--user required for contain-smb"
        [[ -z "$REASON" ]]   && error "--reason required for containment actions"
        msg=$(build_message "contain_smb_threat")
        publish_message "$msg" "SMB threat containment: ${DOMAIN}\\${USERNAME}"
        ;;

    contain-nfs)
        [[ -z "$CLIENT_IP" ]] && error "--ip required for contain-nfs"
        [[ -z "$REASON" ]]    && error "--reason required for containment actions"
        msg=$(build_message "contain_nfs_threat")
        publish_message "$msg" "NFS threat containment: ${CLIENT_IP}"
        ;;

    test)
        SVM_NAME="${SVM_NAME:-test-svm}"
        REASON="${REASON:-CLI dry-run test}"
        DOMAIN="${DOMAIN:-TEST}"
        USERNAME="${USERNAME:-test-user}"
        VOLUME_NAME="${VOLUME_NAME:-test-vol}"
        DRY_RUN=true
        msg=$(build_message "contain_smb_threat")
        publish_message "$msg" "TEST (dry-run)"
        info ""
        info "To publish for real, remove --dry-run or use a different command."
        info "Ensure RESPONSE_TOPIC_ARN is set before publishing."
        ;;

    *)
        error "Unknown command: ${COMMAND}. Run '${SCRIPT_NAME} --help' for usage."
        ;;
esac
