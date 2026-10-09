# Offline: mock provider, plan only, no AWS credentials.
mock_provider "aws" {
  override_during = plan
}

variables {
  log_group_name = "/syslog/fsxn-admin-audit"
}

run "detections_empty" {
  command = plan
  variables {
    detections = {}
  }
  expect_failures = [var.detections]
}

run "datapoints_exceed_evaluation_periods" {
  command = plan
  variables {
    detections = {
      bad = {
        pattern             = "\"x\""
        evaluation_periods  = 2
        datapoints_to_alarm = 3
      }
    }
  }
  expect_failures = [var.detections]
}

run "evaluation_periods_below_minimum" {
  command = plan
  variables {
    detections = {
      bad = {
        pattern            = "\"x\""
        evaluation_periods = 0
      }
    }
  }
  expect_failures = [var.detections]
}

run "datapoints_below_minimum" {
  command = plan
  variables {
    detections = {
      bad = {
        pattern             = "\"x\""
        evaluation_periods  = 1
        datapoints_to_alarm = 0
      }
    }
  }
  expect_failures = [var.detections]
}

run "evaluation_periods_above_maximum" {
  command = plan
  variables {
    detections = {
      bad = {
        pattern             = "\"x\""
        evaluation_periods  = 101
        datapoints_to_alarm = 101
      }
    }
  }
  expect_failures = [var.detections]
}

run "detection_name_too_long" {
  command = plan
  variables {
    # name_prefix (fsxn-log-alarm, 14 chars) + "-" + 255-char key exceeds the
    # 255-char CloudWatch AlarmName/MetricName limit.
    detections = {
      "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" = {
        pattern = "\"x\""
      }
    }
  }
  expect_failures = [var.detections]
}

run "period_not_in_allowed_set" {
  command = plan
  variables {
    detections = {
      bad = {
        pattern        = "\"x\""
        period_seconds = 120
      }
    }
  }
  expect_failures = [var.detections]
}

run "bad_comparison_operator" {
  command = plan
  variables {
    detections = {
      bad = {
        pattern             = "\"x\""
        comparison_operator = "EqualToThreshold"
      }
    }
  }
  expect_failures = [var.detections]
}

run "log_group_name_malformed" {
  command = plan
  variables {
    log_group_name = "bad name with spaces"
  }
  expect_failures = [var.log_group_name]
}

run "notification_email_malformed" {
  command = plan
  variables {
    notification_email = "not-an-email"
  }
  expect_failures = [var.notification_email]
}

run "name_prefix_malformed" {
  command = plan
  variables {
    name_prefix = "bad prefix!"
  }
  expect_failures = [var.name_prefix]
}
