# Offline: mock provider, plan only, no AWS credentials.
mock_provider "aws" {
  override_during = plan

  mock_data "aws_region" {
    defaults = {
      region = "us-east-1"
    }
  }

  mock_resource "aws_sns_topic" {
    defaults = {
      arn = "arn:aws:sns:us-east-1:123456789012:mock-alarms"
    }
  }
}

variables {
  file_system_id = "fs-0123456789abcdef0"
}

run "defaults" {
  command = plan

  assert {
    condition     = aws_cloudwatch_dashboard.this.dashboard_name == "fsxn-monitoring-fsx-for-ontap"
    error_message = "Unexpected default dashboard name."
  }

  assert {
    condition     = length(aws_sns_topic.alarm) == 0 && length(aws_sns_topic_subscription.email) == 0
    error_message = "No SNS topic or subscription expected without notification_email."
  }

  assert {
    condition = (
      aws_cloudwatch_metric_alarm.storage_capacity.namespace == "AWS/FSx" &&
      aws_cloudwatch_metric_alarm.storage_capacity.metric_name == "StorageCapacityUtilization" &&
      aws_cloudwatch_metric_alarm.storage_capacity.statistic == "Average" &&
      aws_cloudwatch_metric_alarm.storage_capacity.period == 300 &&
      aws_cloudwatch_metric_alarm.storage_capacity.evaluation_periods == 3 &&
      aws_cloudwatch_metric_alarm.storage_capacity.threshold == 80 &&
      aws_cloudwatch_metric_alarm.storage_capacity.comparison_operator == "GreaterThanThreshold"
    )
    error_message = "Storage capacity alarm does not match the CloudFormation template."
  }

  assert {
    condition = aws_cloudwatch_metric_alarm.storage_capacity.dimensions == tomap({
      FileSystemId = "fs-0123456789abcdef0"
      StorageTier  = "SSD"
      DataType     = "All"
    })
    error_message = "Storage capacity alarm must use the documented FileSystemId + StorageTier + DataType dimension set."
  }

  assert {
    condition     = length(aws_cloudwatch_metric_alarm.storage_capacity.alarm_actions) == 0
    error_message = "No alarm actions expected without notification_email."
  }

  assert {
    condition = (
      aws_cloudwatch_metric_alarm.network_throughput.metric_name == "NetworkThroughputUtilization" &&
      aws_cloudwatch_metric_alarm.network_throughput.threshold == 80 &&
      aws_cloudwatch_metric_alarm.network_throughput.dimensions == tomap({ FileSystemId = "fs-0123456789abcdef0" })
    )
    error_message = "Network throughput alarm does not match the CloudFormation template."
  }

  assert {
    condition = (
      length(aws_cloudwatch_metric_alarm.file_server) == 0 &&
      length(aws_cloudwatch_metric_alarm.volume_capacity) == 0 &&
      length(aws_cloudwatch_metric_alarm.volume_inode) == 0
    )
    error_message = "Opt-in alarms must be off by default."
  }

  assert {
    condition = alltrue([
      for m in [
        "DataReadBytes", "DataWriteBytes", "DataReadOperations", "DataWriteOperations",
        "NetworkThroughputUtilization", "StorageCapacityUtilization",
        "NetworkSentBytes", "NetworkReceivedBytes", "StorageUsed",
      ] : strcontains(aws_cloudwatch_dashboard.this.dashboard_body, "\"${m}\"")
    ])
    error_message = "Dashboard body is missing an expected metric."
  }

  assert {
    condition     = length(jsondecode(aws_cloudwatch_dashboard.this.dashboard_body).widgets) == 7
    error_message = "Dashboard must have the template's 7 widgets."
  }

  assert {
    condition     = output.sns_topic_arn == null
    error_message = "sns_topic_arn must be null without notification_email."
  }
}

run "notification" {
  command = plan

  variables {
    notification_email = "ops@example.com"
  }

  assert {
    condition     = length(aws_sns_topic.alarm) == 1 && length(aws_sns_topic_subscription.email) == 1
    error_message = "Expected one SNS topic and one subscription."
  }

  assert {
    condition = (
      aws_sns_topic_subscription.email[0].protocol == "email" &&
      aws_sns_topic_subscription.email[0].endpoint == "ops@example.com"
    )
    error_message = "Subscription must be an email subscription to notification_email."
  }

  assert {
    condition = (
      contains(aws_cloudwatch_metric_alarm.storage_capacity.alarm_actions, "arn:aws:sns:us-east-1:123456789012:mock-alarms") &&
      contains(aws_cloudwatch_metric_alarm.storage_capacity.ok_actions, "arn:aws:sns:us-east-1:123456789012:mock-alarms") &&
      contains(aws_cloudwatch_metric_alarm.network_throughput.alarm_actions, "arn:aws:sns:us-east-1:123456789012:mock-alarms") &&
      contains(aws_cloudwatch_metric_alarm.network_throughput.ok_actions, "arn:aws:sns:us-east-1:123456789012:mock-alarms")
    )
    error_message = "Parity alarms must notify the SNS topic on ALARM and OK."
  }
}
