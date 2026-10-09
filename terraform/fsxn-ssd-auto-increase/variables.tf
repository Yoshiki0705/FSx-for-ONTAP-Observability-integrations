# Inputs. The guard behaviour is specified in
# docs/en/capacity-automation-t4-design.md ("Guards", "Ceiling validation",
# "Module interface"). The cross-variable auto-requires-COMPLIANCE rule is a
# precondition in main.tf, not a variable validation, because a variable
# validation cannot read a second variable.

variable "name_prefix" {
  description = "Prefix for every resource name: <prefix>-evaluator, <prefix>-role, <prefix>-dlq, <prefix>-schedule, <prefix>-lock, <prefix>-trigger, <prefix>-notify and the alarm names. Plays the role of the CloudFormation stack name."
  type        = string
  default     = "fsxn-ssd-auto-increase"
  validation {
    # 48 keeps <prefix>-evaluator and <prefix>-role within the 64-character
    # Lambda function and IAM role name limits.
    condition     = can(regex("^[A-Za-z0-9_-]{1,48}$", var.name_prefix))
    error_message = "name_prefix must be 1-48 characters of letters, digits, '-' or '_'."
  }
}

variable "file_system_id" {
  description = "ID of the FSx for ONTAP file system whose SSD capacity is managed (fs-xxxxxxxxxxxxxxxxx). Used as the FileSystemId dimension and to scope fsx:UpdateFileSystem to one file system."
  type        = string
  validation {
    condition     = can(regex("^fs-[0-9a-f]{17}$", var.file_system_id))
    error_message = "file_system_id must match ^fs-[0-9a-f]{17}$."
  }
}

variable "max_storage_capacity_gib" {
  description = "Required absolute SSD ceiling in GiB. No default. The target never exceeds it. Validated here against the widest documented range, and again by the Lambda-resource precondition against the maximum for this file system's deployment type and HA-pair count."
  type        = number
  validation {
    condition = (
      var.max_storage_capacity_gib == floor(var.max_storage_capacity_gib) &&
      var.max_storage_capacity_gib >= 1024 &&
      var.max_storage_capacity_gib <= 1048576
    )
    error_message = "max_storage_capacity_gib must be a whole number from 1024 to 1048576 GiB."
  }
}

variable "mode" {
  description = "notify_only (default): compute and report, never call the API. approve: send an SNS email with the computed aws fsx update-file-system command for a person to run. auto: call UpdateFileSystem. auto requires decision_archive_required_mode = COMPLIANCE (enforced by a precondition)."
  type        = string
  default     = "notify_only"
  validation {
    condition     = contains(["notify_only", "approve", "auto"], var.mode)
    error_message = "mode must be one of notify_only, approve or auto."
  }
}

variable "trigger_threshold_percent" {
  description = "Threshold (%) for the StorageCapacityUtilization trigger alarm (StorageTier=SSD, DataType=All). The alarm fires when SSD utilization stays above this for the evaluation period."
  type        = number
  default     = 80
  validation {
    condition     = var.trigger_threshold_percent >= 1 && var.trigger_threshold_percent <= 100
    error_message = "trigger_threshold_percent must be between 1 and 100."
  }
}

variable "increase_percent" {
  description = "Requested increase as a percent of current capacity. The target is never below the documented 10% service minimum: target = min(ceiling, max(ceil(current*1.10), ceil(current*(1+increase_percent/100))))."
  type        = number
  default     = 10
  validation {
    condition     = var.increase_percent >= 10 && var.increase_percent <= 100 && floor(var.increase_percent) == var.increase_percent
    error_message = "increase_percent must be a whole number between 10 and 100."
  }
}

variable "reevaluation_schedule" {
  description = "EventBridge schedule expression for the hourly re-evaluation. Needed because CloudWatch alarm actions fire only on a state change, so an alarm staying in ALARM would not re-trigger the function."
  type        = string
  default     = "rate(1 hour)"
  validation {
    condition     = can(regex("^(rate\\(.+\\)|cron\\(.+\\))$", var.reevaluation_schedule))
    error_message = "reevaluation_schedule must be a rate(...) or cron(...) expression."
  }
}

variable "log_retention_days" {
  description = "Retention applied to both the decision log group (operational history) and the function's own /aws/lambda log group, one of the values CloudWatch Logs PutRetentionPolicy accepts. Neither is the audit record: the audit record is the S3 Object Lock archive."
  type        = number
  default     = 365
  validation {
    condition     = contains([1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1096, 1827, 2192, 2557, 2922, 3288, 3653], var.log_retention_days)
    error_message = "log_retention_days must be one of 1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1096, 1827, 2192, 2557, 2922, 3288, 3653."
  }
}

variable "indeterminate_reconcile_hours" {
  description = "Hours an indeterminate request (sent, acceptance unknown) is reconciled from AdministrativeActions before it escalates to manual_disposition_required. No new request token is issued before this window passes."
  type        = number
  default     = 6
  validation {
    condition     = var.indeterminate_reconcile_hours >= 1 && var.indeterminate_reconcile_hours <= 24 && floor(var.indeterminate_reconcile_hours) == var.indeterminate_reconcile_hours
    error_message = "indeterminate_reconcile_hours must be a whole number between 1 and 24."
  }
}

variable "decision_archive_bucket" {
  description = "Name of an existing S3 bucket with Object Lock default retention, administered outside this module. The function gets s3:PutObject on the prefix plus read-only retention checks; it never sets or changes retention and the module never creates the bucket."
  type        = string
  validation {
    # S3 bucket naming: 3-63 characters, lowercase letters, digits, hyphens and
    # dots, starting and ending with a letter or digit.
    condition     = can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.decision_archive_bucket))
    error_message = "decision_archive_bucket must be a valid S3 bucket name (3-63 characters, lowercase letters, digits, '-' or '.')."
  }
}

variable "decision_archive_prefix" {
  description = "Key prefix under which decision archive objects are written: <prefix><file-system-id>/<correlation-id>/<sequence>-<event>.json. The function's s3:PutObject and s3:GetObjectRetention are scoped to this prefix."
  type        = string
  default     = "fsx-ssd-auto-increase/"
  validation {
    # No '*' or '?': the prefix is interpolated into the execution-role S3
    # Resource ARN (which already ends in '*'), so an IAM/authorization
    # wildcard in the prefix would widen s3:PutObject and s3:GetObjectRetention
    # beyond the intended prefix. '!', '.', '_', '-', "'", '(', ')' and '/' are
    # S3-safe key characters that are not IAM policy wildcards.
    condition     = can(regex("^[A-Za-z0-9!_.'()/-]{1,512}$", var.decision_archive_prefix))
    error_message = "decision_archive_prefix must be 1-512 characters of S3-safe key characters, excluding the IAM wildcard characters '*' and '?'."
  }
}

variable "decision_archive_required_mode" {
  description = "Object Lock retention mode the archive bucket must provide. COMPLIANCE (default): no principal, including root, can delete or shorten a locked version. GOVERNANCE: a principal with s3:BypassGovernanceRetention can. auto requires COMPLIANCE; notify_only and approve accept either and the retention check fails open there."
  type        = string
  default     = "COMPLIANCE"
  validation {
    condition     = contains(["COMPLIANCE", "GOVERNANCE"], var.decision_archive_required_mode)
    error_message = "decision_archive_required_mode must be COMPLIANCE or GOVERNANCE."
  }
}

variable "decision_archive_min_retention_days" {
  description = "Minimum retain-until period, in days, the function requires on the bucket's default retention and on each written object version."
  type        = number
  default     = 365
  validation {
    condition     = var.decision_archive_min_retention_days >= 1 && floor(var.decision_archive_min_retention_days) == var.decision_archive_min_retention_days
    error_message = "decision_archive_min_retention_days must be a whole number of at least 1."
  }
}

variable "aggregate_names" {
  description = "Optional list of second-generation Aggregate names. Each adds one more StorageCapacityUtilization trigger alarm scoped to that Aggregate dimension. Empty by default, so a first-generation deployment wires exactly one trigger alarm."
  type        = list(string)
  default     = []
  validation {
    condition     = length(distinct(var.aggregate_names)) == length(var.aggregate_names)
    error_message = "aggregate_names must not contain duplicates."
  }
}

variable "notification_email" {
  description = "Email address for the notification topic (reports and approve-mode commands). Empty string skips the email subscription; the topic is still created."
  type        = string
  default     = ""
  validation {
    condition     = var.notification_email == "" || can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.notification_email))
    error_message = "notification_email must be empty or an email address."
  }
}

variable "tags" {
  description = "Tags applied to every taggable resource the module creates."
  type        = map(string)
  default     = {}
}
