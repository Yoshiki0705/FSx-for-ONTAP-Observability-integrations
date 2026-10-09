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
  default     = "fsxn-ssd-auto-increase"
}

variable "file_system_id" {
  description = "Amazon FSx for NetApp ONTAP file system ID (fs-xxxxxxxxxxxxxxxxx) whose SSD capacity is managed."
  type        = string
}

variable "max_storage_capacity_gib" {
  description = "Required absolute SSD ceiling in GiB. Validated against the shape maximum."
  type        = number
}

variable "mode" {
  description = "notify_only (default) | approve | auto. auto requires COMPLIANCE archive mode."
  type        = string
  default     = "notify_only"
}

variable "trigger_threshold_percent" {
  description = "SSD utilization threshold (%) for the trigger alarm."
  type        = number
  default     = 80
}

variable "increase_percent" {
  description = "Requested increase percent (never below the 10% service minimum)."
  type        = number
  default     = 10
}

variable "reevaluation_schedule" {
  description = "EventBridge schedule expression for the hourly re-evaluation."
  type        = string
  default     = "rate(1 hour)"
}

variable "log_retention_days" {
  description = "Retention applied to both the decision log group and the function's own /aws/lambda log group."
  type        = number
  default     = 365
}

variable "indeterminate_reconcile_hours" {
  description = "Reconciliation window before escalation to manual disposition."
  type        = number
  default     = 6
}

variable "decision_archive_bucket" {
  description = "Existing S3 bucket with Object Lock default retention, administered outside this module."
  type        = string
}

variable "decision_archive_prefix" {
  description = "Key prefix for archive objects."
  type        = string
  default     = "fsx-ssd-auto-increase/"
}

variable "decision_archive_required_mode" {
  description = "COMPLIANCE (default) | GOVERNANCE. auto requires COMPLIANCE."
  type        = string
  default     = "COMPLIANCE"
}

variable "decision_archive_min_retention_days" {
  description = "Minimum retain-until period, in days, required on the bucket and each object."
  type        = number
  default     = 365
}

variable "aggregate_names" {
  description = "Optional second-generation Aggregate names; each adds one trigger alarm."
  type        = list(string)
  default     = []
}

variable "notification_email" {
  description = "Email address for the notification topic. Empty string skips the subscription."
  type        = string
  default     = ""
}

variable "tags" {
  description = "Tags applied to every taggable resource."
  type        = map(string)
  default     = {}
}
