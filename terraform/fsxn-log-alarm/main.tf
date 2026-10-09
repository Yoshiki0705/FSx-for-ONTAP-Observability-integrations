# Terraform equivalent of shared/templates/cloudwatch-log-alarm.yaml.
#
# The CloudFormation template uses AWS::CloudWatch::LogAlarm, which alarms
# directly off a scheduled CloudWatch Logs Insights query. The pinned provider
# hashicorp/aws 6.67.0 ships no equivalent resource (no aws_cloudwatch_log_alarm
# and no scheduled-query resource; checked against the provider's v6.67.0
# resource index linked below, and documented in the README). This module
# therefore uses the documented alternative that docs/en/monitoring-design.md
# already names: one metric filter per detection (log pattern -> count metric)
# plus one metric alarm on that metric.
#
#   aws_cloudwatch_log_metric_filter:
#     https://github.com/hashicorp/terraform-provider-aws/blob/v6.67.0/website/docs/r/cloudwatch_log_metric_filter.html.markdown
#   aws_cloudwatch_metric_alarm:
#     https://github.com/hashicorp/terraform-provider-aws/blob/v6.67.0/website/docs/r/cloudwatch_metric_alarm.html.markdown

locals {
  # An existing caller-owned topic ARN takes precedence (template parity). Only
  # when none is given and notification_email is set does the module create and
  # own a topic.
  use_existing_topic = var.alarm_sns_topic_arn != ""
  create_sns         = !local.use_existing_topic && var.notification_email != ""
  alarm_actions = (
    local.use_existing_topic ? [var.alarm_sns_topic_arn] :
    local.create_sns ? [aws_sns_topic.alarm[0].arn] : []
  )

  # Neutral phrase per comparison operator for the generated alarm description,
  # so a less-than alarm is not described as firing "above" the threshold.
  operator_phrase = {
    GreaterThanThreshold          = "above"
    GreaterThanOrEqualToThreshold = "at or above"
    LessThanThreshold             = "below"
    LessThanOrEqualToThreshold    = "at or below"
  }
}

# ----------------------------------------------------------------
# SNS topic for alarms (only when the module owns notification)
# ----------------------------------------------------------------
# The CloudFormation template does NOT create a topic: it requires an existing
# AlarmSnsTopicArn and leaves ownership, encryption, subscriptions, and delivery
# policy to the caller. To match that, pass alarm_sns_topic_arn and the module
# creates nothing here. This resource is a convenience for callers who want the
# module to own a topic (set notification_email, leave alarm_sns_topic_arn
# empty). Its encryption is caller-controlled through sns_kms_master_key_id,
# which defaults to empty (no server-side encryption, the console default for a
# new topic); set it to 'alias/aws/sns' or a CMK to encrypt. Messages carry
# alarm names and descriptions, not log content.
resource "aws_sns_topic" "alarm" {
  count = local.create_sns ? 1 : 0

  name              = "${var.name_prefix}-alarms"
  kms_master_key_id = var.sns_kms_master_key_id != "" ? var.sns_kms_master_key_id : null
  tags              = var.tags
}

resource "aws_sns_topic_subscription" "email" {
  count = local.create_sns ? 1 : 0

  topic_arn = aws_sns_topic.alarm[0].arn
  protocol  = "email"
  endpoint  = var.notification_email
}

# ----------------------------------------------------------------
# One metric filter per detection: log pattern -> count metric
# ----------------------------------------------------------------
# default_value = "0" so the metric reports 0 in no-match windows, which pairs
# with treat_missing_data = "notBreaching" on the alarm (the template's
# TreatMissingData: notBreaching). default_value conflicts with dimensions, so
# the transformation sets no dimensions.
resource "aws_cloudwatch_log_metric_filter" "this" {
  for_each = var.detections

  name           = "${var.name_prefix}-${each.key}"
  log_group_name = var.log_group_name
  pattern        = each.value.pattern

  metric_transformation {
    name          = "${var.name_prefix}-${each.key}"
    namespace     = var.metric_namespace
    value         = each.value.metric_value
    default_value = "0"
    unit          = "Count"
  }
}

# ----------------------------------------------------------------
# One metric alarm per detection, reading the filter's metric
# ----------------------------------------------------------------
# metric_name references the filter's transformation so the alarm and filter
# always name the same metric. ok_actions is set alongside alarm_actions; the
# template sets only AlarmActions (a deliberate difference, stated in the README).
resource "aws_cloudwatch_metric_alarm" "this" {
  for_each = var.detections

  alarm_name = "${var.name_prefix}-${each.key}"
  # The generated description names the actual comparison operator so a
  # less-than alarm is not described as firing "above" the threshold.
  alarm_description = (
    each.value.alarm_description != "" ?
    each.value.alarm_description :
    "FSx for ONTAP log detection '${each.key}' on pattern ${each.value.pattern} in ${var.log_group_name}: alarms when the match count is ${local.operator_phrase[each.value.comparison_operator]} ${each.value.threshold}."
  )

  namespace           = var.metric_namespace
  metric_name         = aws_cloudwatch_log_metric_filter.this[each.key].metric_transformation[0].name
  statistic           = "Sum"
  period              = each.value.period_seconds
  evaluation_periods  = each.value.evaluation_periods
  datapoints_to_alarm = each.value.datapoints_to_alarm
  threshold           = each.value.threshold
  comparison_operator = each.value.comparison_operator
  treat_missing_data  = "notBreaching"

  alarm_actions = local.alarm_actions
  ok_actions    = local.alarm_actions
  tags          = var.tags
}
