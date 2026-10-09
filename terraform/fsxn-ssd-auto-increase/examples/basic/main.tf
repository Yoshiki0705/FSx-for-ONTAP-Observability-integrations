# Runnable root configuration for the fsxn-ssd-auto-increase module.
#
# required_providers lives in versions.tf only: Terraform rejects a second
# declaration in the same module ("Duplicate required providers configuration").
#
# Outside this repository, replace `source = "../.."` with one of the sources
# in "Obtaining the module" of the module README.

provider "aws" {
  region = var.region
}

module "ssd_auto_increase" {
  source = "../.."

  name_prefix              = var.name_prefix
  file_system_id           = var.file_system_id
  max_storage_capacity_gib = var.max_storage_capacity_gib
  mode                     = var.mode

  trigger_threshold_percent     = var.trigger_threshold_percent
  increase_percent              = var.increase_percent
  reevaluation_schedule         = var.reevaluation_schedule
  log_retention_days            = var.log_retention_days
  indeterminate_reconcile_hours = var.indeterminate_reconcile_hours

  decision_archive_bucket             = var.decision_archive_bucket
  decision_archive_prefix             = var.decision_archive_prefix
  decision_archive_required_mode      = var.decision_archive_required_mode
  decision_archive_min_retention_days = var.decision_archive_min_retention_days

  aggregate_names    = var.aggregate_names
  notification_email = var.notification_email

  tags = var.tags
}
