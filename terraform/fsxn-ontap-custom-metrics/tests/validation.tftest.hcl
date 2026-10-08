# Offline: mock aws provider, plan only, no AWS credentials. Each run sets one
# out-of-range input and expects exactly that variable's validation to fail.
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
  # Known ARNs so the execution-role policy is known at plan time for the
  # KMS acceptance runs below.
  mock_resource "aws_sqs_queue" {
    defaults = {
      arn = "arn:aws:sqs:us-east-1:123456789012:fsxn-ontap-metrics-dlq"
    }
  }
  mock_resource "aws_cloudwatch_log_group" {
    defaults = {
      arn = "arn:aws:logs:us-east-1:123456789012:log-group:/aws/lambda/fsxn-ontap-metrics-poller"
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

run "file_system_id_malformed" {
  command = plan
  variables {
    file_system_id = "fs-123"
  }
  expect_failures = [var.file_system_id]
}

run "management_ip_octet_too_large" {
  command = plan
  variables {
    ontap_management_ip = "256.1.1.1"
  }
  expect_failures = [var.ontap_management_ip]
}

run "management_ip_three_octets" {
  command = plan
  variables {
    ontap_management_ip = "198.51.100"
  }
  expect_failures = [var.ontap_management_ip]
}

run "secret_arn_malformed" {
  command = plan
  variables {
    ontap_credentials_secret_arn = "ontap-monitor"
  }
  expect_failures = [var.ontap_credentials_secret_arn]
}

run "kms_key_arn_malformed" {
  command = plan
  variables {
    ontap_credentials_kms_key_arn = "alias/aws/secretsmanager"
  }
  expect_failures = [var.ontap_credentials_kms_key_arn]
}

run "kms_key_arn_bad_key_id" {
  command = plan
  variables {
    # mrk- prefix with 31 hex characters instead of 32.
    ontap_credentials_kms_key_arn = "arn:aws:kms:us-east-1:123456789012:key/mrk-1234abcd12ab34cd56ef1234567890a"
  }
  expect_failures = [var.ontap_credentials_kms_key_arn]
}

# Positive controls for the two documented key ID forms.
run "kms_key_arn_single_region_accepted" {
  command = plan
  variables {
    ontap_credentials_kms_key_arn = "arn:aws:kms:us-east-1:123456789012:key/1234abcd-12ab-34cd-56ef-1234567890ab"
  }
  assert {
    condition     = strcontains(aws_iam_role_policy.lambda.policy, "key/1234abcd-12ab-34cd-56ef-1234567890ab")
    error_message = "A single-Region key ARN must be accepted and scoped in the policy."
  }
}

run "kms_key_arn_multi_region_accepted" {
  command = plan
  variables {
    ontap_credentials_kms_key_arn = "arn:aws:kms:us-east-1:123456789012:key/mrk-1234abcd12ab34cd56ef1234567890ab"
  }
  assert {
    condition     = strcontains(aws_iam_role_policy.lambda.policy, "key/mrk-1234abcd12ab34cd56ef1234567890ab")
    error_message = "A multi-Region key ARN must be accepted and scoped in the policy."
  }
}

run "vpc_id_malformed" {
  command = plan
  variables {
    vpc_id = "vpc-xyz"
  }
  expect_failures = [var.vpc_id]
}

run "subnet_ids_empty" {
  command = plan
  variables {
    subnet_ids = []
  }
  expect_failures = [var.subnet_ids]
}

run "subnet_ids_malformed" {
  command = plan
  variables {
    subnet_ids = ["subnet-xyz"]
  }
  expect_failures = [var.subnet_ids]
}

run "subnet_ids_duplicate" {
  command = plan
  variables {
    subnet_ids = ["subnet-0123456789abcdef0", "subnet-0123456789abcdef0"]
  }
  expect_failures = [var.subnet_ids]
}

run "egress_cidr_malformed" {
  command = plan
  variables {
    aws_api_egress_cidr_blocks = ["198.51.100.0"]
  }
  expect_failures = [var.aws_api_egress_cidr_blocks]
}

run "both_collectors_disabled" {
  command = plan
  variables {
    enable_qtree_collector      = false
    enable_snapmirror_collector = false
  }
  expect_failures = [var.enable_snapmirror_collector]
}

run "qtree_without_svm_name" {
  command = plan
  variables {
    qtree_svm_name = null
  }
  expect_failures = [var.qtree_svm_name]
}

run "qtree_svm_name_too_long" {
  command = plan
  variables {
    qtree_svm_name = "s234567890123456789012345678901234567890123456789012345678901234567"
  }
  expect_failures = [var.qtree_svm_name]
}

run "poll_interval_zero" {
  command = plan
  variables {
    poll_interval_minutes = 0
  }
  expect_failures = [var.poll_interval_minutes]
}

run "poll_interval_61" {
  command = plan
  variables {
    poll_interval_minutes = 61
  }
  expect_failures = [var.poll_interval_minutes]
}

run "poll_interval_fraction" {
  command = plan
  variables {
    poll_interval_minutes = 2.5
  }
  expect_failures = [var.poll_interval_minutes]
}

run "qtree_threshold_49" {
  command = plan
  variables {
    qtree_quota_threshold_percent = 49
  }
  expect_failures = [var.qtree_quota_threshold_percent]
}

run "qtree_threshold_100" {
  command = plan
  variables {
    qtree_quota_threshold_percent = 100
  }
  expect_failures = [var.qtree_quota_threshold_percent]
}

run "lag_threshold_59" {
  command = plan
  variables {
    snapmirror_lag_threshold_seconds = 59
  }
  expect_failures = [var.snapmirror_lag_threshold_seconds]
}

run "lag_threshold_above_30_days" {
  command = plan
  variables {
    snapmirror_lag_threshold_seconds = 2592001
  }
  expect_failures = [var.snapmirror_lag_threshold_seconds]
}

run "max_relationships_zero" {
  command = plan
  variables {
    snapmirror_max_relationships = 0
  }
  expect_failures = [var.snapmirror_max_relationships]
}

run "max_relationships_1001" {
  command = plan
  variables {
    snapmirror_max_relationships = 1001
  }
  expect_failures = [var.snapmirror_max_relationships]
}

run "name_prefix_49_chars" {
  command = plan
  variables {
    name_prefix = "a234567890123456789012345678901234567890123456789"
  }
  expect_failures = [var.name_prefix]
}

run "name_prefix_bad_character" {
  command = plan
  variables {
    name_prefix = "ontap metrics"
  }
  expect_failures = [var.name_prefix]
}

run "notification_email_malformed" {
  command = plan
  variables {
    notification_email = "not-an-email"
  }
  expect_failures = [var.notification_email]
}

run "ca_path_without_layer" {
  command = plan
  variables {
    ca_cert_path = "/opt/certs/ontap-ca.pem"
  }
  expect_failures = [var.ca_cert_path]
}

run "ca_layer_malformed" {
  command = plan
  variables {
    ca_cert_path      = "/opt/certs/ontap-ca.pem"
    ca_cert_layer_arn = "ontap-ca"
  }
  expect_failures = [var.ca_cert_layer_arn]
}

run "log_retention_not_allowed" {
  command = plan
  variables {
    log_retention_days = 31
  }
  expect_failures = [var.log_retention_days]
}

# Positive control: the boundary values themselves are accepted, so the
# failures above come from the out-of-range side and not from a rule that
# rejects everything.
run "boundaries_accepted" {
  command = plan
  variables {
    name_prefix                      = "a23456789012345678901234567890123456789012345678"
    poll_interval_minutes            = 60
    qtree_quota_threshold_percent    = 99
    snapmirror_lag_threshold_seconds = 60
    snapmirror_max_relationships     = 1000
    qtree_svm_name                   = "s23456789012345678901234567890123456789012345678901234567890123456"
    ontap_management_ip              = "255.255.255.255"
  }
  assert {
    condition     = aws_lambda_function.poller.function_name == "a23456789012345678901234567890123456789012345678-poller"
    error_message = "A 48-character prefix must be accepted."
  }
}
