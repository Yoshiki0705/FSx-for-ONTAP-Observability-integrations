# Offline: mock aws provider, plan only, no AWS credentials. The archive
# provider is real, so the Lambda zip is built from shared/lambda/ssd_auto_increase.
mock_provider "aws" {
  override_during = plan

  mock_data "aws_region" {
    defaults = {
      region = "us-east-1"
    }
  }
  mock_data "aws_partition" {
    defaults = {
      partition = "aws"
    }
  }
  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "123456789012"
    }
  }
  # First-generation shape, below the ceiling, so the preconditions and the
  # check pass.
  mock_data "aws_fsx_ontap_file_system" {
    defaults = {
      deployment_type  = "SINGLE_AZ_1"
      ha_pairs         = 1
      storage_capacity = 1024
    }
  }
  mock_resource "aws_sns_topic" {
    defaults = {
      arn = "arn:aws:sns:us-east-1:123456789012:fsxn-ssd-auto-increase-topic"
    }
  }
  mock_resource "aws_sqs_queue" {
    defaults = {
      arn = "arn:aws:sqs:us-east-1:123456789012:fsxn-ssd-auto-increase-dlq"
      url = "https://sqs.us-east-1.amazonaws.com/123456789012/fsxn-ssd-auto-increase-dlq"
    }
  }
  mock_resource "aws_dynamodb_table" {
    defaults = {
      arn = "arn:aws:dynamodb:us-east-1:123456789012:table/fsxn-ssd-auto-increase-lock"
    }
  }
  mock_resource "aws_iam_role" {
    defaults = {
      arn = "arn:aws:iam::123456789012:role/fsxn-ssd-auto-increase-role"
    }
  }
  mock_resource "aws_lambda_function" {
    defaults = {
      arn = "arn:aws:lambda:us-east-1:123456789012:function:fsxn-ssd-auto-increase-evaluator"
    }
  }
  mock_resource "aws_cloudwatch_log_group" {
    defaults = {
      arn = "arn:aws:logs:us-east-1:123456789012:log-group:/fsx/ssd-auto-increase/fs-0123456789abcdef0"
    }
  }
  mock_resource "aws_cloudwatch_event_rule" {
    defaults = {
      arn = "arn:aws:events:us-east-1:123456789012:rule/fsxn-ssd-auto-increase-schedule"
    }
  }
}

variables {
  file_system_id           = "fs-0123456789abcdef0"
  max_storage_capacity_gib = 4096
  decision_archive_bucket  = "ssd-auto-increase-audit"
}

run "defaults" {
  command = plan

  # Give the two SNS topics distinct ARNs so the wiring can be told apart: the
  # alarm must point at the trigger topic, while NOTIFY_TOPIC_ARN and the
  # PublishReports IAM statement must point at the notification topic.
  override_resource {
    target          = aws_sns_topic.trigger
    override_during = plan
    values = {
      arn = "arn:aws:sns:us-east-1:123456789012:fsxn-ssd-auto-increase-trigger"
    }
  }
  override_resource {
    target          = aws_sns_topic.notify
    override_during = plan
    values = {
      arn = "arn:aws:sns:us-east-1:123456789012:fsxn-ssd-auto-increase-notify"
    }
  }

  # --- Lambda -----------------------------------------------------------
  assert {
    condition = (
      aws_lambda_function.evaluator.function_name == "fsxn-ssd-auto-increase-evaluator" &&
      aws_lambda_function.evaluator.runtime == "python3.12" &&
      aws_lambda_function.evaluator.handler == "ssd_auto_increase_handler.lambda_handler" &&
      aws_lambda_function.evaluator.timeout == 300 &&
      aws_lambda_function.evaluator.memory_size == 256
    )
    error_message = "Lambda runtime, handler, timeout or memory differ from the plan."
  }
  assert {
    condition     = length(aws_lambda_function.evaluator.vpc_config) == 0
    error_message = "The evaluator must run outside any VPC (AWS APIs only)."
  }
  assert {
    condition     = aws_lambda_function.evaluator.reserved_concurrent_executions == 1
    error_message = "Only one evaluator may run at a time (single-flight)."
  }
  assert {
    condition     = aws_lambda_function.evaluator.source_code_hash == data.archive_file.lambda.output_base64sha256 && data.archive_file.lambda.output_size > 0
    error_message = "Lambda package must come from the archive built of shared/lambda/ssd_auto_increase."
  }
  assert {
    condition = (
      aws_lambda_function.evaluator.dead_letter_config[0].target_arn == "arn:aws:sqs:us-east-1:123456789012:fsxn-ssd-auto-increase-dlq" &&
      aws_sqs_queue.dlq.kms_master_key_id == "alias/aws/sqs" &&
      aws_sqs_queue.dlq.message_retention_seconds == 1209600
    )
    error_message = "DLQ must be wired to the function and encrypted with alias/aws/sqs."
  }
  assert {
    condition = (
      aws_lambda_function.evaluator.environment[0].variables["FILE_SYSTEM_ID"] == "fs-0123456789abcdef0" &&
      aws_lambda_function.evaluator.environment[0].variables["MODE"] == "notify_only" &&
      aws_lambda_function.evaluator.environment[0].variables["MAX_STORAGE_CAPACITY_GIB"] == "4096" &&
      aws_lambda_function.evaluator.environment[0].variables["INCREASE_PERCENT"] == "10" &&
      aws_lambda_function.evaluator.environment[0].variables["DECISION_ARCHIVE_REQUIRED_MODE"] == "COMPLIANCE" &&
      aws_lambda_function.evaluator.environment[0].variables["INDETERMINATE_RECONCILE_HOURS"] == "6"
    )
    error_message = "Lambda environment does not match the handler contract."
  }
  # NOTIFY_TOPIC_ARN must be the notification topic, never the trigger topic:
  # the function publishes reports there, and the trigger topic is for the alarm.
  assert {
    condition = (
      aws_lambda_function.evaluator.environment[0].variables["NOTIFY_TOPIC_ARN"] == "arn:aws:sns:us-east-1:123456789012:fsxn-ssd-auto-increase-notify" &&
      aws_lambda_function.evaluator.environment[0].variables["NOTIFY_TOPIC_ARN"] != "arn:aws:sns:us-east-1:123456789012:fsxn-ssd-auto-increase-trigger"
    )
    error_message = "NOTIFY_TOPIC_ARN must be the notification topic, not the trigger topic."
  }
  assert {
    condition     = length([for k in keys(aws_lambda_function.evaluator.environment[0].variables) : k if can(regex("(?i)pass|secret", k))]) == 0
    error_message = "The Lambda environment must carry no secret or password."
  }

  # --- Schedule ---------------------------------------------------------
  assert {
    condition = (
      aws_cloudwatch_event_rule.schedule.schedule_expression == "rate(1 hour)" &&
      aws_cloudwatch_event_target.evaluator.arn == "arn:aws:lambda:us-east-1:123456789012:function:fsxn-ssd-auto-increase-evaluator" &&
      aws_lambda_permission.schedule.principal == "events.amazonaws.com"
    )
    error_message = "Schedule rule, target or permission is not wired as expected."
  }

  # --- SNS topics -------------------------------------------------------
  # Exactly two topics, with distinct ARNs. The lambda subscription is on the
  # trigger topic and points at the evaluator; the trigger subscription's topic
  # is the trigger topic, not the notification topic.
  assert {
    condition = (
      aws_sns_topic.trigger.arn == "arn:aws:sns:us-east-1:123456789012:fsxn-ssd-auto-increase-trigger" &&
      aws_sns_topic.notify.arn == "arn:aws:sns:us-east-1:123456789012:fsxn-ssd-auto-increase-notify" &&
      aws_sns_topic.trigger.arn != aws_sns_topic.notify.arn
    )
    error_message = "The module must create two distinct SNS topics (trigger and notify)."
  }
  assert {
    condition = (
      aws_sns_topic_subscription.trigger_lambda.protocol == "lambda" &&
      aws_sns_topic_subscription.trigger_lambda.topic_arn == aws_sns_topic.trigger.arn &&
      aws_sns_topic_subscription.trigger_lambda.endpoint == "arn:aws:lambda:us-east-1:123456789012:function:fsxn-ssd-auto-increase-evaluator" &&
      aws_lambda_permission.trigger.principal == "sns.amazonaws.com" &&
      aws_lambda_permission.trigger.source_arn == aws_sns_topic.trigger.arn
    )
    error_message = "The lambda subscription and invoke permission must be on the trigger topic only."
  }
  assert {
    condition     = length(aws_sns_topic_subscription.notify_email) == 0
    error_message = "No email subscription on the notification topic without notification_email; and never a lambda subscription on it."
  }

  # --- Trigger alarm ----------------------------------------------------
  assert {
    condition = (
      aws_cloudwatch_metric_alarm.trigger.namespace == "AWS/FSx" &&
      aws_cloudwatch_metric_alarm.trigger.metric_name == "StorageCapacityUtilization" &&
      aws_cloudwatch_metric_alarm.trigger.dimensions == tomap({ FileSystemId = "fs-0123456789abcdef0", StorageTier = "SSD", DataType = "All" }) &&
      aws_cloudwatch_metric_alarm.trigger.statistic == "Average" &&
      aws_cloudwatch_metric_alarm.trigger.period == 300 &&
      aws_cloudwatch_metric_alarm.trigger.threshold == 80 &&
      aws_cloudwatch_metric_alarm.trigger.comparison_operator == "GreaterThanThreshold"
    )
    error_message = "Trigger alarm must read AWS/FSx StorageCapacityUtilization with FileSystemId+StorageTier=SSD+DataType=All."
  }
  assert {
    condition = (
      aws_cloudwatch_metric_alarm.trigger.alarm_actions == toset([aws_sns_topic.trigger.arn]) &&
      !contains(aws_cloudwatch_metric_alarm.trigger.alarm_actions, aws_sns_topic.notify.arn)
    )
    error_message = "The trigger alarm must publish to the trigger topic, never the notification topic."
  }
  assert {
    condition     = length(aws_cloudwatch_metric_alarm.trigger_aggregate) == 0
    error_message = "No per-aggregate alarms by default (aggregate_names empty)."
  }

  # --- DynamoDB lock ----------------------------------------------------
  # expires_at is the conditional lease value, not a DynamoDB TTL attribute:
  # TTL must stay disabled so durable states (submitted, optimizing,
  # indeterminate, manual_disposition_required, blocked), which carry an
  # expires_at only for a lease-based takeover, cannot be deleted by the
  # service before their designed release.
  assert {
    condition = (
      aws_dynamodb_table.lock.billing_mode == "PAY_PER_REQUEST" &&
      aws_dynamodb_table.lock.hash_key == "file_system_id" &&
      length(aws_dynamodb_table.lock.ttl) == 0
    )
    error_message = "Lock table must be PAY_PER_REQUEST, keyed on file_system_id, with no TTL (expires_at is the lease, not a deletion trigger)."
  }

  # --- Decision log group -----------------------------------------------
  assert {
    condition     = aws_cloudwatch_log_group.decision.retention_in_days == 365
    error_message = "Decision log group retention must default to 365 days."
  }

  # --- Function (default) log group -------------------------------------
  # The function's standard logger output needs a module-created destination:
  # the execution role has CreateLogStream/PutLogEvents on /aws/lambda/<fn> but
  # not CreateLogGroup, so the group must exist before the first invocation.
  assert {
    condition = (
      aws_cloudwatch_log_group.function.name == "/aws/lambda/fsxn-ssd-auto-increase-evaluator" &&
      aws_cloudwatch_log_group.function.retention_in_days == 365
    )
    error_message = "The module must create /aws/lambda/<function-name> with the configured retention."
  }
  # The execution role's Logs statement must cover both the decision group and
  # the function default group, so standard logs can be written on first deploy.
  assert {
    condition = anytrue([
      for s in jsondecode(aws_iam_role_policy.lambda.policy).Statement :
      contains(s.Resource, "arn:aws:logs:us-east-1:123456789012:log-group:/aws/lambda/fsxn-ssd-auto-increase-evaluator:*")
      if s.Sid == "Logs"
    ])
    error_message = "The execution role must allow log writes to the function default log group."
  }

  # --- IAM --------------------------------------------------------------
  assert {
    condition = [for s in jsondecode(aws_iam_role_policy.lambda.policy).Statement : s.Sid] == [
      "UpdateFileSystem", "DescribeFileSystems", "DescribeAlarms", "GetMetricData",
      "PublishReports", "LockTable", "ArchiveWrite", "ArchiveLockConfig",
      "DeadLetterQueue", "Logs",
    ]
    error_message = "Execution-role statements differ from the README table."
  }
  # The report values come from cloudwatch:GetMetricData (design "IAM
  # permissions"). A classic AWS/FSx metric has no ARN, so the statement holds
  # exactly that one action on Resource "*" and nothing else.
  assert {
    condition = alltrue([
      for s in jsondecode(aws_iam_role_policy.lambda.policy).Statement :
      s.Resource == "*" && s.Action == "cloudwatch:GetMetricData" if s.Sid == "GetMetricData"
    ])
    error_message = "cloudwatch:GetMetricData must be alone on Resource '*' in the GetMetricData statement."
  }
  assert {
    condition     = !contains(keys(aws_lambda_function.evaluator.environment[0].variables), "AGGREGATE_NAMES")
    error_message = "AGGREGATE_NAMES is set only when aggregate_names is not empty."
  }
  assert {
    condition = alltrue([
      for s in jsondecode(aws_iam_role_policy.lambda.policy).Statement :
      s.Resource == "arn:aws:fsx:us-east-1:123456789012:file-system/fs-0123456789abcdef0" if s.Sid == "UpdateFileSystem"
    ])
    error_message = "fsx:UpdateFileSystem must be scoped to the one file-system ARN."
  }
  assert {
    condition = alltrue([
      for s in jsondecode(aws_iam_role_policy.lambda.policy).Statement :
      s.Resource == "*" if s.Sid == "DescribeFileSystems"
    ])
    error_message = "fsx:DescribeFileSystems has no resource type and must use Resource '*'."
  }
  # sns:Publish must be scoped to the notification topic, not the trigger topic:
  # the function reports to notify and never publishes to the trigger topic.
  assert {
    condition = alltrue([
      for s in jsondecode(aws_iam_role_policy.lambda.policy).Statement :
      s.Resource == aws_sns_topic.notify.arn && s.Resource != aws_sns_topic.trigger.arn
      if s.Sid == "PublishReports"
    ])
    error_message = "sns:Publish must be scoped to the notification topic ARN only."
  }
  assert {
    condition     = !strcontains(aws_iam_role_policy.lambda.policy, "s3:DeleteObject")
    error_message = "The function must not have s3:DeleteObject."
  }
  assert {
    condition     = !strcontains(aws_iam_role_policy.lambda.policy, "s3:PutObjectRetention")
    error_message = "The function must not have s3:PutObjectRetention."
  }
  assert {
    condition     = !strcontains(aws_iam_role_policy.lambda.policy, "s3:BypassGovernanceRetention")
    error_message = "The function must not have s3:BypassGovernanceRetention."
  }

  # --- Outputs ----------------------------------------------------------
  assert {
    condition = (
      output.lock_table_name == "fsxn-ssd-auto-increase-lock" &&
      keys(output.trigger_alarm_arns) == ["file-system"] &&
      output.config_fingerprint != ""
    )
    error_message = "Outputs do not expose the lock table, the single trigger alarm and the fingerprint."
  }
}

run "aggregate_alarms_added" {
  command = plan
  variables {
    aggregate_names = ["aggr1", "aggr2"]
  }
  assert {
    condition     = length(aws_cloudwatch_metric_alarm.trigger_aggregate) == 2
    error_message = "Each aggregate name must add one trigger alarm."
  }
  assert {
    condition = alltrue([
      for a in values(aws_cloudwatch_metric_alarm.trigger_aggregate) :
      a.dimensions["Aggregate"] != null && a.dimensions["StorageTier"] == "SSD"
    ])
    error_message = "Per-aggregate alarms must carry the Aggregate dimension and StorageTier=SSD."
  }
  assert {
    condition     = toset(keys(output.trigger_alarm_arns)) == toset(["file-system", "aggregate/aggr1", "aggregate/aggr2"])
    error_message = "trigger_alarm_arns must enumerate the file-system and per-aggregate alarms."
  }
  # The function reads the per-aggregate utilization series of the same
  # aggregates the alarms watch.
  assert {
    condition     = aws_lambda_function.evaluator.environment[0].variables["AGGREGATE_NAMES"] == "aggr1,aggr2"
    error_message = "AGGREGATE_NAMES must list the aggregate_names the per-aggregate alarms use."
  }
}

run "email_subscription_created" {
  command = plan
  variables {
    notification_email = "ops@example.com"
  }
  # The module-wide mock gives every aws_sns_topic the same ARN, which would
  # make the inequality assertion below trivially false. Override the two topics
  # with distinct ARNs, available during plan (override_during = plan), so the
  # subscription's topic_arn is known and the assertions prove it binds to the
  # notification topic and never to the trigger topic.
  override_resource {
    target          = aws_sns_topic.notify
    override_during = plan
    values = {
      arn = "arn:aws:sns:us-east-1:123456789012:fsxn-ssd-auto-increase-notify"
    }
  }
  override_resource {
    target          = aws_sns_topic.trigger
    override_during = plan
    values = {
      arn = "arn:aws:sns:us-east-1:123456789012:fsxn-ssd-auto-increase-trigger"
    }
  }
  assert {
    condition     = length(aws_sns_topic_subscription.notify_email) == 1 && aws_sns_topic_subscription.notify_email[0].protocol == "email"
    error_message = "An email subscription on the notification topic is created when notification_email is set."
  }
  assert {
    condition     = aws_sns_topic_subscription.notify_email[0].topic_arn == aws_sns_topic.notify.arn
    error_message = "The email subscription must bind to the notification topic, not the trigger topic."
  }
  assert {
    condition     = aws_sns_topic_subscription.notify_email[0].topic_arn != aws_sns_topic.trigger.arn
    error_message = "The email subscription must never bind to the trigger topic (which invokes the function)."
  }
}
