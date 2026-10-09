# Offline: mock aws provider, plan only. Variable-validation runs set one
# out-of-range value and expect that variable's validation to fail. Precondition
# runs mock the data source per deployment shape and assert the ceiling
# precondition and the auto-requires-COMPLIANCE precondition.
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
  mock_data "aws_fsx_ontap_file_system" {
    defaults = {
      deployment_type  = "SINGLE_AZ_1"
      ha_pairs         = 1
      storage_capacity = 1024
    }
  }
}

variables {
  file_system_id           = "fs-0123456789abcdef0"
  max_storage_capacity_gib = 4096
  decision_archive_bucket  = "ssd-auto-increase-audit"
}

# --- variable validation -------------------------------------------------

run "ceiling_below_minimum" {
  command = plan
  variables {
    max_storage_capacity_gib = 1023
  }
  expect_failures = [var.max_storage_capacity_gib]
}

run "ceiling_above_maximum" {
  command = plan
  variables {
    max_storage_capacity_gib = 1048577
  }
  expect_failures = [var.max_storage_capacity_gib]
}

run "ceiling_fractional" {
  command = plan
  variables {
    max_storage_capacity_gib = 2048.5
  }
  expect_failures = [var.max_storage_capacity_gib]
}

run "ceiling_boundaries_accepted" {
  command = plan
  variables {
    max_storage_capacity_gib = 196608
  }
  assert {
    condition     = var.max_storage_capacity_gib == 196608
    error_message = "The documented minimum/maximum boundaries must be accepted by the variable validation."
  }
}

run "file_system_id_malformed" {
  command = plan
  variables {
    file_system_id = "fs-123"
  }
  expect_failures = [var.file_system_id]
}

run "mode_invalid" {
  command = plan
  variables {
    mode = "sometimes"
  }
  expect_failures = [var.mode]
}

run "increase_percent_below_floor" {
  command = plan
  variables {
    increase_percent = 5
  }
  expect_failures = [var.increase_percent]
}

run "increase_percent_fractional" {
  command = plan
  variables {
    increase_percent = 12.5
  }
  expect_failures = [var.increase_percent]
}

run "indeterminate_hours_out_of_range" {
  command = plan
  variables {
    indeterminate_reconcile_hours = 25
  }
  expect_failures = [var.indeterminate_reconcile_hours]
}

run "archive_bucket_malformed" {
  command = plan
  variables {
    decision_archive_bucket = "A"
  }
  expect_failures = [var.decision_archive_bucket]
}

run "archive_mode_invalid" {
  command = plan
  variables {
    decision_archive_required_mode = "LEGAL_HOLD"
  }
  expect_failures = [var.decision_archive_required_mode]
}

# A prefix containing an IAM wildcard would, once interpolated into the
# execution-role S3 Resource ARN, widen s3:PutObject/s3:GetObjectRetention past
# the intended prefix. The validation must reject '*' and '?'.
run "archive_prefix_rejects_asterisk" {
  command = plan
  variables {
    decision_archive_prefix = "fsx-ssd-auto-increase/*"
  }
  expect_failures = [var.decision_archive_prefix]
}

run "archive_prefix_rejects_question_mark" {
  command = plan
  variables {
    decision_archive_prefix = "fsx-ssd-auto-increase/?"
  }
  expect_failures = [var.decision_archive_prefix]
}

run "aggregate_names_duplicate" {
  command = plan
  variables {
    aggregate_names = ["aggr1", "aggr1"]
  }
  expect_failures = [var.aggregate_names]
}

run "notification_email_malformed" {
  command = plan
  variables {
    notification_email = "not-an-email"
  }
  expect_failures = [var.notification_email]
}

# --- ceiling precondition on SINGLE_AZ_1 ---------------------------------

run "single_az_1_at_maximum_passes" {
  command = plan
  variables {
    max_storage_capacity_gib = 196608
  }
  assert {
    condition     = aws_lambda_function.evaluator.function_name == "fsxn-ssd-auto-increase-evaluator"
    error_message = "196608 GiB on SINGLE_AZ_1 is the documented maximum and must pass the precondition."
  }
}

# 196609 is the immediate boundary: one GiB above the 196608 SINGLE_AZ_1
# maximum. Using it (not a distant 300000) catches an off-by-one precondition
# that would accept values just above the maximum.
run "single_az_1_one_above_maximum_fails_precondition" {
  command = plan
  variables {
    max_storage_capacity_gib = 196609
  }
  expect_failures = [aws_lambda_function.evaluator]
}

# --- ceiling precondition on SINGLE_AZ_2 ha_pairs ------------------------

run "single_az_2_two_ha_pairs_at_1_pib_passes" {
  command = plan
  variables {
    max_storage_capacity_gib = 1048576
  }
  override_data {
    target = data.aws_fsx_ontap_file_system.target
    values = {
      deployment_type  = "SINGLE_AZ_2"
      ha_pairs         = 2
      storage_capacity = 1024
    }
  }
  assert {
    condition     = aws_lambda_function.evaluator.function_name == "fsxn-ssd-auto-increase-evaluator"
    error_message = "SINGLE_AZ_2 with 2 HA pairs allows 1,048,576 GiB."
  }
}

# 524288 is the exact SINGLE_AZ_2 per-HA-pair maximum (one pair). Use the
# immediate boundary pair 524288-pass / 524289-fail (not a distant 600000) so
# an off-by-one precondition that accepts one GiB above the maximum is caught.
run "single_az_2_one_ha_pair_at_maximum_passes" {
  command = plan
  variables {
    max_storage_capacity_gib = 524288
  }
  override_data {
    target = data.aws_fsx_ontap_file_system.target
    values = {
      deployment_type  = "SINGLE_AZ_2"
      ha_pairs         = 1
      storage_capacity = 1024
    }
  }
  assert {
    condition     = aws_lambda_function.evaluator.function_name == "fsxn-ssd-auto-increase-evaluator"
    error_message = "524288 GiB on SINGLE_AZ_2 with 1 HA pair is the documented maximum and must pass."
  }
}

run "single_az_2_one_ha_pair_one_above_maximum_fails" {
  command = plan
  variables {
    max_storage_capacity_gib = 524289
  }
  override_data {
    target = data.aws_fsx_ontap_file_system.target
    values = {
      deployment_type  = "SINGLE_AZ_2"
      ha_pairs         = 1
      storage_capacity = 1024
    }
  }
  expect_failures = [aws_lambda_function.evaluator]
}

# --- auto-requires-COMPLIANCE precondition -------------------------------

run "auto_with_governance_fails_precondition" {
  command = plan
  variables {
    mode                           = "auto"
    decision_archive_required_mode = "GOVERNANCE"
  }
  expect_failures = [aws_lambda_function.evaluator]
}

run "auto_with_compliance_passes" {
  command = plan
  variables {
    mode                           = "auto"
    decision_archive_required_mode = "COMPLIANCE"
  }
  assert {
    condition     = aws_lambda_function.evaluator.environment[0].variables["MODE"] == "auto"
    error_message = "auto with COMPLIANCE must pass the precondition."
  }
}

# --- ceiling_leaves_room check (warns, does not fail) --------------------

run "ceiling_below_10_percent_warns_without_failing" {
  command = plan
  variables {
    # current 10000, ceil(10000*1.1)=11000; a ceiling of 10500 trips the check
    # warning. The plan itself still succeeds (the check is non-blocking), so
    # the resources plan; only the check assertion is expected to fail, listed
    # here so terraform test records it as the warning it is rather than a
    # module failure.
    max_storage_capacity_gib = 10500
  }
  override_data {
    target = data.aws_fsx_ontap_file_system.target
    values = {
      deployment_type  = "SINGLE_AZ_1"
      ha_pairs         = 1
      storage_capacity = 10000
    }
  }
  expect_failures = [check.ceiling_leaves_room]
}
