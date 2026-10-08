# Inputs. The qtree inputs mirror shared/templates/qtree-quota-monitor.yaml
# (OntapMgmtIp, OntapCredentialsSecretArn, SvmName, SubnetIds,
# PollIntervalMinutes, QuotaThresholdPercent, NotificationEmail, CaCertPath,
# CaCertLayerArn). The template's SecurityGroupId has no counterpart: this
# module creates the Lambda security group itself (vpc_id).

variable "name_prefix" {
  description = "Prefix for every resource name: <prefix>-poller, <prefix>-role, <prefix>-dlq, <prefix>-schedule, <prefix>-lambda, <prefix>-endpoints, <prefix>-alarms and the alarm names. Plays the role of the CloudFormation stack name."
  type        = string
  default     = "fsxn-ontap-metrics"
  validation {
    # 48 keeps <prefix>-poller and <prefix>-role within the 64-character
    # Lambda function and IAM role name limits.
    condition     = can(regex("^[A-Za-z0-9_-]{1,48}$", var.name_prefix))
    error_message = "name_prefix must be 1-48 characters of letters, digits, '-' or '_'."
  }
}

variable "file_system_id" {
  description = "ID of the FSx for ONTAP file system whose ONTAP REST API is polled (fs-xxxxxxxxxxxxxxxxx). Used as the FileSystemId dimension. For SnapMirror, this is the destination file system."
  type        = string
  validation {
    condition     = can(regex("^fs-[0-9a-f]{17}$", var.file_system_id))
    error_message = "file_system_id must match ^fs-[0-9a-f]{17}$."
  }
}

variable "ontap_management_ip" {
  description = "IPv4 address of the file system management endpoint (OntapConfiguration.Endpoints.Management.IpAddresses). The Lambda security group allows TCP 443 to this address only."
  type        = string
  validation {
    condition = (
      length(split(".", var.ontap_management_ip)) == 4 &&
      alltrue([
        for o in split(".", var.ontap_management_ip) :
        can(regex("^[0-9]{1,3}$", o)) ? tonumber(o) <= 255 : false
      ])
    )
    error_message = "ontap_management_ip must be a dotted-quad IPv4 address with octets 0-255."
  }
}

variable "ontap_credentials_secret_arn" {
  description = "ARN of the Secrets Manager secret holding {\"username\": ..., \"password\": ...} for an ONTAP user with the fsxadmin-readonly role. Only the ARN reaches the Lambda environment; the password is read at run time."
  type        = string
  validation {
    condition     = can(regex("^arn:aws[a-z-]*:secretsmanager:[a-z0-9-]+:[0-9]{12}:secret:.+$", var.ontap_credentials_secret_arn))
    error_message = "ontap_credentials_secret_arn must be a Secrets Manager secret ARN."
  }
}

variable "ontap_credentials_kms_key_arn" {
  description = "ARN of the customer managed KMS key that encrypts the secret, or null when the secret uses the AWS managed key aws/secretsmanager. When set, the Lambda role gets kms:Decrypt on this key through Secrets Manager only."
  type        = string
  default     = null
  validation {
    # Key ID is either a UUID (single-Region key) or mrk- followed by 32 hex
    # characters (multi-Region key).
    condition     = var.ontap_credentials_kms_key_arn == null || can(regex("^arn:aws[a-z-]*:kms:[a-z0-9-]+:[0-9]{12}:key/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|mrk-[0-9a-f]{32})$", var.ontap_credentials_kms_key_arn))
    error_message = "ontap_credentials_kms_key_arn must be null or a KMS key ARN (arn:aws:kms:<region>:<account>:key/<key-id>), where <key-id> is a UUID or a multi-Region key ID (mrk- followed by 32 hex characters)."
  }
}

variable "vpc_id" {
  description = "VPC of the Lambda function. The module creates the Lambda security group here."
  type        = string
  validation {
    condition     = can(regex("^vpc-[0-9a-f]{8,17}$", var.vpc_id))
    error_message = "vpc_id must match ^vpc-[0-9a-f]{8,17}$."
  }
}

variable "subnet_ids" {
  description = "Private subnets for the Lambda function (and for the interface endpoints when the module creates them). They need a route to the management endpoint and, without module-created endpoints, a path to CloudWatch and Secrets Manager (NAT gateway or existing interface endpoints)."
  type        = list(string)
  validation {
    condition     = length(var.subnet_ids) >= 1
    error_message = "subnet_ids must contain at least one subnet."
  }
  validation {
    condition     = alltrue([for s in var.subnet_ids : can(regex("^subnet-[0-9a-f]{8,17}$", s))])
    error_message = "Each subnet_ids entry must match ^subnet-[0-9a-f]{8,17}$."
  }
  validation {
    condition     = length(distinct(var.subnet_ids)) == length(var.subnet_ids)
    error_message = "subnet_ids must not contain duplicates."
  }
}

variable "aws_api_egress_cidr_blocks" {
  description = "IPv4 CIDR blocks the Lambda security group allows TCP 443 to, for CloudWatch and Secrets Manager. The default 0.0.0.0/0 suits a NAT gateway path. With existing interface endpoints, the VPC CIDR is enough. Use [] when the module creates both endpoints."
  type        = list(string)
  default     = ["0.0.0.0/0"]
  validation {
    condition     = alltrue([for c in var.aws_api_egress_cidr_blocks : can(cidrnetmask(c))])
    error_message = "Each aws_api_egress_cidr_blocks entry must be an IPv4 CIDR block."
  }
  validation {
    condition     = length(distinct(var.aws_api_egress_cidr_blocks)) == length(var.aws_api_egress_cidr_blocks)
    error_message = "aws_api_egress_cidr_blocks must not contain duplicates."
  }
}

variable "create_monitoring_endpoint" {
  description = "Create an interface VPC endpoint for com.amazonaws.<region>.monitoring (CloudWatch PutMetricData) with private DNS. Leave false when the VPC already has one: two interface endpoints with private DNS for the same service cannot coexist in one VPC."
  type        = bool
  default     = false
}

variable "create_secretsmanager_endpoint" {
  description = "Create an interface VPC endpoint for com.amazonaws.<region>.secretsmanager with private DNS. Leave false when the VPC already has one (same conflict as create_monitoring_endpoint)."
  type        = bool
  default     = false
}

variable "enable_qtree_collector" {
  description = "Publish qtree quota metrics (namespace FSxONTAP/Qtree) for qtree_svm_name."
  type        = bool
  default     = true
}

variable "enable_snapmirror_collector" {
  description = "Publish SnapMirror health and lag metrics (namespace FSxONTAP/SnapMirror) for the relationships whose destination is this file system."
  type        = bool
  default     = true
  validation {
    condition     = var.enable_snapmirror_collector || var.enable_qtree_collector
    error_message = "Enable at least one of enable_qtree_collector and enable_snapmirror_collector."
  }
}

variable "qtree_svm_name" {
  description = "SVM whose tree quota report the qtree collector reads (SvmName dimension). Required when enable_qtree_collector is true."
  type        = string
  default     = null
  validation {
    condition     = !var.enable_qtree_collector || (try(length(var.qtree_svm_name), 0) >= 1 && try(length(var.qtree_svm_name), 0) <= 66)
    error_message = "qtree_svm_name must be 1-66 characters when enable_qtree_collector is true (66 is the template's SvmName MaxLength)."
  }
}

variable "poll_interval_minutes" {
  description = "Minutes between polls (1-60, as the template's PollIntervalMinutes). Every custom-metric and Lambda alarm uses a period of max(300, 60 x this value) seconds. The function has a reserved concurrency of 1, so a poll that would overlap a running one is throttled and retried by Lambda instead of running in parallel."
  type        = number
  default     = 5
  validation {
    condition     = var.poll_interval_minutes >= 1 && var.poll_interval_minutes <= 60 && floor(var.poll_interval_minutes) == var.poll_interval_minutes
    error_message = "poll_interval_minutes must be a whole number between 1 and 60."
  }
}

variable "qtree_quota_threshold_percent" {
  description = "Threshold (%) for the QtreeQuotaUsedPercentMax alarm. 50-99, as the template's QuotaThresholdPercent."
  type        = number
  default     = 85
  validation {
    condition     = var.qtree_quota_threshold_percent >= 50 && var.qtree_quota_threshold_percent <= 99
    error_message = "qtree_quota_threshold_percent must be between 50 and 99."
  }
}

variable "snapmirror_lag_threshold_seconds" {
  description = "Threshold (seconds) for the SnapMirrorLagSecondsMax alarm. Default 10800 is 3 x a 1-hour transfer schedule; set it from your own schedule (see docs/en/sizing-and-headroom.md)."
  type        = number
  default     = 10800
  validation {
    condition     = var.snapmirror_lag_threshold_seconds >= 60 && var.snapmirror_lag_threshold_seconds <= 2592000
    error_message = "snapmirror_lag_threshold_seconds must be between 60 and 2592000 (30 days)."
  }
}

variable "snapmirror_max_relationships" {
  description = "Maximum relationships per run that get per-relationship series (2 series each). Aggregates always cover every relationship read; SnapMirrorRelationshipsTruncated reports 1 when the cap applied or a relationship with an empty path got no series."
  type        = number
  default     = 100
  validation {
    condition     = var.snapmirror_max_relationships >= 1 && var.snapmirror_max_relationships <= 1000 && floor(var.snapmirror_max_relationships) == var.snapmirror_max_relationships
    error_message = "snapmirror_max_relationships must be a whole number between 1 and 1000."
  }
}

variable "ca_cert_path" {
  description = "Path of the ONTAP CA certificate inside ca_cert_layer_arn, for example /opt/certs/ontap-ca.pem. Empty disables TLS verification (CERT_NONE, with a warning in the log), which is acceptable for a PoC only."
  type        = string
  default     = ""
  validation {
    condition     = (var.ca_cert_path == "") == (var.ca_cert_layer_arn == "")
    error_message = "Set ca_cert_path and ca_cert_layer_arn together, or leave both empty."
  }
}

variable "ca_cert_layer_arn" {
  description = "ARN (with version) of a Lambda layer that contains the CA certificate at ca_cert_path."
  type        = string
  default     = ""
  validation {
    condition     = var.ca_cert_layer_arn == "" || can(regex("^arn:aws[a-z-]*:lambda:[a-z0-9-]+:[0-9]{12}:layer:[A-Za-z0-9_-]+:[0-9]+$", var.ca_cert_layer_arn))
    error_message = "ca_cert_layer_arn must be empty or a Lambda layer version ARN."
  }
}

variable "log_retention_days" {
  description = "Retention of the Lambda log group, one of the values CloudWatch Logs PutRetentionPolicy accepts."
  type        = number
  default     = 30
  validation {
    condition     = contains([1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1096, 1827, 2192, 2557, 2922, 3288, 3653], var.log_retention_days)
    error_message = "log_retention_days must be one of 1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1096, 1827, 2192, 2557, 2922, 3288, 3653."
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

variable "tags" {
  description = "Tags applied to every taggable resource the module creates."
  type        = map(string)
  default     = {}
}
