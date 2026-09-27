#!/bin/bash
# =============================================================================
# Teardown discipline template — pipeline verification environments
#
# This is a TEMPLATE. Each pattern's scripts/teardown.sh sources or copies this
# discipline and fills in STANDUP_RESOURCES / the delete steps for that pattern.
# It is not itself a runnable teardown for any specific pattern.
#
# The discipline it encodes (spec requirement 4, matching the deletion-order
# rules in integrations/lakehouse-retention/):
#
#   1. Delete in REVERSE dependency order. A resource that references another
#      (API Gateway → Lambda, consumer → queue) is deleted before the thing it
#      references. See ordered_delete() below.
#
#   2. FSxN_File_System deletion is NOT done by default. It is billed while
#      running and close to irreversible once deleted. Before deleting it, the
#      script prints which resources become unrecoverable, for how long, and the
#      approximate cost until then, and requires an explicit typed confirmation.
#      A non-interactive flag (-y/--yes) does NOT skip this gate — the default is
#      "do not delete".
#
#   3. SnapLock-backed resources are the same: explicit confirmation required,
#      never skippable by a non-interactive flag. Compliance mode is irreversible
#      until the retention period expires, and the script says so.
#
#   4. Asynchronous deletes are judged by polling post-delete state
#      (Lifecycle == DELETING → gone), not by the API success response. On
#      timeout the script reports state and STOPS; it does not retry with an
#      added flag.
#
# Usage (as adapted by a pattern):
#   bash scripts/teardown.sh                 # deletes the reversible stack only
#   bash scripts/teardown.sh --delete-fsxn   # additionally offers the FSxN gate
#   AWS_REGION=us-east-1 bash scripts/teardown.sh
# =============================================================================
set -euo pipefail

AWS_REGION="${AWS_REGION:-ap-northeast-1}"

# A pattern populates these. STANDUP_RESOURCES is the set the standup creates;
# the teardown reversibility test (scripts/tests/test_teardown_reversibility.py)
# reads the markers below to confirm every standup resource has a delete step.
# ---- teardown:standup-begin ----
# STANDUP: <ResourceLogicalName>   # one per line, added by the pattern
# ---- teardown:standup-end ----

# ---- teardown:delete-begin ----
# DELETE: <ResourceLogicalName>    # one per line, in reverse dependency order
# ---- teardown:delete-end ----

RED='\033[0;31m'; YELLOW='\033[0;33m'; GREEN='\033[0;32m'; NC='\033[0m'

# --- Reverse-dependency stack deletion, async-safe ---------------------------
# Judges completion by polling stack state, not by the delete-stack response.
# `wait stack-delete-complete` polls DescribeStacks until the stack is gone or a
# terminal DELETE_FAILED state; a bare `delete-stack` returns immediately and
# tells you nothing about whether deletion succeeded.
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

# --- Irreversible-resource confirmation gate ---------------------------------
# NOT skippable by -y/--yes. Default is "do not delete". Requires the operator to
# type the exact resource id, so a mistyped or reflexive "yes" cannot delete an
# FSxN_File_System or a SnapLock-backed volume.
confirm_irreversible() {
  local resource_id="$1" kind="$2" cost_note="$3"
  echo ""
  echo -e "${RED}════ IRREVERSIBLE: ${kind} ════${NC}"
  echo "  Resource: ${resource_id}"
  echo "  What becomes unrecoverable: this ${kind} and all data on it."
  case "$kind" in
    FSxN_File_System)
      echo "  For how long: permanently once deletion completes; no undo."
      ;;
    SnapLock)
      echo "  For how long: in Compliance mode, until the retention period expires."
      echo "                Not even an administrator can shorten it. Enterprise mode"
      echo "                allows privileged delete; confirm which mode applies."
      ;;
  esac
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

# --- Async resource-level deletion, judged by post-delete state --------------
# Example for a resource whose delete API is asynchronous (e.g. an FSxN volume).
# Polls the Lifecycle field rather than trusting the delete call's response.
delete_and_poll_lifecycle() {
  local describe_cmd="$1"   # a command that prints the resource's Lifecycle
  local delete_cmd="$2"     # the delete API call
  local label="$3"
  local timeout_s="${4:-600}"
  echo "  deleting (async): ${label}"
  eval "$delete_cmd" >/dev/null || true   # response is not the completion signal
  local waited=0 state=""
  while (( waited < timeout_s )); do
    state="$(eval "$describe_cmd" 2>/dev/null || echo "GONE")"
    if [[ "$state" == "GONE" || -z "$state" ]]; then
      echo -e "  ${GREEN}confirmed gone${NC}: ${label}"
      return 0
    fi
    sleep 15; waited=$((waited + 15))
  done
  echo -e "  ${RED}timeout${NC}: ${label} still reports Lifecycle='${state}' after ${timeout_s}s."
  echo "    Reporting state and stopping. Do NOT retry with an added flag."
  return 1
}

echo "This is the teardown discipline TEMPLATE; a pattern's teardown.sh adapts it."
echo "It performs no deletion when run directly."
