# Types and descriptions only. Validation and the full descriptions stay in the
# module (../../variables.tf), so each rule has a single definition. Defaults
# match the module's defaults.

variable "region" {
  description = "AWS Region of the log group, for example ap-northeast-1."
  type        = string
}

variable "log_group_name" {
  description = "CloudWatch Logs log group that receives the FSx for ONTAP EMS and audit events."
  type        = string
}

variable "name_prefix" {
  description = "Prefix for the metric filter, alarm, and SNS topic names."
  type        = string
  default     = "fsxn-log-alarm"
}

variable "notification_email" {
  description = "Email address for alarm notifications. The module creates and subscribes a topic when this is set and alarm_sns_topic_arn is empty."
  type        = string
  default     = ""
}

variable "alarm_sns_topic_arn" {
  description = "ARN of an existing caller-owned SNS topic for alarm actions (template AlarmSnsTopicArn). When set, the module creates no topic."
  type        = string
  default     = ""
}

variable "sns_kms_master_key_id" {
  description = "KMS key id or alias for the module-created SNS topic. Empty leaves it unencrypted."
  type        = string
  default     = ""
}

variable "tags" {
  description = "Tags applied to the alarms and the SNS topic."
  type        = map(string)
  default     = {}
}
