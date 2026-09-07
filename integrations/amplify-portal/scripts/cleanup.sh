#!/usr/bin/env bash
#
# Tear down the correlator stack and report what is left.
#
# This script removes only what this stack created. The FSx-side resources that
# the verification needs (SVM, volumes, S3 access point attachment, the ONTAP
# audit configuration) are not created here and are not deleted here -- they
# have ordering constraints of their own. The order is:
#
#   1. this script (CloudFormation stack: Lambda, schedule, alarms, dashboard,
#      DLQ, SSM parameter, log group)
#   2. `ampx sandbox delete` for the application sandbox
#   3. ONTAP: vserver audit disable, then vserver audit delete
#   4. the S3 access point attachment  -- must precede the volume
#   5. the volumes                     -- must precede the SVM
#   6. the SVM
#
# Deleting the access point attachment after the volume, or the volume after the
# SVM, fails rather than cascading.
#
set -euo pipefail

STACK_NAME="${STACK_NAME:-fsxn-obs-appsig-correlator}"
AWS_REGION="${AWS_REGION:-ap-northeast-1}"
COST_TAG_VALUE="${COST_TAG_VALUE:-fsxn-obs-appsig}"
ASSUME_YES="${ASSUME_YES:-false}"

echo "=== Resources to delete (stack ${STACK_NAME} in ${AWS_REGION}) ==="
if ! aws cloudformation describe-stacks \
      --region "${AWS_REGION}" --stack-name "${STACK_NAME}" >/dev/null 2>&1; then
  echo "Stack ${STACK_NAME} does not exist; nothing to delete."
else
  aws cloudformation list-stack-resources \
    --region "${AWS_REGION}" \
    --stack-name "${STACK_NAME}" \
    --query 'StackResourceSummaries[].[ResourceType,PhysicalResourceId]' \
    --output table

  if [[ "${ASSUME_YES}" != "true" ]]; then
    read -r -p "Delete these? [y/N] " reply
    [[ "${reply}" == "y" || "${reply}" == "Y" ]] || { echo "Aborted."; exit 0; }
  fi

  # The log group holds the audit side of the join. Deleting the stack deletes
  # it, so any correlation still to be run has to be run first.
  echo "NOTE: the correlation log group is deleted with the stack."

  aws cloudformation delete-stack --region "${AWS_REGION}" --stack-name "${STACK_NAME}"
  echo "Waiting for the stack to be deleted..."
  aws cloudformation wait stack-delete-complete \
    --region "${AWS_REGION}" --stack-name "${STACK_NAME}"
  echo "Stack deleted."
fi

# Deleting a stack reports success as soon as CloudFormation is done with it,
# which is not the same as nothing being left behind. Checked explicitly.
echo
echo "=== Residual check ==="

REMAINING_STACKS="$(aws cloudformation list-stacks \
  --region "${AWS_REGION}" \
  --stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE DELETE_FAILED \
  --query "StackSummaries[?starts_with(StackName, 'fsxn-obs-appsig')].StackName" \
  --output text)"
echo "Stacks matching fsxn-obs-appsig: ${REMAINING_STACKS:-none}"

REMAINING_FUNCTIONS="$(aws lambda list-functions \
  --region "${AWS_REGION}" \
  --query "Functions[?starts_with(FunctionName, 'fsxn-obs-appsig')].FunctionName" \
  --output text)"
echo "Lambda functions matching fsxn-obs-appsig: ${REMAINING_FUNCTIONS:-none}"

REMAINING_DASHBOARDS="$(aws cloudwatch list-dashboards \
  --region "${AWS_REGION}" \
  --query "DashboardEntries[?starts_with(DashboardName, 'fsxn-obs-appsig')].DashboardName" \
  --output text)"
echo "Dashboards matching fsxn-obs-appsig: ${REMAINING_DASHBOARDS:-none}"

REMAINING_ALARMS="$(aws cloudwatch describe-alarms \
  --region "${AWS_REGION}" \
  --alarm-name-prefix fsxn-obs-appsig \
  --query 'MetricAlarms[].AlarmName' --output text)"
echo "Alarms matching fsxn-obs-appsig: ${REMAINING_ALARMS:-none}"

REMAINING_PARAMS="$(aws ssm describe-parameters \
  --region "${AWS_REGION}" \
  --parameter-filters "Key=Name,Option=BeginsWith,Values=/fsxn-appsig/" \
  --query 'Parameters[].Name' --output text)"
echo "SSM parameters under /fsxn-appsig/: ${REMAINING_PARAMS:-none}"

echo
echo "Still to remove by hand, in this order:"
echo "  1. ampx sandbox delete (the application sandbox)"
echo "  2. ONTAP: vserver audit disable -> vserver audit delete"
echo "  3. aws fsx detach-and-delete-s3-access-point   (before the volume)"
echo "  4. aws fsx delete-volume                       (before the SVM)"
echo "  5. aws fsx delete-storage-virtual-machine"
echo
echo "Then confirm the billing stopped:"
echo "  cost allocation tag cost=${COST_TAG_VALUE} in Cost Explorer, next full day"
