#!/bin/bash
# OpenTelemetry Collector — Wire FSx for ONTAP pipeline alarms to a notification channel
#
# WHAT THIS DOES, AND WHY IT DIFFERS FROM THE OTHER VENDORS:
#   The other integrations create detection rules inside a vendor backend
#   (Datadog monitors, Honeycomb triggers, LogScale alerts). The OTel Collector
#   is not a backend — it is a fan-out point, so security detections belong in
#   whichever backend the collector exports to. Creating "OTel alerts" would be
#   creating them nowhere.
#
#   What this integration does own is the delivery pipeline's own health, and
#   there is a real gap there: template.yaml creates four CloudWatch alarms
#   (audit-errors, ems-errors, fpolicy-errors, dlq-messages) but wires
#   AlarmActions only when AlarmNotificationTopicArn is supplied. With the
#   default empty value the alarms deploy, turn red on failure, and notify
#   nobody — visible in the console, invisible everywhere else. This script
#   closes that gap on an already-deployed stack.
#
#   For detection rules, run the create-alerts.sh of the backend you export to:
#     integrations/datadog/scripts/create-alerts.sh
#     integrations/honeycomb/scripts/create-alerts.sh
#     ... and see docs/en/detection-use-cases.md
#
# Prerequisites:
#   - AWS CLI configured, with the OTel Collector stack already deployed
#
# Usage:
#   export ALARM_EMAIL="soc@example.com"          # subscribes to a created topic
#   # STACK_NAME defaults to ${STACK_PREFIX:-fsxn-otel}-integration, matching deploy.sh
#   # or reuse an existing topic instead of creating one:
#   # export ALARM_TOPIC_ARN="arn:aws:sns:<region>:<account-id>:<topic>"
#   bash integrations/otel-collector/scripts/create-alerts.sh

set -euo pipefail

# Same defaults as deploy.sh / verify.sh in this directory, so all three target
# the same stack without extra environment setup.
AWS_REGION="${AWS_REGION:-ap-northeast-1}"
STACK_PREFIX="${STACK_PREFIX:-fsxn-otel}"
STACK_NAME="${STACK_NAME:-${STACK_PREFIX}-integration}"
ALARM_EMAIL="${ALARM_EMAIL:-}"
ALARM_TOPIC_ARN="${ALARM_TOPIC_ARN:-}"
TOPIC_NAME="${TOPIC_NAME:-${STACK_NAME}-alarms}"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " OTel Collector — Wire pipeline alarms to notifications"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Stack:  ${STACK_NAME}"
echo "  Region: ${AWS_REGION}"
echo ""

if [ -z "${ALARM_EMAIL}" ] && [ -z "${ALARM_TOPIC_ARN}" ]; then
  echo "❌ ERROR: set ALARM_EMAIL (creates and subscribes a topic) or"
  echo "         ALARM_TOPIC_ARN (uses an existing topic)."
  exit 1
fi

# Confirm the stack exists before touching anything, so a typo in STACK_NAME
# fails here rather than after a topic has been created.
if ! aws cloudformation describe-stacks --stack-name "${STACK_NAME}" \
      --region "${AWS_REGION}" >/dev/null 2>&1; then
  echo "❌ ERROR: stack '${STACK_NAME}' not found in ${AWS_REGION}."
  echo "   Deploy it first: bash integrations/otel-collector/scripts/deploy.sh"
  exit 1
fi

# ─── Topic ───────────────────────────────────────────────────────
if [ -z "${ALARM_TOPIC_ARN}" ]; then
  echo "  Creating SNS topic '${TOPIC_NAME}'..."
  # create-topic is idempotent: an existing topic of the same name returns its ARN.
  ALARM_TOPIC_ARN=$(aws sns create-topic --name "${TOPIC_NAME}" \
    --region "${AWS_REGION}" --query 'TopicArn' --output text)
  echo "    ✅ ${ALARM_TOPIC_ARN}"

  echo "  Subscribing ${ALARM_EMAIL}..."
  aws sns subscribe --topic-arn "${ALARM_TOPIC_ARN}" \
    --protocol email --notification-endpoint "${ALARM_EMAIL}" \
    --region "${AWS_REGION}" >/dev/null
  echo "    ✅ Subscription requested — confirm the link in that mailbox."
  echo "       Until it is confirmed the subscription stays PendingConfirmation"
  echo "       and no alarm mail is delivered."
else
  echo "  Using existing topic: ${ALARM_TOPIC_ARN}"
fi

echo ""

# ─── Alarms ──────────────────────────────────────────────────────
# Alarm names come from template.yaml: !Sub '${AWS::StackName}-<suffix>'.
ALARM_SUFFIXES=(audit-errors ems-errors fpolicy-errors dlq-messages)
WIRED=0
MISSING=0

for suffix in "${ALARM_SUFFIXES[@]}"; do
  alarm="${STACK_NAME}-${suffix}"
  printf '  %-45s ' "${alarm}"

  # Read the list into a variable before testing it. Piping straight into
  # `grep -q` under `set -o pipefail` lets grep exit at the first match, the
  # upstream command take SIGPIPE, and the pipeline report failure for an alarm
  # that actually exists.
  existing=$(aws cloudwatch describe-alarms --alarm-names "${alarm}" \
    --region "${AWS_REGION}" --query 'MetricAlarms[].AlarmName' --output text)

  if [ -z "${existing}" ] || [ "${existing}" = "None" ]; then
    echo "not found — skipped"
    MISSING=$((MISSING + 1))
    continue
  fi

  # Alarms are updated in place by CloudWatch, but this stack's alarms are
  # CloudFormation-managed: editing them here creates drift, and the next
  # `deploy` reverts AlarmActions to whatever AlarmNotificationTopicArn says.
  # So set the parameter on the stack instead — see the summary below. This
  # loop only reports current wiring.
  actions=$(aws cloudwatch describe-alarms --alarm-names "${alarm}" \
    --region "${AWS_REGION}" --query 'MetricAlarms[0].AlarmActions' --output text)
  if [ -n "${actions}" ] && [ "${actions}" != "None" ]; then
    echo "already wired"
    WIRED=$((WIRED + 1))
  else
    echo "no notification action"
  fi
done

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [ "${MISSING}" -gt 0 ]; then
  echo "⚠️  ${MISSING} of ${#ALARM_SUFFIXES[@]} alarms were not found."
  echo "   The EMS and FPolicy alarms only exist if those handlers were deployed."
fi

if [ "${WIRED}" -eq "${#ALARM_SUFFIXES[@]}" ]; then
  echo "✅ All alarms already have a notification action."
else
  echo "➡️  Apply the topic to the stack so the wiring survives redeployment:"
  echo ""
  echo "   aws cloudformation deploy \\"
  echo "     --template-file integrations/otel-collector/template.yaml \\"
  echo "     --stack-name ${STACK_NAME} \\"
  echo "     --region ${AWS_REGION} \\"
  echo "     --capabilities CAPABILITY_NAMED_IAM \\"
  echo "     --parameter-overrides AlarmNotificationTopicArn=${ALARM_TOPIC_ARN}"
  echo ""
  echo "   Editing the alarms directly with \`aws cloudwatch put-metric-alarm\`"
  echo "   would work until the next deploy, which resets AlarmActions from the"
  echo "   AlarmNotificationTopicArn parameter."
fi

echo ""
echo "Detection rules live in the backend you export to, not here:"
echo "  bash integrations/<backend>/scripts/create-alerts.sh"
echo "  docs/en/detection-use-cases.md"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
