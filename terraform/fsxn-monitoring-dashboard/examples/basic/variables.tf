# Types and descriptions only. Validation and the full descriptions stay in the
# module (../../variables.tf), so each rule has a single definition. Defaults
# match the module's defaults.

variable "region" {
  description = "AWS Region of the file system, for example ap-northeast-1."
  type        = string
}

variable "file_system_id" {
  description = "Amazon FSx for NetApp ONTAP file system ID (fs-xxxxxxxxxxxxxxxxx)."
  type        = string
}

variable "file_system_name" {
  description = "Human-readable file system name, used in the dashboard name and title."
  type        = string
  default     = "fsx-for-ontap"
}

variable "name_prefix" {
  description = "Prefix for the dashboard, alarm, and SNS topic names."
  type        = string
  default     = "fsxn-monitoring"
}

variable "capacity_threshold_percent" {
  description = "Storage capacity utilization threshold (%) for the capacity alarm (50-95)."
  type        = number
  default     = 80
}

variable "notification_email" {
  description = "Email address for alarm notifications. Empty string skips the SNS topic and subscription."
  type        = string
  default     = ""
}

variable "enable_cpu_utilization_alarm" {
  description = "Create an alarm on CPUUtilization."
  type        = bool
  default     = false
}

variable "enable_disk_iops_utilization_alarm" {
  description = "Create an alarm on FileServerDiskIopsUtilization."
  type        = bool
  default     = false
}

variable "enable_disk_throughput_utilization_alarm" {
  description = "Create an alarm on FileServerDiskThroughputUtilization."
  type        = bool
  default     = false
}

variable "file_server_names" {
  description = "FileServer dimension values for second-generation file systems. Empty uses FileSystemId only."
  type        = list(string)
  default     = []
}

variable "volume_ids" {
  description = "Volume IDs to alarm on (2 alarms per volume)."
  type        = list(string)
  default     = []
}

variable "tags" {
  description = "Tags applied to the alarms and the SNS topic."
  type        = map(string)
  default     = {}
}
