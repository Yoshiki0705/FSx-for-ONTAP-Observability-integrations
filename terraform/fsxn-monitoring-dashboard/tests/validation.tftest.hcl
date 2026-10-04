# Offline: mock provider, plan only, no AWS credentials.
mock_provider "aws" {
  override_during = plan

  mock_data "aws_region" {
    defaults = {
      region = "us-east-1"
    }
  }
}

variables {
  file_system_id = "fs-0123456789abcdef0"
}

run "capacity_threshold_zero" {
  command = plan
  variables {
    capacity_threshold_percent = 0
  }
  expect_failures = [var.capacity_threshold_percent]
}

run "capacity_threshold_150" {
  command = plan
  variables {
    capacity_threshold_percent = 150
  }
  expect_failures = [var.capacity_threshold_percent]
}

run "capacity_threshold_96" {
  command = plan
  variables {
    capacity_threshold_percent = 96
  }
  expect_failures = [var.capacity_threshold_percent]
}

run "throughput_threshold_zero" {
  command = plan
  variables {
    throughput_threshold_percent = 0
  }
  expect_failures = [var.throughput_threshold_percent]
}

run "throughput_threshold_150" {
  command = plan
  variables {
    throughput_threshold_percent = 150
  }
  expect_failures = [var.throughput_threshold_percent]
}

run "file_system_id_malformed" {
  command = plan
  variables {
    file_system_id = "fs-123"
  }
  expect_failures = [var.file_system_id]
}

run "volume_id_malformed" {
  command = plan
  variables {
    volume_ids = ["vol-1"]
  }
  expect_failures = [var.volume_ids]
}

run "notification_email_malformed" {
  command = plan
  variables {
    notification_email = "not-an-email"
  }
  expect_failures = [var.notification_email]
}
