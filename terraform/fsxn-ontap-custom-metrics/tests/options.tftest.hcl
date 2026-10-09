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

run "snapmirror_only" {
  command = plan

  variables {
    enable_qtree_collector = false
    qtree_svm_name         = null
  }

  assert {
    condition = (
      length(aws_cloudwatch_metric_alarm.qtree_quota) == 0 &&
      keys(aws_cloudwatch_metric_alarm.heartbeat) == ["snapmirror"] &&
      length(aws_cloudwatch_metric_alarm.snapmirror_unhealthy) == 1 &&
      length(aws_cloudwatch_metric_alarm.snapmirror_lag) == 1
    )
    error_message = "With qtree off there must be no qtree alarm and no qtree heartbeat."
  }
  assert {
    condition = (
      aws_lambda_function.poller.environment[0].variables["COLLECTORS"] == "snapmirror" &&
      !contains(keys(aws_lambda_function.poller.environment[0].variables), "SVM_NAME")
    )
    error_message = "With qtree off, COLLECTORS must be snapmirror and SVM_NAME absent."
  }
  assert {
    condition = alltrue([
      for s in jsondecode(aws_iam_role_policy.lambda.policy).Statement :
      s.Condition.StringEquals["cloudwatch:namespace"] == ["FSxONTAP/SnapMirror"] if s.Sid == "CloudWatchPublish"
    ])
    error_message = "PutMetricData must be limited to FSxONTAP/SnapMirror."
  }
  assert {
    condition     = output.metric_namespaces == ["FSxONTAP/SnapMirror"] && !contains(keys(output.alarm_arns), "qtree_quota")
    error_message = "Outputs must not list qtree with the qtree collector off."
  }
}

run "qtree_only" {
  command = plan

  variables {
    enable_snapmirror_collector = false
  }

  assert {
    condition = (
      length(aws_cloudwatch_metric_alarm.qtree_quota) == 1 &&
      keys(aws_cloudwatch_metric_alarm.heartbeat) == ["qtree"] &&
      length(aws_cloudwatch_metric_alarm.snapmirror_unhealthy) == 0 &&
      length(aws_cloudwatch_metric_alarm.snapmirror_lag) == 0
    )
    error_message = "With SnapMirror off there must be no SnapMirror alarms and no SnapMirror heartbeat."
  }
  assert {
    condition = (
      aws_lambda_function.poller.environment[0].variables["COLLECTORS"] == "qtree" &&
      aws_lambda_function.poller.environment[0].variables["SVM_NAME"] == "svm-prod-01"
    )
    error_message = "With SnapMirror off, COLLECTORS must be qtree and SVM_NAME set."
  }
  assert {
    condition = alltrue([
      for s in jsondecode(aws_iam_role_policy.lambda.policy).Statement :
      s.Condition.StringEquals["cloudwatch:namespace"] == ["FSxONTAP/Qtree"] if s.Sid == "CloudWatchPublish"
    ])
    error_message = "PutMetricData must be limited to FSxONTAP/Qtree."
  }
}

run "both_endpoints" {
  command = plan

  variables {
    create_monitoring_endpoint     = true
    create_secretsmanager_endpoint = true
    aws_api_egress_cidr_blocks     = []
  }

  override_resource {
    target          = aws_security_group.endpoints[0]
    override_during = plan
    values = {
      id = "sg-0fedcba9876543210"
    }
  }

  assert {
    condition = (
      toset(keys(aws_vpc_endpoint.this)) == toset(["monitoring", "secretsmanager"]) &&
      aws_vpc_endpoint.this["monitoring"].service_name == "com.amazonaws.us-east-1.monitoring" &&
      aws_vpc_endpoint.this["secretsmanager"].service_name == "com.amazonaws.us-east-1.secretsmanager" &&
      alltrue([for e in aws_vpc_endpoint.this : (
        e.vpc_endpoint_type == "Interface" &&
        e.private_dns_enabled == true &&
        e.subnet_ids == toset(["subnet-0123456789abcdef0"]) &&
        e.security_group_ids == toset(["sg-0fedcba9876543210"])
      )])
    )
    error_message = "Both interface endpoints must use private DNS, the Lambda subnets and the endpoint security group."
  }
  assert {
    condition = (
      aws_vpc_security_group_ingress_rule.endpoints_from_lambda[0].security_group_id == "sg-0fedcba9876543210" &&
      aws_vpc_security_group_ingress_rule.endpoints_from_lambda[0].referenced_security_group_id == "sg-0123456789abcdef0" &&
      aws_vpc_security_group_ingress_rule.endpoints_from_lambda[0].from_port == 443 &&
      aws_vpc_security_group_egress_rule.lambda_to_endpoints[0].security_group_id == "sg-0123456789abcdef0" &&
      aws_vpc_security_group_egress_rule.lambda_to_endpoints[0].referenced_security_group_id == "sg-0fedcba9876543210" &&
      aws_vpc_security_group_egress_rule.lambda_to_endpoints[0].to_port == 443
    )
    error_message = "Endpoint SG must allow 443 from the Lambda SG, and the Lambda SG must allow 443 to it."
  }
  assert {
    condition     = length(aws_vpc_security_group_egress_rule.aws_api) == 0 && aws_vpc_security_group_egress_rule.ontap_management.cidr_ipv4 == "198.51.100.10/32"
    error_message = "With aws_api_egress_cidr_blocks = [] only the management and endpoint egress rules remain."
  }
}

run "one_endpoint_only" {
  command = plan

  variables {
    create_secretsmanager_endpoint = true
  }

  assert {
    condition     = keys(aws_vpc_endpoint.this) == ["secretsmanager"] && length(aws_security_group.endpoints) == 1
    error_message = "Only the Secrets Manager endpoint must be created."
  }
}

run "notification" {
  command = plan

  variables {
    notification_email = "ops@example.com"
  }

  assert {
    condition = (
      length(aws_sns_topic.alarm) == 1 &&
      aws_sns_topic.alarm[0].name == "fsxn-ontap-metrics-alarms" &&
      aws_sns_topic_subscription.email[0].protocol == "email" &&
      aws_sns_topic_subscription.email[0].endpoint == "ops@example.com"
    )
    error_message = "Expected one topic with one email subscription."
  }
  assert {
    condition = alltrue([
      for a in concat(
        aws_cloudwatch_metric_alarm.qtree_quota, aws_cloudwatch_metric_alarm.snapmirror_unhealthy,
        aws_cloudwatch_metric_alarm.snapmirror_lag, values(aws_cloudwatch_metric_alarm.heartbeat),
        [aws_cloudwatch_metric_alarm.dlq_depth, aws_cloudwatch_metric_alarm.lambda_errors],
      ) :
      a.alarm_actions == toset(["arn:aws:sns:us-east-1:123456789012:mock-alarms"]) &&
      a.ok_actions == toset(["arn:aws:sns:us-east-1:123456789012:mock-alarms"])
    ])
    error_message = "Every alarm must notify the topic on ALARM and OK."
  }
}

run "poll_every_minute" {
  command = plan

  variables {
    poll_interval_minutes = 1
  }

  assert {
    condition     = aws_cloudwatch_event_rule.schedule.schedule_expression == "rate(1 minute)"
    error_message = "EventBridge requires the singular unit for a value of 1."
  }
  assert {
    # A run can take up to 300 seconds, longer than this interval; the
    # single-flight limit is what keeps two runs from overlapping.
    condition     = aws_lambda_function.poller.reserved_concurrent_executions == 1 && aws_lambda_function.poller.timeout > 60
    error_message = "The shortest interval must keep the single-flight concurrency limit."
  }
  assert {
    condition     = aws_cloudwatch_metric_alarm.snapmirror_lag[0].period == 300 && aws_cloudwatch_metric_alarm.heartbeat["qtree"].period == 300
    error_message = "Alarm period must not drop below 300 seconds."
  }
}

run "poll_every_ten_minutes" {
  command = plan

  variables {
    poll_interval_minutes = 10
  }

  assert {
    condition     = aws_cloudwatch_event_rule.schedule.schedule_expression == "rate(10 minutes)"
    error_message = "Schedule must use the plural unit above 1."
  }
  assert {
    condition = (
      aws_cloudwatch_metric_alarm.qtree_quota[0].period == 600 &&
      aws_cloudwatch_metric_alarm.snapmirror_unhealthy[0].period == 600 &&
      aws_cloudwatch_metric_alarm.snapmirror_lag[0].period == 600 &&
      alltrue([for a in aws_cloudwatch_metric_alarm.heartbeat : a.period == 600]) &&
      aws_cloudwatch_metric_alarm.lambda_errors.period == 600 &&
      aws_cloudwatch_metric_alarm.dlq_depth.period == 300
    )
    error_message = "Custom-metric and Lambda alarms follow the poll interval; the DLQ alarm stays at 300 seconds."
  }
}

run "ca_certificate_layer" {
  command = plan

  variables {
    ca_cert_path      = "/opt/certs/ontap-ca.pem"
    ca_cert_layer_arn = "arn:aws:lambda:us-east-1:123456789012:layer:ontap-ca:1"
  }

  assert {
    condition = (
      aws_lambda_function.poller.layers == tolist(["arn:aws:lambda:us-east-1:123456789012:layer:ontap-ca:1"]) &&
      aws_lambda_function.poller.environment[0].variables["CA_CERT_PATH"] == "/opt/certs/ontap-ca.pem"
    )
    error_message = "The CA layer must be attached and CA_CERT_PATH set."
  }
}

run "customer_managed_kms_key" {
  command = plan

  variables {
    ontap_credentials_kms_key_arn = "arn:aws:kms:us-east-1:123456789012:key/00000000-0000-0000-0000-000000000000"
  }

  assert {
    condition = [for s in jsondecode(aws_iam_role_policy.lambda.policy).Statement : s.Sid] == [
      "SecretsRead", "SecretKms", "CloudWatchPublish", "Logs", "DeadLetterQueue",
    ]
    error_message = "SecretKms must follow SecretsRead when a KMS key is given."
  }
  assert {
    condition = alltrue([
      for s in jsondecode(aws_iam_role_policy.lambda.policy).Statement : (
        s.Action == "kms:Decrypt" &&
        s.Resource == "arn:aws:kms:us-east-1:123456789012:key/00000000-0000-0000-0000-000000000000" &&
        s.Condition.StringEquals["kms:ViaService"] == "secretsmanager.us-east-1.amazonaws.com"
      ) if s.Sid == "SecretKms"
    ])
    error_message = "kms:Decrypt must be scoped to the key and to calls through Secrets Manager."
  }
}

run "custom_name_prefix" {
  command = plan

  variables {
    name_prefix = "ops-metrics"
  }

  assert {
    condition = (
      aws_lambda_function.poller.function_name == "ops-metrics-poller" &&
      aws_iam_role.lambda.name == "ops-metrics-role" &&
      aws_sqs_queue.dlq.name == "ops-metrics-dlq" &&
      aws_cloudwatch_event_rule.schedule.name == "ops-metrics-schedule" &&
      aws_security_group.lambda.name == "ops-metrics-lambda" &&
      aws_cloudwatch_log_group.poller.name == "/aws/lambda/ops-metrics-poller" &&
      aws_cloudwatch_metric_alarm.heartbeat["snapmirror"].alarm_name == "ops-metrics-snapmirror-heartbeat"
    )
    error_message = "Every resource name must start with name_prefix."
  }
}
