# Offline: mock aws provider, plan only, no AWS credentials. The archive
# provider is real, so the Lambda zip is built from shared/lambda/ontap_metrics.
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
  mock_resource "aws_sns_topic" {
    defaults = {
      arn = "arn:aws:sns:us-east-1:123456789012:mock-alarms"
    }
  }
  mock_resource "aws_sqs_queue" {
    defaults = {
      arn = "arn:aws:sqs:us-east-1:123456789012:fsxn-ontap-metrics-dlq"
      url = "https://sqs.us-east-1.amazonaws.com/123456789012/fsxn-ontap-metrics-dlq"
    }
  }
  mock_resource "aws_iam_role" {
    defaults = {
      arn = "arn:aws:iam::123456789012:role/fsxn-ontap-metrics-role"
    }
  }
  mock_resource "aws_lambda_function" {
    defaults = {
      arn = "arn:aws:lambda:us-east-1:123456789012:function:fsxn-ontap-metrics-poller"
    }
  }
  mock_resource "aws_cloudwatch_log_group" {
    defaults = {
      arn = "arn:aws:logs:us-east-1:123456789012:log-group:/aws/lambda/fsxn-ontap-metrics-poller"
    }
  }
  mock_resource "aws_cloudwatch_event_rule" {
    defaults = {
      arn = "arn:aws:events:us-east-1:123456789012:rule/fsxn-ontap-metrics-schedule"
    }
  }
  mock_resource "aws_security_group" {
    defaults = {
      id = "sg-0123456789abcdef0"
    }
  }
}

variables {
  file_system_id               = "fs-0123456789abcdef0"
  ontap_management_ip          = "198.51.100.10"
  ontap_credentials_secret_arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:ontap-monitor-XXXXXX"
  vpc_id                       = "vpc-0123456789abcdef0"
  subnet_ids                   = ["subnet-0123456789abcdef0"]
  qtree_svm_name               = "svm-prod-01"
}

run "defaults" {
  command = plan

  # --- Lambda -----------------------------------------------------------
  assert {
    condition = (
      aws_lambda_function.poller.function_name == "fsxn-ontap-metrics-poller" &&
      aws_lambda_function.poller.runtime == "python3.12" &&
      aws_lambda_function.poller.handler == "ontap_metrics_handler.lambda_handler" &&
      aws_lambda_function.poller.timeout == 300 &&
      aws_lambda_function.poller.memory_size == 256 &&
      length(aws_lambda_function.poller.layers) == 0
    )
    error_message = "Lambda runtime, handler, timeout, memory or layers differ from the plan."
  }
  assert {
    condition = (
      aws_lambda_function.poller.environment[0].variables["ONTAP_CREDENTIALS_SECRET_ARN"] == "arn:aws:secretsmanager:us-east-1:123456789012:secret:ontap-monitor-XXXXXX" &&
      aws_lambda_function.poller.environment[0].variables["ONTAP_MGMT_IP"] == "198.51.100.10" &&
      aws_lambda_function.poller.environment[0].variables["FILE_SYSTEM_ID"] == "fs-0123456789abcdef0" &&
      aws_lambda_function.poller.environment[0].variables["COLLECTORS"] == "qtree,snapmirror" &&
      aws_lambda_function.poller.environment[0].variables["SVM_NAME"] == "svm-prod-01" &&
      aws_lambda_function.poller.environment[0].variables["SNAPMIRROR_MAX_RELATIONSHIPS"] == "100" &&
      aws_lambda_function.poller.environment[0].variables["CREDENTIALS_CACHE_TTL_SECONDS"] == "300" &&
      aws_lambda_function.poller.environment[0].variables["CA_CERT_PATH"] == ""
    )
    error_message = "Lambda environment does not match the handler contract."
  }
  assert {
    condition     = length([for k in keys(aws_lambda_function.poller.environment[0].variables) : k if can(regex("(?i)pass|secret_value", k))]) == 0
    error_message = "The Lambda environment must carry the secret ARN only, never a password."
  }
  assert {
    condition = (
      aws_lambda_function.poller.dead_letter_config[0].target_arn == "arn:aws:sqs:us-east-1:123456789012:fsxn-ontap-metrics-dlq" &&
      aws_sqs_queue.dlq.kms_master_key_id == "alias/aws/sqs" &&
      aws_sqs_queue.dlq.message_retention_seconds == 1209600
    )
    error_message = "DLQ must be wired to the function and encrypted with alias/aws/sqs."
  }
  assert {
    condition = (
      aws_lambda_function.poller.vpc_config[0].security_group_ids == toset(["sg-0123456789abcdef0"]) &&
      aws_lambda_function.poller.vpc_config[0].subnet_ids == toset(["subnet-0123456789abcdef0"])
    )
    error_message = "Lambda must run in the given subnets with the module-created security group."
  }
  assert {
    condition     = aws_lambda_function.poller.reserved_concurrent_executions == 1
    error_message = "Only one poller may run at a time, so overlapping runs cannot send parallel ONTAP logins."
  }
  assert {
    condition     = aws_lambda_function.poller.source_code_hash == data.archive_file.lambda.output_base64sha256 && data.archive_file.lambda.output_size > 0
    error_message = "Lambda package must come from the archive built of shared/lambda/ontap_metrics."
  }
  assert {
    condition     = aws_cloudwatch_log_group.poller.name == "/aws/lambda/fsxn-ontap-metrics-poller" && aws_cloudwatch_log_group.poller.retention_in_days == 30
    error_message = "Log group name must match the function's default log group, with 30-day retention."
  }

  # --- Schedule ---------------------------------------------------------
  assert {
    condition = (
      aws_cloudwatch_event_rule.schedule.schedule_expression == "rate(5 minutes)" &&
      aws_cloudwatch_event_target.poller.arn == "arn:aws:lambda:us-east-1:123456789012:function:fsxn-ontap-metrics-poller" &&
      aws_lambda_permission.schedule.principal == "events.amazonaws.com" &&
      aws_lambda_permission.schedule.source_arn == "arn:aws:events:us-east-1:123456789012:rule/fsxn-ontap-metrics-schedule"
    )
    error_message = "Schedule rule, target or permission is not wired as expected."
  }

  # --- Network ----------------------------------------------------------
  assert {
    condition = (
      aws_vpc_security_group_egress_rule.ontap_management.cidr_ipv4 == "198.51.100.10/32" &&
      aws_vpc_security_group_egress_rule.ontap_management.from_port == 443 &&
      aws_vpc_security_group_egress_rule.ontap_management.to_port == 443 &&
      aws_vpc_security_group_egress_rule.ontap_management.ip_protocol == "tcp"
    )
    error_message = "Lambda SG must allow TCP 443 to the management endpoint /32."
  }
  assert {
    condition     = keys(aws_vpc_security_group_egress_rule.aws_api) == ["0.0.0.0/0"] && aws_vpc_security_group_egress_rule.aws_api["0.0.0.0/0"].from_port == 443
    error_message = "Default AWS API egress must be TCP 443 to 0.0.0.0/0 (NAT gateway path)."
  }
  assert {
    condition = (
      length(aws_vpc_endpoint.this) == 0 &&
      length(aws_security_group.endpoints) == 0 &&
      length(aws_vpc_security_group_egress_rule.lambda_to_endpoints) == 0 &&
      length(output.vpc_endpoint_ids) == 0
    )
    error_message = "No interface endpoints by default."
  }
  assert {
    condition     = length(aws_sns_topic.alarm) == 0 && length(aws_sns_topic_subscription.email) == 0 && output.sns_topic_arn == null
    error_message = "No SNS topic without notification_email."
  }

  # --- IAM --------------------------------------------------------------
  assert {
    condition     = aws_iam_role_policy_attachment.vpc_access.policy_arn == "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
    error_message = "The role must carry AWSLambdaVPCAccessExecutionRole, as in the template."
  }
  assert {
    condition = [for s in jsondecode(aws_iam_role_policy.lambda.policy).Statement : s.Sid] == [
      "SecretsRead", "CloudWatchPublish", "Logs", "DeadLetterQueue",
    ]
    error_message = "Execution-role statements differ from the README table (no SecretKms without a KMS key)."
  }
  assert {
    condition = alltrue([
      for s in jsondecode(aws_iam_role_policy.lambda.policy).Statement :
      s.Resource == "arn:aws:secretsmanager:us-east-1:123456789012:secret:ontap-monitor-XXXXXX" if s.Sid == "SecretsRead"
    ])
    error_message = "GetSecretValue must be scoped to the input secret ARN."
  }
  assert {
    condition = alltrue([
      for s in jsondecode(aws_iam_role_policy.lambda.policy).Statement :
      s.Action == "cloudwatch:PutMetricData" && s.Condition.StringEquals["cloudwatch:namespace"] == ["FSxONTAP/Qtree", "FSxONTAP/SnapMirror"]
      if s.Sid == "CloudWatchPublish"
    ])
    error_message = "PutMetricData must be limited to both enabled namespaces."
  }
  assert {
    condition = alltrue([
      for s in jsondecode(aws_iam_role_policy.lambda.policy).Statement : (
        s.Sid == "Logs" ? s.Resource == "arn:aws:logs:us-east-1:123456789012:log-group:/aws/lambda/fsxn-ontap-metrics-poller:*" :
        s.Sid == "DeadLetterQueue" ? s.Resource == "arn:aws:sqs:us-east-1:123456789012:fsxn-ontap-metrics-dlq" : true
      )
    ])
    error_message = "Logs and DLQ statements must be scoped to the module-created log group and queue."
  }
  assert {
    condition     = !strcontains(aws_iam_role_policy.lambda.policy, "kms:Decrypt")
    error_message = "No kms:Decrypt without ontap_credentials_kms_key_arn."
  }

  # --- Alarms -----------------------------------------------------------
  assert {
    condition = (
      aws_cloudwatch_metric_alarm.qtree_quota[0].alarm_name == "fsxn-ontap-metrics-qtree-quota-high" &&
      aws_cloudwatch_metric_alarm.qtree_quota[0].namespace == "FSxONTAP/Qtree" &&
      aws_cloudwatch_metric_alarm.qtree_quota[0].metric_name == "QtreeQuotaUsedPercentMax" &&
      aws_cloudwatch_metric_alarm.qtree_quota[0].dimensions == tomap({ SvmName = "svm-prod-01" }) &&
      aws_cloudwatch_metric_alarm.qtree_quota[0].statistic == "Maximum" &&
      aws_cloudwatch_metric_alarm.qtree_quota[0].period == 300 &&
      aws_cloudwatch_metric_alarm.qtree_quota[0].evaluation_periods == 2 &&
      aws_cloudwatch_metric_alarm.qtree_quota[0].threshold == 85 &&
      aws_cloudwatch_metric_alarm.qtree_quota[0].comparison_operator == "GreaterThanThreshold" &&
      aws_cloudwatch_metric_alarm.qtree_quota[0].treat_missing_data == "missing"
    )
    error_message = "qtree_quota alarm does not match the template's QtreeQuotaAlarm."
  }
  assert {
    condition = (
      aws_cloudwatch_metric_alarm.snapmirror_unhealthy[0].alarm_name == "fsxn-ontap-metrics-snapmirror-unhealthy" &&
      aws_cloudwatch_metric_alarm.snapmirror_unhealthy[0].namespace == "FSxONTAP/SnapMirror" &&
      aws_cloudwatch_metric_alarm.snapmirror_unhealthy[0].metric_name == "SnapMirrorUnhealthyCount" &&
      aws_cloudwatch_metric_alarm.snapmirror_unhealthy[0].dimensions == tomap({ FileSystemId = "fs-0123456789abcdef0" }) &&
      aws_cloudwatch_metric_alarm.snapmirror_unhealthy[0].statistic == "Maximum" &&
      aws_cloudwatch_metric_alarm.snapmirror_unhealthy[0].period == 300 &&
      aws_cloudwatch_metric_alarm.snapmirror_unhealthy[0].evaluation_periods == 2 &&
      aws_cloudwatch_metric_alarm.snapmirror_unhealthy[0].threshold == 0 &&
      aws_cloudwatch_metric_alarm.snapmirror_unhealthy[0].comparison_operator == "GreaterThanThreshold" &&
      aws_cloudwatch_metric_alarm.snapmirror_unhealthy[0].treat_missing_data == "missing"
    )
    error_message = "snapmirror_unhealthy alarm does not match the alert design."
  }
  assert {
    condition = (
      aws_cloudwatch_metric_alarm.snapmirror_lag[0].alarm_name == "fsxn-ontap-metrics-snapmirror-lag-high" &&
      aws_cloudwatch_metric_alarm.snapmirror_lag[0].namespace == "FSxONTAP/SnapMirror" &&
      aws_cloudwatch_metric_alarm.snapmirror_lag[0].metric_name == "SnapMirrorLagSecondsMax" &&
      aws_cloudwatch_metric_alarm.snapmirror_lag[0].dimensions == tomap({ FileSystemId = "fs-0123456789abcdef0" }) &&
      aws_cloudwatch_metric_alarm.snapmirror_lag[0].statistic == "Maximum" &&
      aws_cloudwatch_metric_alarm.snapmirror_lag[0].period == 300 &&
      aws_cloudwatch_metric_alarm.snapmirror_lag[0].evaluation_periods == 1 &&
      aws_cloudwatch_metric_alarm.snapmirror_lag[0].threshold == 10800 &&
      aws_cloudwatch_metric_alarm.snapmirror_lag[0].comparison_operator == "GreaterThanThreshold" &&
      aws_cloudwatch_metric_alarm.snapmirror_lag[0].treat_missing_data == "missing"
    )
    error_message = "snapmirror_lag alarm does not match the alert design."
  }
  assert {
    condition = keys(aws_cloudwatch_metric_alarm.heartbeat) == ["qtree", "snapmirror"] && alltrue([
      for k, a in aws_cloudwatch_metric_alarm.heartbeat : (
        a.alarm_name == "fsxn-ontap-metrics-${k}-heartbeat" &&
        a.namespace == (k == "qtree" ? "FSxONTAP/Qtree" : "FSxONTAP/SnapMirror") &&
        a.metric_name == "CollectorSucceeded" &&
        a.dimensions == tomap({ FileSystemId = "fs-0123456789abcdef0", Collector = k }) &&
        a.statistic == "Minimum" &&
        a.period == 300 &&
        a.evaluation_periods == 2 &&
        a.threshold == 1 &&
        a.comparison_operator == "LessThanThreshold" &&
        a.treat_missing_data == "breaching"
      )
    ])
    error_message = "Heartbeat alarms must exist per collector in the collector's namespace, breaching on missing data."
  }
  assert {
    condition = (
      aws_cloudwatch_metric_alarm.dlq_depth.namespace == "AWS/SQS" &&
      aws_cloudwatch_metric_alarm.dlq_depth.metric_name == "ApproximateNumberOfMessagesVisible" &&
      aws_cloudwatch_metric_alarm.dlq_depth.dimensions == tomap({ QueueName = "fsxn-ontap-metrics-dlq" }) &&
      aws_cloudwatch_metric_alarm.dlq_depth.statistic == "Maximum" &&
      aws_cloudwatch_metric_alarm.dlq_depth.period == 300 &&
      aws_cloudwatch_metric_alarm.dlq_depth.evaluation_periods == 1 &&
      aws_cloudwatch_metric_alarm.dlq_depth.threshold == 0 &&
      aws_cloudwatch_metric_alarm.dlq_depth.treat_missing_data == "notBreaching"
    )
    error_message = "dlq_depth alarm does not match the template's DLQ alarm."
  }
  assert {
    condition = (
      aws_cloudwatch_metric_alarm.lambda_errors.namespace == "AWS/Lambda" &&
      aws_cloudwatch_metric_alarm.lambda_errors.metric_name == "Errors" &&
      aws_cloudwatch_metric_alarm.lambda_errors.dimensions == tomap({ FunctionName = "fsxn-ontap-metrics-poller" }) &&
      aws_cloudwatch_metric_alarm.lambda_errors.statistic == "Sum" &&
      aws_cloudwatch_metric_alarm.lambda_errors.period == 300 &&
      aws_cloudwatch_metric_alarm.lambda_errors.evaluation_periods == 1 &&
      aws_cloudwatch_metric_alarm.lambda_errors.threshold == 0 &&
      aws_cloudwatch_metric_alarm.lambda_errors.comparison_operator == "GreaterThanThreshold" &&
      aws_cloudwatch_metric_alarm.lambda_errors.treat_missing_data == "notBreaching"
    )
    error_message = "lambda_errors alarm does not match the alert design."
  }
  assert {
    condition     = length(aws_cloudwatch_metric_alarm.qtree_quota[0].alarm_actions) == 0 && length(aws_cloudwatch_metric_alarm.heartbeat["qtree"].ok_actions) == 0
    error_message = "No alarm or OK actions without notification_email."
  }

  # --- Contract: every custom-metric alarm reads a series the Lambda publishes.
  # The Python tests assert that the collectors emit exactly the series in
  # metric_contract.json; this assertion ties each alarm to that same file,
  # comparing namespace, metric name and the set of dimension names.
  assert {
    condition = length(concat(
      aws_cloudwatch_metric_alarm.qtree_quota, aws_cloudwatch_metric_alarm.snapmirror_unhealthy,
      aws_cloudwatch_metric_alarm.snapmirror_lag, values(aws_cloudwatch_metric_alarm.heartbeat),
    )) == 5
    error_message = "Expected 5 custom-metric alarms with both collectors on; the contract check below would otherwise be vacuous."
  }
  assert {
    condition = alltrue([
      for a in concat(
        aws_cloudwatch_metric_alarm.qtree_quota, aws_cloudwatch_metric_alarm.snapmirror_unhealthy,
        aws_cloudwatch_metric_alarm.snapmirror_lag, values(aws_cloudwatch_metric_alarm.heartbeat),
      ) :
      contains(
        [
          for e in jsondecode(file("../../shared/lambda/ontap_metrics/tests/fixtures/metric_contract.json")) :
          "${e.namespace}|${e.metric}|${join(",", sort(e.dimensions))}" if e.alarm
        ],
        "${a.namespace}|${a.metric_name}|${join(",", sort(keys(a.dimensions)))}"
      )
    ])
    error_message = "A custom-metric alarm reads a (namespace, metric, dimension names) tuple that metric_contract.json does not mark as alarm-read, so it would match no published series."
  }

  # --- Outputs ----------------------------------------------------------
  assert {
    condition = (
      output.metric_namespaces == ["FSxONTAP/Qtree", "FSxONTAP/SnapMirror"] &&
      toset(keys(output.alarm_arns)) == toset([
        "qtree_quota", "snapmirror_unhealthy", "snapmirror_lag",
        "heartbeat/qtree", "heartbeat/snapmirror", "dlq_depth", "lambda_errors",
      ])
    )
    error_message = "Outputs do not list the enabled namespaces and all seven alarms."
  }
}
