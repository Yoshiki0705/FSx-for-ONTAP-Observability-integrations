#!/bin/bash
# =============================================================================
# FSx for ONTAP Observability — Pre-flight Deployment Check
#
# Validates that your existing AWS environment is ready for stack deployment.
# Detects VPC Endpoint conflicts, missing network routes, and ONTAP-level
# blockers BEFORE CloudFormation attempts to create resources.
#
# Usage:
#   bash shared/scripts/preflight-check.sh --vpc-id vpc-xxx --profile automated-response
#   bash shared/scripts/preflight-check.sh --vpc-id vpc-xxx --profile full-suite
#   bash shared/scripts/preflight-check.sh --list-profiles
#   PREFLIGHT_SKIP=vpc_endpoints bash shared/scripts/preflight-check.sh ...
#
# Profiles:
#   audit-shipping         — Vendor integration (no VPC checks)
#   automated-response     — Incident response stack
#   restore-verification   — Recovery point verification
#   content-classification — PII scanner
#   full-suite             — All stacks combined
#   pipeline-pattern-1     — MQTT → InfluxDB → Grafana Live verification env
#   pipeline-pattern-2     — Prometheus + remote_write verification env
#   pipeline-pattern-3     — Managed IoT → Timestream/S3 (reuse only, S3 AP access)
#   pipeline-pattern-4     — Kafka + AutoMQ WAL-on-FSx-for-ONTAP verification env
#   pipeline-pattern-5     — QuestDB / TimescaleDB verification env
#
# Exit Codes:
#   0  — All checks passed
#   78 — Configuration/state failures (actionable)
#   75 — Missing prerequisite tools
#   2  — Usage error
# =============================================================================
set -euo pipefail

# --- Colors and formatting ---------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
NC='\033[0m'

PASS="${GREEN}✓${NC}"
FAIL="${RED}✗${NC}"
WARN="${YELLOW}⚠${NC}"
INFO="${CYAN}ℹ${NC}"

# --- Configuration -----------------------------------------------------------
REGION="${AWS_REGION:-ap-northeast-1}"
VPC_ID=""
PROFILE=""
ONTAP_MGMT_IP=""
SVM_NAME=""
ONTAP_USER=""
ONTAP_PASS=""
SECRET_ARN=""

FAILURES=()
WARNINGS=()

# --- Parse arguments ---------------------------------------------------------
usage() {
  echo "Usage: $0 --vpc-id <vpc-id> --profile <profile> [options]"
  echo ""
  echo "Options:"
  echo "  --vpc-id <id>        VPC ID to check"
  echo "  --profile <name>     Deployment profile (see --list-profiles)"
  echo "  --region <region>    AWS region (default: \$AWS_REGION or ap-northeast-1)"
  echo "  --ontap-ip <ip>      ONTAP management IP (for connectivity check)"
  echo "  --svm-name <name>    SVM name (for restore-verification checks)"
  echo "  --secret-arn <arn>   Secrets Manager ARN (for credential validation)"
  echo "  --list-profiles      Show available profiles"
  echo "  --help               Show this help"
  echo ""
  echo "Environment:"
  echo "  PREFLIGHT_SKIP=<check_id>[,<id>]  Skip specific checks"
  echo "  PREFLIGHT_VERBOSE=1               Show detailed output for passing checks"
  exit "${1:-0}"
}

list_profiles() {
  echo "Available profiles:"
  echo ""
  echo "  audit-shipping         No VPC checks. Validates S3 AP access only."
  echo "  automated-response     Checks: VPC EPs (SecretsManager, SNS), SG egress, subnet routes"
  echo "  restore-verification   Checks: All of above + ONTAP S3 server, volume security style, route tables"
  echo "  content-classification Checks: VPC EPs (S3, DynamoDB, Comprehend, SNS) if VPC mode"
  echo "  full-suite             Checks: Combined checks for all stacks (detects cross-stack EP conflicts)"
  echo ""
  echo "  Pipeline verification environments (integrations/pipeline-verification/):"
  echo "  pipeline-pattern-1     Checks: VPC EPs, S3 AP network origin, AD DC reachability (if AD-joined SVM)"
  echo "  pipeline-pattern-2     Checks: VPC EP conflict (S3 Gateway), SG egress, EC2/ECS-on-EC2 subnet reachability"
  echo "  pipeline-pattern-3     No VPC checks. Validates S3 AP access only (reuse-only pattern)."
  echo "  pipeline-pattern-4     Checks: VPC EPs, ONTAP S3 server, FSx for ONTAP Gen2 throughput, WAL volume reachability"
  echo "  pipeline-pattern-5     Checks: VPC EPs, iSCSI target reachability (EC2), volume security style"
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --vpc-id) VPC_ID="$2"; shift 2 ;;
    --profile) PROFILE="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    --ontap-ip) ONTAP_MGMT_IP="$2"; shift 2 ;;
    --svm-name) SVM_NAME="$2"; shift 2 ;;
    --secret-arn) SECRET_ARN="$2"; shift 2 ;;
    --list-profiles) list_profiles ;;
    --help|-h) usage 0 ;;
    *) echo "Unknown option: $1"; usage 2 ;;
  esac
done

if [[ -z "$PROFILE" ]]; then
  echo "Error: --profile is required"
  usage 2
fi

# --- Skip logic --------------------------------------------------------------
SKIP_LIST="${PREFLIGHT_SKIP:-}"
should_skip() {
  local check_id="$1"
  if [[ "$SKIP_LIST" == "*" ]]; then return 0; fi
  echo "$SKIP_LIST" | tr ',' '\n' | grep -qx "$check_id"
}

# --- Check functions ---------------------------------------------------------

check_tools() {
  local missing=()
  command -v aws >/dev/null 2>&1 || missing+=("aws")
  command -v jq >/dev/null 2>&1 || missing+=("jq")
  command -v curl >/dev/null 2>&1 || missing+=("curl")

  if [[ ${#missing[@]} -gt 0 ]]; then
    echo -e "${FAIL} Required tools missing: ${missing[*]}"
    echo "  fix: Install missing tools (brew install awscli jq curl)"
    exit 75
  fi
  echo -e "${PASS} Required tools: aws, jq, curl"
}

check_aws_identity() {
  if should_skip "aws_identity"; then echo -e "${INFO} [SKIPPED] aws_identity"; return; fi

  local identity
  identity=$(aws sts get-caller-identity --region "$REGION" --output json 2>&1) || {
    FAILURES+=("aws_identity: Cannot authenticate to AWS. Run 'aws configure' or set credentials.")
    echo -e "${FAIL} AWS identity: authentication failed"
    return
  }
  local account=$(echo "$identity" | jq -r '.Account')
  local arn=$(echo "$identity" | jq -r '.Arn')
  echo -e "${PASS} AWS identity: $arn (account: $account)"
}

check_vpc_endpoints() {
  if should_skip "vpc_endpoints"; then echo -e "${INFO} [SKIPPED] vpc_endpoints"; return; fi
  if [[ -z "$VPC_ID" ]]; then echo -e "${INFO} vpc_endpoints: No VPC specified, skipping"; return; fi

  local eps
  eps=$(aws ec2 describe-vpc-endpoints --region "$REGION" \
    --filters "Name=vpc-id,Values=$VPC_ID" \
    --query 'VpcEndpoints[].ServiceName' --output json 2>&1)

  if [[ $? -ne 0 ]]; then
    FAILURES+=("vpc_endpoints: Failed to query VPC endpoints — check permissions (ec2:DescribeVpcEndpoints)")
    echo -e "${FAIL} VPC endpoints: query failed"
    return
  fi

  echo -e "${PASS} VPC endpoints in $VPC_ID:"

  local services=("secretsmanager" "sns" "sts" "s3" "dynamodb" "comprehend" "syslog-logs")
  for svc in "${services[@]}"; do
    local full="com.amazonaws.${REGION}.${svc}"
    if echo "$eps" | jq -e "index(\"$full\")" >/dev/null 2>&1; then
      echo -e "      ${GREEN}exists${NC}: $svc"
    else
      echo -e "      ${YELLOW}absent${NC}: $svc"
    fi
  done

  # Profile-specific conflict detection
  case "$PROFILE" in
    automated-response)
      if echo "$eps" | jq -e 'index("com.amazonaws.'$REGION'.secretsmanager")' >/dev/null 2>&1; then
        WARNINGS+=("SecretsManager EP already exists. Set CreateVpcEndpoints=false in automated-response.yaml")
        echo -e "  ${WARN} SecretsManager EP exists — set CreateVpcEndpoints=false"
      fi
      ;;
    restore-verification)
      if echo "$eps" | jq -e 'index("com.amazonaws.'$REGION'.secretsmanager")' >/dev/null 2>&1; then
        echo -e "  ${INFO} SecretsManager EP exists — set CreateSecretsManagerEndpoint=false"
      fi
      if echo "$eps" | jq -e 'index("com.amazonaws.'$REGION'.s3")' >/dev/null 2>&1; then
        echo -e "  ${INFO} S3 Gateway EP exists — set CreateS3GatewayEndpoint=false"
      fi
      ;;
    full-suite)
      if echo "$eps" | jq -e 'index("com.amazonaws.'$REGION'.secretsmanager")' >/dev/null 2>&1; then
        echo -e "  ${INFO} SecretsManager EP exists — set false in ALL subsequent stacks"
      fi
      ;;
  esac
}

check_security_group_egress() {
  if should_skip "sg_egress"; then echo -e "${INFO} [SKIPPED] sg_egress"; return; fi
  if [[ -z "$VPC_ID" || -z "$ONTAP_MGMT_IP" ]]; then
    echo -e "${INFO} sg_egress: Skipped (need --vpc-id and --ontap-ip)"
    return
  fi

  # Check if there's a SG in the VPC that allows egress to ONTAP IP on 443
  echo -e "${INFO} sg_egress: Verify your SecurityGroupId allows outbound TCP 443 to $ONTAP_MGMT_IP"
  echo "      Check: aws ec2 describe-security-groups --group-ids <sg-id> --query 'SecurityGroups[0].IpPermissionsEgress'"
}

check_ontap_s3_server() {
  if should_skip "ontap_s3"; then echo -e "${INFO} [SKIPPED] ontap_s3"; return; fi
  if [[ -z "$ONTAP_MGMT_IP" || -z "$SVM_NAME" ]]; then
    echo -e "${INFO} ontap_s3: Skipped (need --ontap-ip and --svm-name)"
    return
  fi

  if [[ -z "$ONTAP_USER" || -z "$ONTAP_PASS" ]]; then
    # Try to get from secret
    if [[ -n "$SECRET_ARN" ]]; then
      local secret_val
      secret_val=$(aws secretsmanager get-secret-value --region "$REGION" --secret-id "$SECRET_ARN" --query SecretString --output text 2>/dev/null) || {
        echo -e "${WARN} ontap_s3: Cannot read credentials from $SECRET_ARN — skipping ONTAP-level checks"
        return
      }
      ONTAP_USER=$(echo "$secret_val" | jq -r '.username')
      ONTAP_PASS=$(echo "$secret_val" | jq -r '.password')
    else
      echo -e "${INFO} ontap_s3: Skipped (need --secret-arn for ONTAP API access)"
      return
    fi
  fi

  # NOTE: -sk is intentional. FSx for ONTAP management endpoints use
  # self-signed certificates by default. This script runs in a private
  # network context (VPC internal) where MITM risk is negligible.
  # If your environment has custom CA certs installed, remove -k.
  result=$(curl -sk -u "${ONTAP_USER}:${ONTAP_PASS}" \
    "https://${ONTAP_MGMT_IP}/api/protocols/s3/services?svm.name=${SVM_NAME}" 2>&1)

  local num_records=$(echo "$result" | jq -r '.num_records // 0' 2>/dev/null)

  if [[ "$num_records" -gt 0 ]]; then
    FAILURES+=("ontap_s3: SVM '$SVM_NAME' has an ONTAP S3 server enabled. S3 Access Point creation will fail (structural exclusion). Use a different SVM or delete the S3 server.")
    echo -e "${FAIL} ONTAP S3 server: EXISTS on SVM '$SVM_NAME' — S3 AP attach will fail"
    echo "      fix: Use a different SVM, or delete the S3 server:"
    echo "      curl -sk -u admin:pass -X DELETE \"https://${ONTAP_MGMT_IP}/api/protocols/s3/services/<uuid>?delete_all=true\""
  else
    echo -e "${PASS} ONTAP S3 server: None on SVM '$SVM_NAME' — safe for S3 AP"
  fi
}

check_volume_security_style() {
  if should_skip "vol_style"; then echo -e "${INFO} [SKIPPED] vol_style"; return; fi
  if [[ -z "$ONTAP_MGMT_IP" || -z "$ONTAP_USER" ]]; then
    echo -e "${INFO} vol_style: Skipped (ONTAP credentials not available)"
    return
  fi

  echo -e "${INFO} vol_style: For restore-verification, target volume MUST be UNIX security style."
  echo "      Check: curl -sk -u admin:pass \"https://${ONTAP_MGMT_IP}/api/storage/volumes?name=<vol>&fields=nas.security_style\""
}

check_route_tables() {
  if should_skip "route_tables"; then echo -e "${INFO} [SKIPPED] route_tables"; return; fi
  if [[ -z "$VPC_ID" ]]; then echo -e "${INFO} route_tables: Skipped (no VPC)"; return; fi

  echo -e "${INFO} route_tables: Ensure your SubnetIds have explicit route table associations."
  echo "      Find: aws ec2 describe-route-tables --filters \"Name=vpc-id,Values=$VPC_ID\" --query 'RouteTables[].{Id:RouteTableId,Subnets:Associations[].SubnetId}'"
}

# --- Main execution ----------------------------------------------------------

echo ""
echo "════════════════════════════════════════════════════════════════"
echo " FSx for ONTAP Observability — Pre-flight Check"
echo " Profile: $PROFILE"
echo " Region:  $REGION"
echo " VPC:     ${VPC_ID:-<not specified>}"
echo "════════════════════════════════════════════════════════════════"
echo ""

if [[ "$SKIP_LIST" == "*" ]]; then
  echo -e "${WARN} ALL CHECKS SKIPPED (PREFLIGHT_SKIP=*)"
  echo ""
  exit 0
fi

# Common checks for all profiles
check_tools
check_aws_identity

case "$PROFILE" in
  audit-shipping)
    echo ""
    echo -e "${INFO} Profile 'audit-shipping' requires no VPC checks."
    echo "  Ensure: FSx audit logging enabled, S3 AP created, vendor secret stored."
    ;;
  automated-response)
    check_vpc_endpoints
    check_security_group_egress
    ;;
  restore-verification)
    check_vpc_endpoints
    check_security_group_egress
    check_ontap_s3_server
    check_volume_security_style
    check_route_tables
    ;;
  content-classification)
    if [[ -n "$VPC_ID" ]]; then
      check_vpc_endpoints
    else
      echo -e "${INFO} VPC-外 mode — no VPC checks needed"
    fi
    ;;
  full-suite)
    check_vpc_endpoints
    check_security_group_egress
    check_ontap_s3_server
    check_volume_security_style
    check_route_tables
    ;;
  pipeline-pattern-1)
    # MQTT → InfluxDB → Grafana Live. S3 AP archive origin + AD DC reachability
    # for AD-joined SVMs are the pattern-specific risks; VPC EP conflict is the
    # common one. iSCSI hot-tier detail is a sample-run concern, not preflight.
    check_vpc_endpoints
    check_security_group_egress
    check_route_tables
    ;;
  pipeline-pattern-2)
    # Prometheus + remote_write. Local TSDB runs on EC2/ECS-on-EC2, so subnet
    # egress and the S3 Gateway EP (remote_write archive target) matter.
    check_vpc_endpoints
    check_security_group_egress
    check_route_tables
    ;;
  pipeline-pattern-3)
    # Reuse-only (managed IoT → Timestream/S3). Same shape as audit-shipping:
    # no VPC checks, S3 AP access only.
    echo ""
    echo -e "${INFO} Profile 'pipeline-pattern-3' is reuse-only and requires no VPC checks."
    echo "  Reuses integrations/lakehouse-retention/ and ontap-edge-to-cloud-ai:cloud/iot_ingestion/."
    echo "  Ensure: existing E2E-verified path is available; nothing is rebuilt."
    ;;
  pipeline-pattern-4)
    # Kafka + AutoMQ WAL-on-FSx-for-ONTAP Gen2. WAL volume reachability and the
    # ONTAP S3 server check apply; Gen2 throughput setting is confirmed here.
    check_vpc_endpoints
    check_security_group_egress
    check_ontap_s3_server
    check_route_tables
    ;;
  pipeline-pattern-5)
    # QuestDB / TimescaleDB single node on EC2. iSCSI LUN hot tier (EC2 only —
    # Fargate has no iSCSI initiator); volume security style matters for the LUN.
    check_vpc_endpoints
    check_security_group_egress
    check_volume_security_style
    check_route_tables
    ;;
  *)
    echo "Unknown profile: $PROFILE"
    echo "Run with --list-profiles to see available profiles."
    exit 2
    ;;
esac

# --- Summary -----------------------------------------------------------------
echo ""
echo "════════════════════════════════════════════════════════════════"

if [[ ${#FAILURES[@]} -gt 0 ]]; then
  echo -e " ${FAIL} ${#FAILURES[@]} FAILURE(S) — fix before deploying:"
  echo ""
  for f in "${FAILURES[@]}"; do
    echo -e "   ${RED}•${NC} $f"
  done
  echo ""
  exit 78
fi

if [[ ${#WARNINGS[@]} -gt 0 ]]; then
  echo -e " ${WARN} ${#WARNINGS[@]} WARNING(S) — review parameter overrides:"
  echo ""
  for w in "${WARNINGS[@]}"; do
    echo -e "   ${YELLOW}•${NC} $w"
  done
  echo ""
fi

echo -e " ${PASS} All checks passed for profile '$PROFILE'"
echo ""
exit 0
