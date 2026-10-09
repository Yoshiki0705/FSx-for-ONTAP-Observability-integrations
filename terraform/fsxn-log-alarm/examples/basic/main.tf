# Runnable root configuration for the fsxn-log-alarm module.
#
# required_providers lives in versions.tf only: Terraform rejects a second
# declaration in the same module ("Duplicate required providers configuration").
#
# Outside this repository, replace `source = "../.."` with one of the sources
# in "Obtaining the module" of the module README.

provider "aws" {
  region = var.region
}

module "fsx_ontap_log_alarm" {
  source = "../.."

  log_group_name        = var.log_group_name
  name_prefix           = var.name_prefix
  notification_email    = var.notification_email
  alarm_sns_topic_arn   = var.alarm_sns_topic_arn
  sns_kms_master_key_id = var.sns_kms_master_key_id

  # `detections` is not set here, so the module's five shipped recipes apply
  # (autosize-fail, failed-access, bulk-delete, privileged-operations,
  # unauthorized-access). To override or extend them, including the template's
  # `custom` type as an extra key, add a `detections = { ... }` argument.

  tags = var.tags
}
