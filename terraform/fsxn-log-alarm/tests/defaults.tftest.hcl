# Offline: mock provider, plan only, no AWS credentials.
mock_provider "aws" {
  override_during = plan

  mock_resource "aws_sns_topic" {
    defaults = {
      arn = "arn:aws:sns:us-east-1:123456789012:mock-alarms"
    }
  }
}

variables {
  log_group_name = "/syslog/fsxn-admin-audit"
}

run "defaults" {
  command = plan

  assert {
    condition     = length(aws_cloudwatch_log_metric_filter.this) == 5
    error_message = "Expected one metric filter per shipped detection (5)."
  }

  assert {
    condition     = length(aws_cloudwatch_metric_alarm.this) == 5
    error_message = "Expected one metric alarm per shipped detection (5)."
  }

  assert {
    condition     = length(aws_sns_topic.alarm) == 0 && length(aws_sns_topic_subscription.email) == 0
    error_message = "No SNS topic or subscription expected without notification_email."
  }

  assert {
    condition     = output.sns_topic_arn == null
    error_message = "sns_topic_arn must be null without notification_email."
  }

  # The autoSize.fail recipe is the one this phase names explicitly: the filter
  # pattern must contain the EMS event id, and the alarm must fire on any match.
  assert {
    condition     = strcontains(aws_cloudwatch_log_metric_filter.this["autosize-fail"].pattern, "wafl.vol.autoSize.fail")
    error_message = "The autosize-fail filter pattern must contain wafl.vol.autoSize.fail."
  }

  assert {
    condition = (
      aws_cloudwatch_metric_alarm.this["autosize-fail"].threshold == 0 &&
      aws_cloudwatch_metric_alarm.this["autosize-fail"].evaluation_periods == 1 &&
      aws_cloudwatch_metric_alarm.this["autosize-fail"].datapoints_to_alarm == 1 &&
      aws_cloudwatch_metric_alarm.this["autosize-fail"].comparison_operator == "GreaterThanThreshold" &&
      aws_cloudwatch_metric_alarm.this["autosize-fail"].treat_missing_data == "notBreaching" &&
      aws_cloudwatch_metric_alarm.this["autosize-fail"].statistic == "Sum" &&
      aws_cloudwatch_metric_alarm.this["autosize-fail"].period == 300
    )
    error_message = "The autosize-fail alarm wiring does not match the intended any-occurrence detection."
  }

  # Every alarm reads exactly the metric its own filter emits (name and
  # namespace), checked per detection key, not just for autosize-fail.
  assert {
    condition = alltrue([
      for k, a in aws_cloudwatch_metric_alarm.this : (
        a.metric_name == aws_cloudwatch_log_metric_filter.this[k].metric_transformation[0].name &&
        a.namespace == aws_cloudwatch_log_metric_filter.this[k].metric_transformation[0].namespace
      )
    ])
    error_message = "Every alarm must read the metric name and namespace its own filter emits."
  }

  # Filter transformations report 0 on no-match windows (default_value "0").
  assert {
    condition = alltrue([
      for f in aws_cloudwatch_log_metric_filter.this : f.metric_transformation[0].default_value == "0"
    ])
    error_message = "Every metric transformation must set default_value = \"0\" so the metric reports 0 on no-match windows."
  }

  # Every detection names its metric from the same prefix and key.
  assert {
    condition = alltrue([
      for k, a in aws_cloudwatch_metric_alarm.this :
      a.alarm_name == "fsxn-log-alarm-${k}" && a.namespace == "FSxONTAP/LogAlarm"
    ])
    error_message = "Alarm names and namespace do not match the defaults."
  }

  assert {
    condition = alltrue([
      for a in aws_cloudwatch_metric_alarm.this : length(a.alarm_actions) == 0
    ])
    error_message = "No alarm actions expected without notification_email."
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
    condition = alltrue([
      for a in aws_cloudwatch_metric_alarm.this : (
        contains(a.alarm_actions, "arn:aws:sns:us-east-1:123456789012:mock-alarms") &&
        contains(a.ok_actions, "arn:aws:sns:us-east-1:123456789012:mock-alarms")
      )
    ])
    error_message = "Every alarm must notify the SNS topic on ALARM and OK."
  }
}

run "existing_topic" {
  command = plan

  variables {
    alarm_sns_topic_arn = "arn:aws:sns:ap-northeast-1:123456789012:fsxn-security-alerts"
    # notification_email is ignored when alarm_sns_topic_arn is set.
    notification_email = "ops@example.com"
  }

  # The module creates no topic or subscription when a caller topic is supplied.
  assert {
    condition     = length(aws_sns_topic.alarm) == 0 && length(aws_sns_topic_subscription.email) == 0
    error_message = "No topic or subscription expected when alarm_sns_topic_arn is set."
  }

  # Alarms notify the caller-owned topic.
  assert {
    condition = alltrue([
      for a in aws_cloudwatch_metric_alarm.this : (
        contains(a.alarm_actions, "arn:aws:sns:ap-northeast-1:123456789012:fsxn-security-alerts") &&
        contains(a.ok_actions, "arn:aws:sns:ap-northeast-1:123456789012:fsxn-security-alerts")
      )
    ])
    error_message = "Every alarm must notify the caller-supplied topic on ALARM and OK."
  }

  assert {
    condition     = output.sns_topic_arn == "arn:aws:sns:ap-northeast-1:123456789012:fsxn-security-alerts" && output.sns_topic_created == false
    error_message = "sns_topic_arn must be the caller topic and sns_topic_created must be false."
  }
}

run "sns_encryption" {
  command = plan

  variables {
    notification_email    = "ops@example.com"
    sns_kms_master_key_id = "alias/aws/sns"
  }

  assert {
    condition     = aws_sns_topic.alarm[0].kms_master_key_id == "alias/aws/sns" && output.sns_topic_created == true
    error_message = "The module-created topic must use the supplied KMS key, and sns_topic_created must be true."
  }
}

run "less_than_description" {
  command = plan

  variables {
    detections = {
      quiet-check = {
        pattern             = "\"heartbeat\""
        threshold           = 1
        comparison_operator = "LessThanThreshold"
      }
    }
  }

  # A less-than alarm must not be described as firing "above" the threshold.
  assert {
    condition = (
      strcontains(aws_cloudwatch_metric_alarm.this["quiet-check"].alarm_description, "below") &&
      !strcontains(aws_cloudwatch_metric_alarm.this["quiet-check"].alarm_description, "above")
    )
    error_message = "A LessThanThreshold alarm's generated description must say 'below', not 'above'."
  }
}

run "custom_detection" {
  command = plan

  variables {
    detections = {
      my-custom = {
        pattern = "\"ERROR\""
      }
    }
  }

  assert {
    condition = (
      length(aws_cloudwatch_log_metric_filter.this) == 1 &&
      length(aws_cloudwatch_metric_alarm.this) == 1 &&
      aws_cloudwatch_log_metric_filter.this["my-custom"].pattern == "\"ERROR\"" &&
      aws_cloudwatch_metric_alarm.this["my-custom"].threshold == 0 &&
      aws_cloudwatch_metric_alarm.this["my-custom"].comparison_operator == "GreaterThanThreshold"
    )
    error_message = "A caller-supplied detection must produce one filter and one alarm with the optional defaults."
  }
}
