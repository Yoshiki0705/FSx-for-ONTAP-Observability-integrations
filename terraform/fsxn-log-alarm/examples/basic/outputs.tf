output "metric_filter_names" {
  description = "Metric filter names keyed by detection."
  value       = module.fsx_ontap_log_alarm.metric_filter_names
}

output "alarm_names" {
  description = "Alarm names keyed by detection."
  value       = module.fsx_ontap_log_alarm.alarm_names
}

output "alarm_arns" {
  description = "Alarm ARNs keyed by detection."
  value       = module.fsx_ontap_log_alarm.alarm_arns
}

output "metric_namespace" {
  description = "CloudWatch namespace the metric filters emit into."
  value       = module.fsx_ontap_log_alarm.metric_namespace
}

output "sns_topic_arn" {
  description = "ARN of the alarm SNS topic, or null when notification_email is empty."
  value       = module.fsx_ontap_log_alarm.sns_topic_arn
}
