output "dashboard_name" {
  description = "CloudWatch dashboard name."
  value       = module.fsx_ontap_monitoring.dashboard_name
}

output "dashboard_url" {
  description = "Direct URL to the CloudWatch dashboard."
  value       = module.fsx_ontap_monitoring.dashboard_url
}

output "capacity_alarm_arn" {
  description = "ARN of the storage capacity alarm."
  value       = module.fsx_ontap_monitoring.capacity_alarm_arn
}

output "throughput_alarm_arn" {
  description = "ARN of the network throughput utilization alarm."
  value       = module.fsx_ontap_monitoring.throughput_alarm_arn
}

output "sns_topic_arn" {
  description = "ARN of the alarm SNS topic, or null when notification_email is empty."
  value       = module.fsx_ontap_monitoring.sns_topic_arn
}

output "file_server_alarm_arns" {
  description = "Opt-in file-server alarm ARNs."
  value       = module.fsx_ontap_monitoring.file_server_alarm_arns
}

output "volume_alarm_arns" {
  description = "Per-volume alarm ARNs."
  value       = module.fsx_ontap_monitoring.volume_alarm_arns
}
