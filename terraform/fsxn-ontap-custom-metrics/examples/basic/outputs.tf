output "lambda_function_name" {
  description = "Name of the poller Lambda function."
  value       = module.ontap_custom_metrics.lambda_function_name
}

output "lambda_security_group_id" {
  description = "Allow TCP 443 from this security group in the file system's security group."
  value       = module.ontap_custom_metrics.lambda_security_group_id
}

output "log_group_name" {
  description = "CloudWatch Logs group of the poller."
  value       = module.ontap_custom_metrics.log_group_name
}

output "dead_letter_queue_url" {
  description = "DLQ for failed invocations."
  value       = module.ontap_custom_metrics.dead_letter_queue_url
}

output "alarm_arns" {
  description = "Alarm ARNs keyed by alarm."
  value       = module.ontap_custom_metrics.alarm_arns
}

output "sns_topic_arn" {
  description = "ARN of the alarm SNS topic, or null when notification_email is empty."
  value       = module.ontap_custom_metrics.sns_topic_arn
}

output "metric_namespaces" {
  description = "CloudWatch namespaces the enabled collectors publish to."
  value       = module.ontap_custom_metrics.metric_namespaces
}

output "vpc_endpoint_ids" {
  description = "Module-created interface endpoint IDs."
  value       = module.ontap_custom_metrics.vpc_endpoint_ids
}
