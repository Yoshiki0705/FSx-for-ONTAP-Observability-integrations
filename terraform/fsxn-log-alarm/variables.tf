# Inputs express the parameters of shared/templates/cloudwatch-log-alarm.yaml
# (LogGroupName, DetectionType, TargetPattern, AlarmThreshold,
# EvaluationFrequencyMinutes, QueryResultsToEvaluate/ToAlarm, AlarmSnsTopicArn)
# as one data-driven `detections` map instead of a DetectionType enum. The
# mechanism is a metric filter plus a metric alarm per detection, not a native
# LogAlarm resource (see main.tf and the README for why).

variable "name_prefix" {
  description = "Prefix for the metric filter, alarm, and SNS topic names. Plays the role of the CloudFormation stack name in the template."
  type        = string
  default     = "fsxn-log-alarm"

  validation {
    condition     = can(regex("^[A-Za-z0-9_-]{1,64}$", var.name_prefix))
    error_message = "name_prefix must be 1-64 characters of letters, digits, '-' or '_'."
  }
}

variable "log_group_name" {
  description = "CloudWatch Logs log group that receives the FSx for ONTAP EMS and audit events (template LogGroupName, e.g. /syslog/fsxn-admin-audit). The module reads this group; it does not create it or the delivery path."
  type        = string

  validation {
    # CloudWatch Logs log-group names are 1-512 chars of these characters.
    condition     = can(regex("^[A-Za-z0-9_./#-]{1,512}$", var.log_group_name))
    error_message = "log_group_name must be 1-512 characters of letters, digits, '_', '/', '.', '#' or '-'."
  }
}

variable "metric_namespace" {
  description = "CloudWatch namespace for the count metrics the filters emit."
  type        = string
  default     = "FSxONTAP/LogAlarm"

  validation {
    condition     = can(regex("^[^:*$]{1,255}$", var.metric_namespace))
    error_message = "metric_namespace must be 1-255 characters and must not contain ':', '*' or '$'."
  }
}

variable "notification_email" {
  description = "Email address for alarm notifications. When set and alarm_sns_topic_arn is empty, the module creates an SNS topic and subscribes this address. Ignored when alarm_sns_topic_arn is set."
  type        = string
  default     = ""

  validation {
    condition     = var.notification_email == "" || can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.notification_email))
    error_message = "notification_email must be empty or an email address."
  }
}

variable "alarm_sns_topic_arn" {
  description = "ARN of an existing SNS topic for alarm actions. Mirrors the CloudFormation template's AlarmSnsTopicArn: the caller owns the topic, its encryption, subscriptions, and delivery policy. When set, the module creates no topic and ignores notification_email. Empty string uses notification_email instead."
  type        = string
  default     = ""

  validation {
    condition     = var.alarm_sns_topic_arn == "" || can(regex("^arn:aws[a-z-]*:sns:", var.alarm_sns_topic_arn))
    error_message = "alarm_sns_topic_arn must be empty or an SNS topic ARN."
  }
}

variable "sns_kms_master_key_id" {
  description = "KMS key id or alias for server-side encryption of the module-created SNS topic (only used when the module creates a topic). Empty string leaves the topic unencrypted, matching the console default for a new topic. 'alias/aws/sns' uses the AWS-managed key."
  type        = string
  default     = ""
}

variable "detections" {
  description = <<-EOT
    Map of detections. Each key names a metric filter plus a metric alarm. The
    default ships five recipes: autosize-fail (EMS wafl.vol.autoSize.fail),
    failed-access, bulk-delete, privileged-operations, unauthorized-access.
    Add a key with a `pattern` to cover the template's `custom` type. `pattern`
    is CloudWatch Logs filter-pattern syntax, not Logs Insights.
  EOT
  type = map(object({
    pattern             = string
    threshold           = optional(number, 0)
    comparison_operator = optional(string, "GreaterThanThreshold")
    evaluation_periods  = optional(number, 1)
    datapoints_to_alarm = optional(number, 1)
    period_seconds      = optional(number, 300)
    metric_value        = optional(string, "1")
    alarm_description   = optional(string, "")
  }))

  default = {
    # The EMS event this phase names explicitly. severity `error`, space
    # exhaustion imminent (docs/en/ems-detection-capabilities.md). Any single
    # occurrence should fire.
    autosize-fail = {
      pattern           = "\"wafl.vol.autoSize.fail\""
      threshold         = 0
      alarm_description = "FSx for ONTAP: volume auto-resize failed (EMS wafl.vol.autoSize.fail). Space exhaustion may be imminent; expand the volume or free space."
    }
    # Template failed-access-attempts: OR of failure/denied terms.
    failed-access = {
      pattern             = "?\"Failure\" ?\"denied\" ?\"DENIED\""
      threshold           = 10
      evaluation_periods  = 3
      datapoints_to_alarm = 3
      alarm_description   = "FSx for ONTAP: authentication or authorization failures above threshold. May indicate brute-force attempts or misconfigured permissions."
    }
    # Template bulk-delete-operations: OR of delete/remove terms.
    bulk-delete = {
      pattern             = "?\"DELETE\" ?\"delete\" ?\"remove\""
      threshold           = 50
      evaluation_periods  = 3
      datapoints_to_alarm = 2
      alarm_description   = "FSx for ONTAP: bulk file deletion detected. High deletion rates may indicate ransomware or an accidental recursive delete."
    }
    # Template specific-user-activity (privileged-user monitoring). Replace the
    # term with the user to watch.
    privileged-operations = {
      pattern             = "\"admin\""
      threshold           = 0
      evaluation_periods  = 3
      datapoints_to_alarm = 1
      alarm_description   = "FSx for ONTAP: activity by a monitored privileged user. Review the operation context."
    }
    # Template sensitive-file-access. Replace the term with the protected path.
    unauthorized-access = {
      pattern             = "\"/vol/data/confidential\""
      threshold           = 0
      evaluation_periods  = 3
      datapoints_to_alarm = 1
      alarm_description   = "FSx for ONTAP: access to a protected path. Investigate the user and operation context."
    }
  }

  validation {
    condition     = length(var.detections) > 0
    error_message = "detections must not be empty."
  }

  validation {
    condition     = alltrue([for k in keys(var.detections) : can(regex("^[A-Za-z0-9_-]{1,255}$", k))])
    error_message = "Each detections key must be 1-255 characters of letters, digits, '-' or '_'."
  }

  validation {
    condition     = alltrue([for d in values(var.detections) : contains([60, 300, 600, 900, 1800, 3600], d.period_seconds)])
    error_message = "Each detections period_seconds must be one of 60, 300, 600, 900, 1800, 3600 (the template's EvaluationFrequencyMinutes set, in seconds)."
  }

  validation {
    # evaluation_periods is N in M-out-of-N; the PutMetricAlarm API minimum is 1
    # and the source template caps it at 100 (QueryResultsToEvaluate 1..100).
    condition     = alltrue([for d in values(var.detections) : d.evaluation_periods == floor(d.evaluation_periods) && d.evaluation_periods >= 1 && d.evaluation_periods <= 100])
    error_message = "Each detections evaluation_periods must be an integer in 1-100 (the API minimum is 1; the template caps it at 100)."
  }

  validation {
    # datapoints_to_alarm is M in M-out-of-N; same API minimum and template cap.
    condition     = alltrue([for d in values(var.detections) : d.datapoints_to_alarm == floor(d.datapoints_to_alarm) && d.datapoints_to_alarm >= 1 && d.datapoints_to_alarm <= 100])
    error_message = "Each detections datapoints_to_alarm must be an integer in 1-100 (the API minimum is 1; the template caps it at 100)."
  }

  validation {
    condition     = alltrue([for d in values(var.detections) : d.datapoints_to_alarm <= d.evaluation_periods])
    error_message = "Each detections datapoints_to_alarm must be less than or equal to its evaluation_periods."
  }

  validation {
    condition     = alltrue([for d in values(var.detections) : contains(["GreaterThanThreshold", "GreaterThanOrEqualToThreshold", "LessThanThreshold", "LessThanOrEqualToThreshold"], d.comparison_operator)])
    error_message = "Each detections comparison_operator must be one of GreaterThanThreshold, GreaterThanOrEqualToThreshold, LessThanThreshold, LessThanOrEqualToThreshold."
  }

  validation {
    # alarm_name and the emitted metric name are both "${name_prefix}-${key}".
    # CloudWatch PutMetricAlarm limits AlarmName and MetricName to 255 chars, so
    # the combined length must stay within that. (name_prefix <= 64, so this
    # bites only for long detection keys.)
    condition     = alltrue([for k in keys(var.detections) : length("${var.name_prefix}-${k}") <= 255])
    error_message = "Each 'name_prefix-detectionkey' name must be at most 255 characters (the CloudWatch AlarmName and MetricName limit)."
  }
}

variable "tags" {
  description = "Tags applied to the alarms and the SNS topic. CloudWatch log metric filters do not take tags."
  type        = map(string)
  default     = {}
}
