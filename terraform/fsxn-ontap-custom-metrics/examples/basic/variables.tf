# Types and descriptions only. Validation and the full descriptions stay in the
# module (../../variables.tf), so each rule has a single definition. Defaults
# match the module's defaults.

variable "region" {
  description = "AWS Region of the file system, for example ap-northeast-1."
  type        = string
}

variable "name_prefix" {
  description = "Prefix for every resource name."
  type        = string
  default     = "fsxn-ontap-metrics"
}

variable "file_system_id" {
  description = "Amazon FSx for NetApp ONTAP file system ID (fs-xxxxxxxxxxxxxxxxx). For SnapMirror, the destination file system."
  type        = string
}

variable "ontap_management_ip" {
  description = "Management endpoint IPv4 address of the file system."
  type        = string
}

variable "ontap_credentials_secret_arn" {
  description = "Secrets Manager secret ARN with {\"username\", \"password\"} of an ONTAP user with the fsxadmin-readonly role."
  type        = string
}

variable "ontap_credentials_kms_key_arn" {
  description = "Customer managed KMS key ARN of the secret, or null for aws/secretsmanager."
  type        = string
  default     = null
}

variable "vpc_id" {
  description = "VPC of the Lambda function."
  type        = string
}

variable "subnet_ids" {
  description = "Private subnets for the Lambda function."
  type        = list(string)
}

variable "aws_api_egress_cidr_blocks" {
  description = "CIDR blocks the Lambda may reach on TCP 443 for CloudWatch and Secrets Manager."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "create_monitoring_endpoint" {
  description = "Create an interface endpoint for CloudWatch (monitoring). Leave false if the VPC has one."
  type        = bool
  default     = false
}

variable "create_secretsmanager_endpoint" {
  description = "Create an interface endpoint for Secrets Manager. Leave false if the VPC has one."
  type        = bool
  default     = false
}

variable "enable_qtree_collector" {
  description = "Publish qtree quota metrics."
  type        = bool
  default     = true
}

variable "enable_snapmirror_collector" {
  description = "Publish SnapMirror health and lag metrics."
  type        = bool
  default     = true
}

variable "qtree_svm_name" {
  description = "SVM for the qtree collector. Required when enable_qtree_collector is true."
  type        = string
  default     = null
}

variable "poll_interval_minutes" {
  description = "Minutes between polls (1-60)."
  type        = number
  default     = 5
}

variable "qtree_quota_threshold_percent" {
  description = "Qtree quota alarm threshold (%), 50-99."
  type        = number
  default     = 85
}

variable "snapmirror_lag_threshold_seconds" {
  description = "SnapMirror lag alarm threshold (seconds), 60-2592000."
  type        = number
  default     = 10800
}

variable "snapmirror_max_relationships" {
  description = "Relationships per run that get per-relationship series (1-1000)."
  type        = number
  default     = 100
}

variable "ca_cert_path" {
  description = "ONTAP CA certificate path inside ca_cert_layer_arn. Empty disables TLS verification."
  type        = string
  default     = ""
}

variable "ca_cert_layer_arn" {
  description = "Lambda layer version ARN containing the CA certificate."
  type        = string
  default     = ""
}

variable "log_retention_days" {
  description = "Retention of the Lambda log group."
  type        = number
  default     = 30
}

variable "notification_email" {
  description = "Email address for alarm notifications. Empty string skips SNS."
  type        = string
  default     = ""
}

variable "tags" {
  description = "Tags applied to every taggable resource."
  type        = map(string)
  default     = {}
}
