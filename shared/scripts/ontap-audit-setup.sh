#!/bin/bash
set -euo pipefail

# ============================================================
# FSx for ONTAP Audit Logging Setup Script
# ============================================================
# This script connects to FSx for ONTAP via SSH and configures
# audit logging on the specified SVM.
#
# Prerequisites:
#   - SSH access to FSx for ONTAP management endpoint
#   - ONTAP admin credentials
#   - Audit volume available on the SVM
#   - FSx for ONTAP S3 Access Point attached to the audit volume
#
# Usage:
#   ./ontap-audit-setup.sh \
#     --endpoint <management-ip> \
#     --svm <svm-name> \
#     --format <evtx|xml> \
#     [--rotate-size <size>] \
#     [--dry-run]
# ============================================================

# Default values
ROTATE_SIZE="100MB"
FORMAT="evtx"
DRY_RUN=false
ENDPOINT=""
SVM_NAME=""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

usage() {
  cat <<EOF
Usage: $0 [OPTIONS]

Configure FSx for ONTAP audit logging on a Storage Virtual Machine (SVM).

Required:
  --endpoint <ip>       FSx for ONTAP management endpoint IP address
  --svm <name>          Storage Virtual Machine name

Optional:
  --format <format>     Log format: evtx or xml
  --rotate-size <size>  Log rotation size (default: 100MB)
  --dry-run             Print commands without executing
  --help                Show this help message

Examples:
  # Enable EVTX format audit logging
  $0 --endpoint 198.51.100.100 --svm svm-prod-01

  # Enable XML format with custom rotation
  $0 --endpoint 198.51.100.100 --svm svm-prod-01 --format xml --rotate-size 50MB

  # Preview commands without executing
  $0 --endpoint 198.51.100.100 --svm svm-prod-01 --dry-run
EOF
  exit 0
}

# Parse arguments
while [[ $# -gt 0 ]]; do
  case $1 in
    --endpoint) ENDPOINT="$2"; shift 2 ;;
    --svm) SVM_NAME="$2"; shift 2 ;;
    --format) FORMAT="$2"; shift 2 ;;
    --rotate-size) ROTATE_SIZE="$2"; shift 2 ;;
    --dry-run) DRY_RUN=true; shift ;;
    --help) usage ;;
    *) echo -e "${RED}Unknown option: $1${NC}"; usage ;;
  esac
done

# Validate required parameters
if [[ -z "$ENDPOINT" || -z "$SVM_NAME" ]]; then
  echo -e "${RED}Error: --endpoint and --svm are required${NC}"
  usage
fi

if [[ "$FORMAT" != "evtx" && "$FORMAT" != "xml" ]]; then
  echo -e "${RED}Error: --format must be 'evtx' or 'xml'${NC}"
  exit 1
fi

echo -e "${GREEN}============================================================${NC}"
echo -e "${GREEN} FSx for ONTAP Audit Logging Configuration${NC}"
echo -e "${GREEN}============================================================${NC}"
echo ""
echo "  Endpoint:    $ENDPOINT"
echo "  SVM:         $SVM_NAME"
echo "  Format:      $FORMAT"
echo "  Rotate Size: $ROTATE_SIZE"
echo "  Dry Run:     $DRY_RUN"
echo ""

# ONTAP CLI commands to execute
COMMANDS=(
  "# Step 1: Check if audit configuration already exists"
  "vserver audit show -vserver ${SVM_NAME}"
  ""
  "# Step 2: Create audit configuration"
  "vserver audit create -vserver ${SVM_NAME} -destination /vol/audit_logs -events file-ops -format ${FORMAT} -rotate-size ${ROTATE_SIZE}"
  ""
  "# Step 3: Enable audit logging"
  "vserver audit enable -vserver ${SVM_NAME}"
  ""
  "# Step 4: Verify configuration"
  "vserver audit show -vserver ${SVM_NAME} -fields state,format,destination"
  ""
  "# Step 5: Configure events to audit (file operations)"
  "vserver audit modify -vserver ${SVM_NAME} -events file-ops,cifs-logon-logoff,authorization-policy-change"
)

if [[ "$DRY_RUN" == true ]]; then
  echo -e "${YELLOW}=== DRY RUN: Commands that would be executed ===${NC}"
  echo ""
  echo "SSH target: admin@${ENDPOINT}"
  echo ""
  for cmd in "${COMMANDS[@]}"; do
    if [[ "$cmd" == \#* ]]; then
      echo -e "${GREEN}${cmd}${NC}"
    else
      echo "  $cmd"
    fi
  done
  echo ""
  echo -e "${YELLOW}=== End of dry run ===${NC}"
  echo ""
  echo "To execute, run without --dry-run flag."
  echo "Or connect manually: ssh admin@${ENDPOINT}"
  exit 0
fi

# Execute commands via SSH
echo -e "${YELLOW}Connecting to FSx for ONTAP management endpoint...${NC}"
echo ""

# Build SSH command sequence
SSH_COMMANDS=""
for cmd in "${COMMANDS[@]}"; do
  if [[ -n "$cmd" && "$cmd" != \#* ]]; then
    SSH_COMMANDS+="${cmd}; "
  fi
done

echo -e "${YELLOW}Executing ONTAP CLI commands...${NC}"
echo ""

# Note: FSx for ONTAP uses the 'admin' user by default
# The SSH connection may require the fsxadmin password
ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 "admin@${ENDPOINT}" <<EOF
${SSH_COMMANDS}
EOF

RESULT=$?

if [[ $RESULT -eq 0 ]]; then
  echo ""
  echo -e "${GREEN}✅ Audit logging configured successfully${NC}"
  echo ""
  echo "Next steps:"
  echo "  1. Verify logs are being generated: vserver audit show -vserver ${SVM_NAME}"
  echo "  2. Attach an FSx for ONTAP S3 Access Point to the audit volume"
  echo "  3. Deploy the prerequisites stack: shared/templates/prerequisites.yaml"
  echo "  4. Deploy a vendor integration stack (e.g., integrations/datadog/template.yaml)"
else
  echo ""
  echo -e "${RED}❌ Failed to configure audit logging (exit code: $RESULT)${NC}"
  echo ""
  echo "Troubleshooting:"
  echo "  - Verify SSH access: ssh admin@${ENDPOINT}"
  echo "  - Check security group allows port 22 from your IP"
  echo "  - Verify the SVM name: vserver show"
  exit 1
fi
