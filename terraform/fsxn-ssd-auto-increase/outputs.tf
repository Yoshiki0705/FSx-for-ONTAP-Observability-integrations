output "lambda_function_name" {
  description = "Name of the evaluator Lambda function."
  value       = aws_lambda_function.evaluator.function_name
}

output "lambda_function_arn" {
  description = "ARN of the evaluator Lambda function."
  value       = aws_lambda_function.evaluator.arn
}

output "lambda_role_arn" {
  description = "ARN of the Lambda execution role."
  value       = aws_iam_role.lambda.arn
}

output "trigger_topic_arn" {
  description = "ARN of the trigger SNS topic (alarm -> Lambda). The function never publishes here."
  value       = aws_sns_topic.trigger.arn
}

output "notification_topic_arn" {
  description = "ARN of the notification SNS topic (reports and approve emails)."
  value       = aws_sns_topic.notify.arn
}

output "lock_table_name" {
  description = "Name of the DynamoDB single-flight lock table."
  value       = aws_dynamodb_table.lock.name
}

output "lock_table_arn" {
  description = "ARN of the DynamoDB single-flight lock table."
  value       = aws_dynamodb_table.lock.arn
}

output "decision_log_group_name" {
  description = "CloudWatch Logs group of the decision log (operational history, not the audit record)."
  value       = aws_cloudwatch_log_group.decision.name
}

output "dead_letter_queue_url" {
  description = "DLQ for failed invocations."
  value       = aws_sqs_queue.dlq.url
}

output "dead_letter_queue_arn" {
  description = "ARN of the DLQ."
  value       = aws_sqs_queue.dlq.arn
}

output "schedule_rule_arn" {
  description = "ARN of the EventBridge schedule rule for hourly re-evaluation."
  value       = aws_cloudwatch_event_rule.schedule.arn
}

output "trigger_alarm_arns" {
  description = "Trigger alarm ARNs keyed by file-system and aggregate/<name>."
  value = merge(
    { "file-system" = aws_cloudwatch_metric_alarm.trigger.arn },
    { for a, alarm in aws_cloudwatch_metric_alarm.trigger_aggregate : "aggregate/${a}" => alarm.arn },
  )
}

output "config_fingerprint" {
  description = "Hash of the ceiling, increase percent, mode and archive mode. A blocked latch in the lock table clears when this value changes."
  value       = local.config_fingerprint
}
