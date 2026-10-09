# Phase T4 of docs/en/monitoring-design.md: a guarded SSD auto-increase for one
# Amazon FSx for NetApp ONTAP file system. The behaviour is specified in
# docs/en/capacity-automation-t4-design.md; this file is the HCL half (the
# deploy-time ceiling checks, the auto-requires-COMPLIANCE precondition, and the
# infrastructure). The run-time guards, the lock lifecycle, the ceiling re-check
# and the Object Lock archive writes are the Python half in
# shared/lambda/ssd_auto_increase/.
#
# The module shares no runtime code with T2 (fsxn-ontap-custom-metrics): T2 is
# an ONTAP-REST poller inside a VPC; T4 calls AWS APIs only and runs outside any
# VPC, so it has no security groups and no interface endpoints.

data "aws_region" "current" {}
data "aws_partition" "current" {}
data "aws_caller_identity" "current" {}

# Reads deployment_type, ha_pairs and storage_capacity for the deploy-time
# ceiling precondition and the check block. The run-time ceiling re-check in the
# Lambda does not depend on this data source.
data "aws_fsx_ontap_file_system" "target" {
  id = var.file_system_id
}

locals {
  region     = data.aws_region.current.region
  partition  = data.aws_partition.current.partition
  account_id = data.aws_caller_identity.current.account_id

  function_name  = "${var.name_prefix}-evaluator"
  log_group_name = "/aws/lambda/${local.function_name}"

  # Operational history log group (not the audit record).
  decision_log_group_name = "/fsx/ssd-auto-increase/${var.file_system_id}"

  file_system_arn    = "arn:${local.partition}:fsx:${local.region}:${local.account_id}:file-system/${var.file_system_id}"
  archive_bucket_arn = "arn:${local.partition}:s3:::${var.decision_archive_bucket}"
  archive_prefix_arn = "arn:${local.partition}:s3:::${var.decision_archive_bucket}/${var.decision_archive_prefix}*"

  # Per-file-system SSD maximum by deployment shape (FSx for ONTAP quotas page,
  # documented, read 2026-10-08). A map lookup on an unknown deployment_type
  # fails the plan on purpose: a deployment type this design does not list
  # should stop the module rather than fall back to a default maximum.
  shape_max_gib = {
    SINGLE_AZ_1 = 196608
    MULTI_AZ_1  = 196608
    MULTI_AZ_2  = 524288
    SINGLE_AZ_2 = min(524288 * data.aws_fsx_ontap_file_system.target.ha_pairs, 1048576)
  }[data.aws_fsx_ontap_file_system.target.deployment_type]

  # Passed to the function; a blocked latch clears when this value changes.
  config_fingerprint = sha256(jsonencode({
    ceiling          = var.max_storage_capacity_gib
    increase_percent = var.increase_percent
    mode             = var.mode
    archive_mode     = var.decision_archive_required_mode
  }))

  # Trigger alarms: one file-system-level alarm, plus one per Aggregate on
  # second generation. Keyed so trigger_alarm_arns and the DescribeAlarms-scope
  # of the IAM policy can enumerate them.
  trigger_alarm_names = merge(
    { "file-system" = "${var.name_prefix}-ssd-utilization" },
    { for a in var.aggregate_names : "aggregate/${a}" => "${var.name_prefix}-ssd-utilization-${a}" },
  )
  trigger_alarm_arns = {
    for k, name in local.trigger_alarm_names :
    k => "arn:${local.partition}:cloudwatch:${local.region}:${local.account_id}:alarm:${name}"
  }

  notify_subscription = var.notification_email != ""
}

# ----------------------------------------------------------------
# Lambda package, built at plan time from the shared source directory
# ----------------------------------------------------------------
# With a //subdirectory git or archive source, Terraform extracts the whole
# package, so ../../shared resolves. A sparse checkout must include
# shared/lambda/ssd_auto_increase (see "Obtaining the module" in the README).
data "archive_file" "lambda" {
  type        = "zip"
  source_dir  = "${path.module}/../../shared/lambda/ssd_auto_increase"
  output_path = "${path.module}/.build/ssd-auto-increase-lambda.zip"
  # Same checksum on every platform, so a plan from another OS shows no diff.
  output_file_mode = "0666"
  excludes = [
    "tests",
    "tests/**",
    "__pycache__",
    "__pycache__/**",
    "**/__pycache__",
    "**/__pycache__/**",
  ]
}

# ----------------------------------------------------------------
# Decision log group, dead-letter queue, single-flight lock table
# ----------------------------------------------------------------
resource "aws_cloudwatch_log_group" "decision" {
  name              = local.decision_log_group_name
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

# The function's standard logger output (including the AccessDenied line the
# live IAM-deny plan relies on) goes to the Lambda default log group
# /aws/lambda/<function-name>. The execution role has CreateLogStream and
# PutLogEvents on this ARN but not CreateLogGroup, so the group must exist
# before the first invocation or standard logs have no destination. T2
# (fsxn-ontap-custom-metrics) creates its default group the same way.
resource "aws_cloudwatch_log_group" "function" {
  name              = local.log_group_name
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

# The schedule invokes the function asynchronously, so a failing invocation is
# retried twice by Lambda and then lands here.
resource "aws_sqs_queue" "dlq" {
  name                      = "${var.name_prefix}-dlq"
  message_retention_seconds = 1209600
  kms_master_key_id         = "alias/aws/sqs"
  tags                      = var.tags
}

# Single-flight lock, keyed on the file system ID. The lock condition logic is
# Python (a DynamoDB conditional put); HCL only creates the table.
#
# TTL is NOT enabled on expires_at. expires_at is the conditional takeover
# lease value the Python lock compares (expires_at < now), and several durable
# states (submitted, optimizing, indeterminate, manual_disposition_required,
# blocked) carry an expires_at only so a lease-based takeover of a wedged item
# stays possible; the design requires those states to persist until a terminal
# action, reconciliation, an operator disposition or a configuration change.
# A DynamoDB TTL on expires_at would make those durable items eligible for
# service-side deletion roughly six minutes after the handling invocation,
# removing the same-token barrier (indeterminate), the latch (blocked) or the
# one-request/one-terminal-event chain (submitted/optimizing). The lock never
# depends on when a row is physically deleted, so no TTL is configured; items
# are released by the explicit conditional delete in LockStore.release.
resource "aws_dynamodb_table" "lock" {
  name         = "${var.name_prefix}-lock"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "file_system_id"

  attribute {
    name = "file_system_id"
    type = "S"
  }

  tags = var.tags
}

# ----------------------------------------------------------------
# IAM
# ----------------------------------------------------------------
resource "aws_iam_role" "lambda" {
  name = "${var.name_prefix}-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  tags = var.tags
}

# jsonencode rather than data "aws_iam_policy_document": a mock provider
# returns a random string for data sources, which would leave the offline
# tests nothing to assert on.
resource "aws_iam_role_policy" "lambda" {
  name = "${var.name_prefix}-policy"
  role = aws_iam_role.lambda.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # Scoped to the one file system. The Service Authorization Reference
        # lists file-system* as the resource type for fsx:UpdateFileSystem.
        Sid      = "UpdateFileSystem"
        Effect   = "Allow"
        Action   = "fsx:UpdateFileSystem"
        Resource = local.file_system_arn
      },
      {
        # No resource type in the Service Authorization Reference, so "*".
        Sid      = "DescribeFileSystems"
        Effect   = "Allow"
        Action   = "fsx:DescribeFileSystems"
        Resource = "*"
      },
      {
        Sid      = "DescribeAlarms"
        Effect   = "Allow"
        Action   = "cloudwatch:DescribeAlarms"
        Resource = values(local.trigger_alarm_arns)
      },
      {
        # The report's utilization value (shared/lambda/ssd_auto_increase/
        # utilization.py reads the same StorageCapacityUtilization series as
        # the trigger alarms). The Service Authorization Reference lists only
        # the optional `dataset` resource type, for OTLP datasets; a classic
        # AWS/FSx metric has no ARN to scope to, so "*".
        Sid      = "GetMetricData"
        Effect   = "Allow"
        Action   = "cloudwatch:GetMetricData"
        Resource = "*"
      },
      {
        Sid      = "PublishReports"
        Effect   = "Allow"
        Action   = "sns:Publish"
        Resource = aws_sns_topic.notify.arn
      },
      {
        Sid    = "LockTable"
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "dynamodb:DeleteItem",
        ]
        Resource = aws_dynamodb_table.lock.arn
      },
      {
        # Write and read-only retention proof on the archive prefix. No
        # DeleteObject, PutObjectRetention or BypassGovernanceRetention: the
        # function proves retention, it never sets or removes it.
        Sid    = "ArchiveWrite"
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:GetObjectRetention",
        ]
        Resource = local.archive_prefix_arn
      },
      {
        Sid      = "ArchiveLockConfig"
        Effect   = "Allow"
        Action   = "s3:GetBucketObjectLockConfiguration"
        Resource = local.archive_bucket_arn
      },
      {
        Sid      = "DeadLetterQueue"
        Effect   = "Allow"
        Action   = "sqs:SendMessage"
        Resource = aws_sqs_queue.dlq.arn
      },
      {
        Sid    = "Logs"
        Effect = "Allow"
        Action = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = [
          "${aws_cloudwatch_log_group.decision.arn}:*",
          "arn:${local.partition}:logs:${local.region}:${local.account_id}:log-group:${local.log_group_name}:*",
        ]
      },
    ]
  })
}

# ----------------------------------------------------------------
# Lambda function and schedule
# ----------------------------------------------------------------
resource "aws_lambda_function" "evaluator" {
  function_name    = local.function_name
  description      = "Guarded SSD auto-increase evaluator for ${var.file_system_id} (mode ${var.mode})"
  role             = aws_iam_role.lambda.arn
  runtime          = "python3.12"
  handler          = "ssd_auto_increase_handler.lambda_handler"
  filename         = data.archive_file.lambda.output_path
  source_code_hash = data.archive_file.lambda.output_base64sha256
  memory_size      = 256
  timeout          = 300
  # Single-flight: one evaluator at a time, as the second layer of the lock
  # (the DynamoDB conditional put is the first).
  reserved_concurrent_executions = 1

  dead_letter_config {
    target_arn = aws_sqs_queue.dlq.arn
  }

  environment {
    variables = merge(
      {
        FILE_SYSTEM_ID                      = var.file_system_id
        MODE                                = var.mode
        MAX_STORAGE_CAPACITY_GIB            = tostring(var.max_storage_capacity_gib)
        INCREASE_PERCENT                    = tostring(var.increase_percent)
        TRIGGER_ALARM_NAMES                 = join(",", values(local.trigger_alarm_names))
        LOCK_TABLE_NAME                     = aws_dynamodb_table.lock.name
        NOTIFY_TOPIC_ARN                    = aws_sns_topic.notify.arn
        DECISION_LOG_GROUP                  = local.decision_log_group_name
        DECISION_ARCHIVE_BUCKET             = var.decision_archive_bucket
        DECISION_ARCHIVE_PREFIX             = var.decision_archive_prefix
        DECISION_ARCHIVE_REQUIRED_MODE      = var.decision_archive_required_mode
        DECISION_ARCHIVE_MIN_RETENTION_DAYS = tostring(var.decision_archive_min_retention_days)
        INDETERMINATE_RECONCILE_HOURS       = tostring(var.indeterminate_reconcile_hours)
        CONFIG_FINGERPRINT                  = local.config_fingerprint
      },
      # The per-aggregate utilization series the report reads, matching the
      # trigger_aggregate alarms. Set only on second generation (non-empty
      # aggregate_names), so first generation carries no empty value.
      length(var.aggregate_names) > 0 ? { AGGREGATE_NAMES = join(",", var.aggregate_names) } : {},
    )
  }

  lifecycle {
    precondition {
      condition     = var.max_storage_capacity_gib <= local.shape_max_gib
      error_message = "max_storage_capacity_gib exceeds the SSD maximum for this deployment type and HA-pair count."
    }
    precondition {
      condition     = var.mode != "auto" || var.decision_archive_required_mode == "COMPLIANCE"
      error_message = "mode = auto requires decision_archive_required_mode = COMPLIANCE."
    }
  }

  tags = var.tags

  depends_on = [
    aws_cloudwatch_log_group.decision,
    aws_cloudwatch_log_group.function,
    aws_iam_role_policy.lambda,
  ]
}

resource "aws_cloudwatch_event_rule" "schedule" {
  name                = "${var.name_prefix}-schedule"
  description         = "Re-evaluate SSD capacity of ${var.file_system_id} on ${var.reevaluation_schedule}"
  schedule_expression = var.reevaluation_schedule
  state               = "ENABLED"
  tags                = var.tags
}

resource "aws_cloudwatch_event_target" "evaluator" {
  rule      = aws_cloudwatch_event_rule.schedule.name
  target_id = "ssd-auto-increase-evaluator"
  arn       = aws_lambda_function.evaluator.arn
}

resource "aws_lambda_permission" "schedule" {
  statement_id  = "AllowEventBridgeSchedule"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.evaluator.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.schedule.arn
}

# ----------------------------------------------------------------
# Trigger SNS topic (alarm -> Lambda, the only Lambda subscription)
# ----------------------------------------------------------------
# The function never publishes here; alarm_actions on the trigger alarms point
# at this topic.
resource "aws_sns_topic" "trigger" {
  name         = "${var.name_prefix}-trigger"
  display_name = "FSx for ONTAP SSD auto-increase trigger (${var.file_system_id})"
  tags         = var.tags
}

resource "aws_sns_topic_subscription" "trigger_lambda" {
  topic_arn = aws_sns_topic.trigger.arn
  protocol  = "lambda"
  endpoint  = aws_lambda_function.evaluator.arn
}

resource "aws_lambda_permission" "trigger" {
  statement_id  = "AllowTriggerTopic"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.evaluator.function_name
  principal     = "sns.amazonaws.com"
  source_arn    = aws_sns_topic.trigger.arn
}

# ----------------------------------------------------------------
# Notification SNS topic (reports and approve emails; no Lambda subscription)
# ----------------------------------------------------------------
# Separate topic so a report cannot re-invoke the function.
resource "aws_sns_topic" "notify" {
  name         = "${var.name_prefix}-notify"
  display_name = "FSx for ONTAP SSD auto-increase reports (${var.file_system_id})"
  tags         = var.tags
}

resource "aws_sns_topic_subscription" "notify_email" {
  count = local.notify_subscription ? 1 : 0

  topic_arn = aws_sns_topic.notify.arn
  protocol  = "email"
  endpoint  = var.notification_email
}

# ----------------------------------------------------------------
# Trigger CloudWatch alarms on SSD utilization
# ----------------------------------------------------------------
# Uses the StorageCapacityUtilization series (StorageTier=SSD, DataType=All)
# this repository verified fires, not the AWS sample's StorageUsed/StorageCapacity
# metric math. One file-system-level alarm, plus one per Aggregate on second
# generation.
resource "aws_cloudwatch_metric_alarm" "trigger" {
  alarm_name          = local.trigger_alarm_names["file-system"]
  alarm_description   = "SSD utilization of ${var.file_system_id} exceeded ${var.trigger_threshold_percent}%. The ${local.function_name} evaluator decides whether to increase SSD capacity, within the configured ceiling and guards."
  namespace           = "AWS/FSx"
  metric_name         = "StorageCapacityUtilization"
  dimensions          = { FileSystemId = var.file_system_id, StorageTier = "SSD", DataType = "All" }
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 1
  threshold           = var.trigger_threshold_percent
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.trigger.arn]
  tags                = var.tags
}

resource "aws_cloudwatch_metric_alarm" "trigger_aggregate" {
  for_each = toset(var.aggregate_names)

  alarm_name          = local.trigger_alarm_names["aggregate/${each.value}"]
  alarm_description   = "SSD utilization of aggregate ${each.value} on ${var.file_system_id} exceeded ${var.trigger_threshold_percent}%. The ${local.function_name} evaluator decides whether to increase SSD capacity, within the configured ceiling and guards."
  namespace           = "AWS/FSx"
  metric_name         = "StorageCapacityUtilization"
  dimensions          = { FileSystemId = var.file_system_id, StorageTier = "SSD", DataType = "All", Aggregate = each.value }
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 1
  threshold           = var.trigger_threshold_percent
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.trigger.arn]
  tags                = var.tags
}

# ----------------------------------------------------------------
# Non-blocking headroom check
# ----------------------------------------------------------------
check "ceiling_leaves_room" {
  assert {
    condition     = var.max_storage_capacity_gib >= ceil(data.aws_fsx_ontap_file_system.target.storage_capacity * 1.1)
    error_message = "The ceiling is below current capacity plus the 10% minimum increase; T4 can never act."
  }
}
