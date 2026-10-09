output "lambda_function_name" {
  description = "Name of the evaluator Lambda function."
  value       = module.ssd_auto_increase.lambda_function_name
}

output "lambda_role_arn" {
  description = "ARN of the Lambda execution role."
  value       = module.ssd_auto_increase.lambda_role_arn
}

output "trigger_topic_arn" {
  description = "ARN of the trigger SNS topic."
  value       = module.ssd_auto_increase.trigger_topic_arn
}

output "notification_topic_arn" {
  description = "ARN of the notification SNS topic."
  value       = module.ssd_auto_increase.notification_topic_arn
}

output "lock_table_name" {
  description = "Name of the DynamoDB single-flight lock table."
  value       = module.ssd_auto_increase.lock_table_name
}

output "decision_log_group_name" {
  description = "CloudWatch Logs group of the decision log."
  value       = module.ssd_auto_increase.decision_log_group_name
}

output "dead_letter_queue_url" {
  description = "DLQ for failed invocations."
  value       = module.ssd_auto_increase.dead_letter_queue_url
}

output "schedule_rule_arn" {
  description = "ARN of the EventBridge schedule rule."
  value       = module.ssd_auto_increase.schedule_rule_arn
}

output "trigger_alarm_arns" {
  description = "Trigger alarm ARNs keyed by file-system and aggregate/<name>."
  value       = module.ssd_auto_increase.trigger_alarm_arns
}

output "config_fingerprint" {
  description = "Hash of the ceiling, increase percent, mode and archive mode."
  value       = module.ssd_auto_increase.config_fingerprint
}
