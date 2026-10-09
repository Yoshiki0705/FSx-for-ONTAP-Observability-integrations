output "lambda_function_name" {
  description = "Name of the poller Lambda function."
  value       = aws_lambda_function.poller.function_name
}

output "lambda_function_arn" {
  description = "ARN of the poller Lambda function."
  value       = aws_lambda_function.poller.arn
}

output "lambda_role_arn" {
  description = "ARN of the Lambda execution role."
  value       = aws_iam_role.lambda.arn
}

output "lambda_security_group_id" {
  description = "Security group of the Lambda function. Allow TCP 443 from it in the file system's security group."
  value       = aws_security_group.lambda.id
}

output "log_group_name" {
  description = "CloudWatch Logs group of the poller."
  value       = aws_cloudwatch_log_group.poller.name
}

output "dead_letter_queue_url" {
  description = "DLQ for failed invocations. Messages here mean the custom metrics are stale."
  value       = aws_sqs_queue.dlq.url
}

output "dead_letter_queue_arn" {
  description = "ARN of the DLQ."
  value       = aws_sqs_queue.dlq.arn
}

output "schedule_rule_arn" {
  description = "ARN of the EventBridge schedule rule."
  value       = aws_cloudwatch_event_rule.schedule.arn
}

output "alarm_arns" {
  description = "Alarm ARNs keyed by qtree_quota, snapmirror_unhealthy, snapmirror_lag, heartbeat/<collector>, dlq_depth and lambda_errors (keys of disabled collectors are absent)."
  value = merge(
    { for a in aws_cloudwatch_metric_alarm.qtree_quota : "qtree_quota" => a.arn },
    { for a in aws_cloudwatch_metric_alarm.snapmirror_unhealthy : "snapmirror_unhealthy" => a.arn },
    { for a in aws_cloudwatch_metric_alarm.snapmirror_lag : "snapmirror_lag" => a.arn },
    { for k, a in aws_cloudwatch_metric_alarm.heartbeat : "heartbeat/${k}" => a.arn },
    {
      dlq_depth     = aws_cloudwatch_metric_alarm.dlq_depth.arn
      lambda_errors = aws_cloudwatch_metric_alarm.lambda_errors.arn
    },
  )
}

output "sns_topic_arn" {
  description = "ARN of the alarm SNS topic, or null when notification_email is empty."
  value       = try(aws_sns_topic.alarm[0].arn, null)
}

output "metric_namespaces" {
  description = "CloudWatch namespaces the enabled collectors publish to."
  value       = local.namespaces
}

output "vpc_endpoint_ids" {
  description = "Module-created interface endpoint IDs keyed by service (monitoring, secretsmanager). Empty by default."
  value       = { for k, e in aws_vpc_endpoint.this : k => e.id }
}
