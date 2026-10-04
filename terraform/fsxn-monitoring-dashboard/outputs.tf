output "dashboard_name" {
  description = "CloudWatch dashboard name."
  value       = aws_cloudwatch_dashboard.this.dashboard_name
}

output "dashboard_arn" {
  description = "CloudWatch dashboard ARN."
  value       = aws_cloudwatch_dashboard.this.dashboard_arn
}

output "dashboard_url" {
  description = "Direct URL to the CloudWatch dashboard (same shape as the template's DashboardUrl output)."
  value       = "https://${local.region}.console.aws.amazon.com/cloudwatch/home?region=${local.region}#dashboards:name=${aws_cloudwatch_dashboard.this.dashboard_name}"
}

output "capacity_alarm_arn" {
  description = "ARN of the storage capacity alarm."
  value       = aws_cloudwatch_metric_alarm.storage_capacity.arn
}

output "throughput_alarm_arn" {
  description = "ARN of the network throughput utilization alarm."
  value       = aws_cloudwatch_metric_alarm.network_throughput.arn
}

output "sns_topic_arn" {
  description = "ARN of the alarm SNS topic, or null when notification_email is empty."
  value       = try(aws_sns_topic.alarm[0].arn, null)
}

output "file_server_alarm_arns" {
  description = "Opt-in file-server alarm ARNs keyed by '<metric_key>' or '<metric_key>/<file_server>'."
  value       = { for k, a in aws_cloudwatch_metric_alarm.file_server : k => a.arn }
}

output "volume_alarm_arns" {
  description = "Per-volume alarm ARNs: { capacity = { <volume_id> = arn }, inode = { <volume_id> = arn } }."
  value = {
    capacity = { for k, a in aws_cloudwatch_metric_alarm.volume_capacity : k => a.arn }
    inode    = { for k, a in aws_cloudwatch_metric_alarm.volume_inode : k => a.arn }
  }
}
