# Inputs mirror the parameters of shared/templates/fsxn-monitoring-dashboard.yaml
# (FileSystemId, FileSystemName, CapacityThresholdPercent, NotificationEmail).
# Everything after those four is either a deliberate parameterization of a
# value the template hardcodes or an opt-in alarm that the template does not have.

variable "name_prefix" {
  description = "Prefix for the dashboard, alarm, and SNS topic names. Plays the role of the CloudFormation stack name in the template."
  type        = string
  default     = "fsxn-monitoring"

  validation {
    condition     = can(regex("^[A-Za-z0-9_-]{1,64}$", var.name_prefix))
    error_message = "name_prefix must be 1-64 characters of letters, digits, '-' or '_'."
  }
}

variable "file_system_id" {
  description = "FSx for ONTAP file system ID (fs-xxxxxxxxxxxxxxxxx)."
  type        = string

  validation {
    condition     = can(regex("^fs-[0-9a-f]{17}$", var.file_system_id))
    error_message = "file_system_id must match ^fs-[0-9a-f]{17}$."
  }
}

variable "file_system_name" {
  description = "Human-readable file system name, used in the dashboard name and title."
  type        = string
  default     = "fsx-for-ontap"

  validation {
    # CloudWatch dashboard names allow only alphanumerics, '-' and '_'.
    condition     = can(regex("^[A-Za-z0-9_-]{1,64}$", var.file_system_name))
    error_message = "file_system_name must be 1-64 characters of letters, digits, '-' or '_'."
  }
}

variable "capacity_threshold_percent" {
  description = "Storage capacity utilization threshold (%) for the capacity alarm. Range 50-95, copied from the template's MinValue/MaxValue."
  type        = number
  default     = 80

  validation {
    condition     = var.capacity_threshold_percent >= 50 && var.capacity_threshold_percent <= 95
    error_message = "capacity_threshold_percent must be between 50 and 95."
  }
}

variable "throughput_threshold_percent" {
  description = "Network throughput utilization threshold (%). The CloudFormation template hardcodes 80; this module exposes it with the same default."
  type        = number
  default     = 80

  validation {
    condition     = var.throughput_threshold_percent >= 1 && var.throughput_threshold_percent <= 100
    error_message = "throughput_threshold_percent must be between 1 and 100."
  }
}

variable "notification_email" {
  description = "Email address for alarm notifications. Empty string skips the SNS topic and subscription."
  type        = string
  default     = ""

  validation {
    condition     = var.notification_email == "" || can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.notification_email))
    error_message = "notification_email must be empty or an email address."
  }
}

# --- Opt-in file-server alarms (all off by default) ---

variable "enable_cpu_utilization_alarm" {
  description = "Create an alarm on CPUUtilization."
  type        = bool
  default     = false
}

variable "cpu_utilization_threshold_percent" {
  description = "Threshold (%) for the CPUUtilization alarm."
  type        = number
  default     = 80

  validation {
    condition     = var.cpu_utilization_threshold_percent >= 1 && var.cpu_utilization_threshold_percent <= 100
    error_message = "cpu_utilization_threshold_percent must be between 1 and 100."
  }
}

variable "enable_disk_iops_utilization_alarm" {
  description = "Create an alarm on FileServerDiskIopsUtilization."
  type        = bool
  default     = false
}

variable "disk_iops_utilization_threshold_percent" {
  description = "Threshold (%) for the FileServerDiskIopsUtilization alarm."
  type        = number
  default     = 80

  validation {
    condition     = var.disk_iops_utilization_threshold_percent >= 1 && var.disk_iops_utilization_threshold_percent <= 100
    error_message = "disk_iops_utilization_threshold_percent must be between 1 and 100."
  }
}

variable "enable_disk_throughput_utilization_alarm" {
  description = "Create an alarm on FileServerDiskThroughputUtilization."
  type        = bool
  default     = false
}

variable "disk_throughput_utilization_threshold_percent" {
  description = "Threshold (%) for the FileServerDiskThroughputUtilization alarm."
  type        = number
  default     = 80

  validation {
    condition     = var.disk_throughput_utilization_threshold_percent >= 1 && var.disk_throughput_utilization_threshold_percent <= 100
    error_message = "disk_throughput_utilization_threshold_percent must be between 1 and 100."
  }
}

variable "file_server_names" {
  description = "FileServer dimension values for second-generation file systems (format from the AWS doc example: FsxId01234567890abcdef-01). Empty: one alarm per enabled file-server metric with FileSystemId only (first-generation dimension set). Non-empty: one alarm per metric and file server."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for s in var.file_server_names : can(regex("^FsxId[0-9a-f]+-[0-9]+$", s))])
    error_message = "Each file_server_names entry must match ^FsxId[0-9a-f]+-[0-9]+$."
  }

  validation {
    condition     = length(distinct(var.file_server_names)) == length(var.file_server_names)
    error_message = "file_server_names must not contain duplicates."
  }
}

# --- Opt-in per-volume alarms ---

variable "volume_ids" {
  description = "Volume IDs to alarm on. Each volume gets a StorageCapacityUtilization alarm and an inode utilization alarm (2 alarms per volume)."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for v in var.volume_ids : can(regex("^fsvol-[0-9a-f]{17}$", v))])
    error_message = "Each volume_ids entry must match ^fsvol-[0-9a-f]{17}$."
  }

  validation {
    condition     = length(distinct(var.volume_ids)) == length(var.volume_ids)
    error_message = "volume_ids must not contain duplicates."
  }
}

variable "volume_capacity_threshold_percent" {
  description = "Threshold (%) for the per-volume StorageCapacityUtilization alarms."
  type        = number
  default     = 80

  validation {
    condition     = var.volume_capacity_threshold_percent >= 1 && var.volume_capacity_threshold_percent <= 100
    error_message = "volume_capacity_threshold_percent must be between 1 and 100."
  }
}

variable "volume_inode_threshold_percent" {
  description = "Threshold (%) for the per-volume inode utilization alarms (100 * FilesUsed / FilesCapacity)."
  type        = number
  default     = 80

  validation {
    condition     = var.volume_inode_threshold_percent >= 1 && var.volume_inode_threshold_percent <= 100
    error_message = "volume_inode_threshold_percent must be between 1 and 100."
  }
}

variable "tags" {
  description = "Tags applied to the alarms and the SNS topic. CloudWatch dashboards do not take tags."
  type        = map(string)
  default     = {}
}
