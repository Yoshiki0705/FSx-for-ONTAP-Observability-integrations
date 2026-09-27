#!/bin/bash
# =============================================================================
# Pattern 4 (Kafka + AutoMQ WAL-on-FSx-for-ONTAP) — teardown.
#
# Adapts the teardown discipline in
# integrations/pipeline-verification/shared/teardown-template.sh (spec
# requirement 4): reverse-dependency deletion, an irreversible-resource gate for
# the FSx for ONTAP file system that -y/--yes cannot skip, and completion judged
# by polling stack state rather than by the delete-stack response.
#
# Pattern-4 addition: before dropping the FSx for ONTAP Generation 2
# HIGH-THROUGHPUT WAL configuration, teardown surfaces a confirmation, because
# that provisioning costs MORE than the standard configuration while running.
# This gate is informational (the throughput setting belongs to the reused file
# system, not this stack) but is shown so the operator does not leave a
# higher-cost configuration billing after the brokers are gone.
#
# What this stack owns: a Kafka broker (EC2), its security group and the
# self-referencing intra-fleet ingress, its instance role/profile, and — when
# VPC endpoints were created — a nested VPC endpoints stack. Deleting the
# CloudFormation stack removes all of these together; CloudFormation resolves
# intra-stack ordering, and the STANDUP/DELETE markers below record the
# reverse-dependency intent the reversibility test
# (scripts/tests/test_teardown_reversibility.py) checks.
#
# This stack does NOT create or own the FSx for ONTAP file system or its Gen2
# throughput provisioning. The --delete-fsxn gate is present for the discipline
# and refuses by default; the file system is a shared foundation reused across
# patterns, not this stack's to delete.
#
# Usage:
#   bash scripts/teardown.sh                 # delete the reversible stack only
#   bash scripts/teardown.sh --delete-fsxn   # additionally offer the FSxN gate
#   AWS_REGION=us-east-1 bash scripts/teardown.sh
# =============================================================================
set -euo pipefail

AWS_REGION="${AWS_REGION:-ap-northeast-1}"
STACK_NAME="${STACK_NAME:-fsxn-pattern-4-kafka-automq}"
DELETE_FSXN="false"
FILE_SYSTEM_ID="${FILE_SYSTEM_ID:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --delete-fsxn) DELETE_FSXN="true"; shift ;;
    -y|--yes) shift ;;   # accepted for parity; does NOT skip the irreversible gate
    -h|--help) sed -n '2,38p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $1 (try --help)"; exit 2 ;;
  esac
done

# Standup/delete markers for the reversibility test. STANDUP is the set the
# standup (template.yaml) creates; DELETE is the same set in reverse dependency
# order. KafkaBroker depends on the instance profile/role and the security
# group; BrokerIntraFleetIngress depends on the security group; the VPC
# endpoints nested stack is independent.
# ---- teardown:standup-begin ----
# STANDUP: VpcEndpointsStack
# STANDUP: BrokerSecurityGroup
# STANDUP: BrokerIntraFleetIngress
# STANDUP: BrokerInstanceRole
# STANDUP: BrokerInstanceProfile
# STANDUP: KafkaBroker
# ---- teardown:standup-end ----
# ---- teardown:delete-begin ----
# DELETE: KafkaBroker
# DELETE: BrokerInstanceProfile
# DELETE: BrokerInstanceRole
# DELETE: BrokerIntraFleetIngress
# DELETE: BrokerSecurityGroup
# DELETE: VpcEndpointsStack
# ---- teardown:delete-end ----

RED='\033[0;31m'; YELLOW='\033[0;33m'; GREEN='\033[0;32m'; NC='\033[0m'

# Delete a stack, judging completion by polling state (not the delete response).
ordered_delete() {
  local stack_name="$1" description="${2:-}"
  if ! aws cloudformation describe-stacks \
       --stack-name "$stack_name" --region "$AWS_REGION" >/dev/null 2>&1; then
    echo "  skip (not found): ${stack_name}"
    return 0
  fi
  echo "  deleting: ${stack_name} ${description:+($description)}"
  aws cloudformation delete-stack --stack-name "$stack_name" --region "$AWS_REGION"
  if ! aws cloudformation wait stack-delete-complete \
       --stack-name "$stack_name" --region "$AWS_REGION" 2>/dev/null; then
    echo -e "  ${RED}deletion failed or timed out${NC}: ${stack_name}"
    echo "    Reporting state and stopping. Do NOT re-run with an added --force flag;"
    echo "    inspect the failed events first:"
    echo "    aws cloudformation describe-stack-events --stack-name ${stack_name} \\"
    echo "      --region ${AWS_REGION} --query 'StackEvents[?ResourceStatus==\`DELETE_FAILED\`]'"
    return 1
  fi
  echo -e "  ${GREEN}deleted${NC}: ${stack_name}"
}

# Irreversible-resource gate. NOT skippable by -y/--yes; default is do-not-delete.
confirm_irreversible() {
  local resource_id="$1" kind="$2" cost_note="$3"
  echo ""
  echo -e "${RED}════ IRREVERSIBLE: ${kind} ════${NC}"
  echo "  Resource: ${resource_id}"
  echo "  What becomes unrecoverable: this ${kind} and all data on it."
  echo "  For how long: permanently once deletion completes; no undo."
  echo "  Cost until deleted: ${cost_note}"
  echo ""
  echo -e "  ${YELLOW}This gate is not skippable by -y/--yes.${NC} Default is: do not delete."
  printf "  To proceed, type the resource id exactly (%s): " "$resource_id"
  local typed=""
  read -r typed
  if [[ "$typed" != "$resource_id" ]]; then
    echo -e "  ${GREEN}Not deleting${NC} ${resource_id} (input did not match)."
    return 1
  fi
  return 0
}

echo "============================================================"
echo "Pattern 4 (Kafka + AutoMQ WAL-on-FSx-for-ONTAP) — Teardown"
echo "============================================================"
echo "Region: ${AWS_REGION}"
echo "Stack:  ${STACK_NAME}"
echo "============================================================"
echo ""

# --- Gen2 high-throughput WAL cost confirmation ---------------------------
# The Gen2 high-throughput provisioning belongs to the reused file system, not
# this stack, so teardown cannot drop it — but it is the pattern's largest
# steady-state cost driver, so surface it so the operator does not forget it is
# still billing after the brokers are gone.
echo "--- FSx for ONTAP Gen2 high-throughput WAL (cost reminder) ---"
echo -e "  ${YELLOW}NOTE:${NC} the WAL relied on FSx for ONTAP Generation 2 HIGH-THROUGHPUT"
echo "  provisioning, which costs MORE than the standard configuration and keeps"
echo "  billing while the file system runs. This teardown does not change the file"
echo "  system's throughput (it is a shared foundation, not owned here). If this"
echo "  verification is complete and no other pattern needs the high-throughput"
echo "  setting, lower it on the foundation stack to stop the extra charge."
echo "  See docs/{ja,en}/setup-guide.md (Cost Drivers)."
echo ""

# --- Delete the reversible stack (all broker + nested endpoints resources) ---
echo "--- Deleting the verification stack (reverse dependency order handled by CloudFormation) ---"
ordered_delete "${STACK_NAME}" "Kafka broker fleet + nested VPC endpoints"
echo ""

# --- FSxN file system gate (shared foundation; not owned by this stack) ---
if [ "${DELETE_FSXN}" = "true" ]; then
  if [ -z "${FILE_SYSTEM_ID}" ]; then
    echo "  --delete-fsxn given but FILE_SYSTEM_ID is unset; nothing to gate on."
  else
    echo "  NOTE: this stack does not own the FSx for ONTAP file system. It is a"
    echo "  shared foundation reused across patterns. Deleting it here is offered"
    echo "  only for completeness and is strongly discouraged."
    if confirm_irreversible "${FILE_SYSTEM_ID}" "FSxN_File_System" \
         "FSx for ONTAP Gen2 high-throughput is billed continuously while running (setup-guide.md)."; then
      echo "  (No delete is performed by this pattern's teardown; the file system"
      echo "   is managed by its own foundation stack. Delete it there.)"
    fi
  fi
else
  echo "--- FSx for ONTAP file system: NOT touched (default). Use --delete-fsxn to see the gate. ---"
fi
echo ""
echo "Teardown complete for stack ${STACK_NAME}."
