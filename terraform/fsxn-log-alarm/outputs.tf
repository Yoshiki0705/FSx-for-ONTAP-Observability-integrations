output "metric_filter_names" {
  description = "Metric filter names keyed by detection."
  value       = { for k, f in aws_cloudwatch_log_metric_filter.this : k => f.name }
}

output "alarm_names" {
  description = "Alarm names keyed by detection."
  value       = { for k, a in aws_cloudwatch_metric_alarm.this : k => a.alarm_name }
}

output "alarm_arns" {
  description = "Alarm ARNs keyed by detection."
  value       = { for k, a in aws_cloudwatch_metric_alarm.this : k => a.arn }
}

output "metric_namespace" {
  description = "CloudWatch namespace the metric filters emit into."
  value       = var.metric_namespace
}

output "sns_topic_arn" {
  description = "ARN of the SNS topic alarms notify: the caller-supplied alarm_sns_topic_arn when set, otherwise the module-created topic, or null when neither is configured."
  value       = var.alarm_sns_topic_arn != "" ? var.alarm_sns_topic_arn : try(aws_sns_topic.alarm[0].arn, null)
}

output "sns_topic_created" {
  description = "Whether the module created and owns the SNS topic (true) or alarms use a caller-supplied topic or none (false)."
  value       = length(aws_sns_topic.alarm) > 0
}
