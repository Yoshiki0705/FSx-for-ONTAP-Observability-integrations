# Runnable root configuration for the fsxn-monitoring-dashboard module.
#
# required_providers lives in versions.tf only: Terraform rejects a second
# declaration in the same module ("Duplicate required providers configuration").
#
# Outside this repository, replace `source = "../.."` with one of the sources
# in "Obtaining the module" of the module README.

provider "aws" {
  region = var.region
}

module "fsx_ontap_monitoring" {
  source = "../.."

  file_system_id             = var.file_system_id
  file_system_name           = var.file_system_name
  name_prefix                = var.name_prefix
  capacity_threshold_percent = var.capacity_threshold_percent
  notification_email         = var.notification_email

  # Opt-in alarms (all off by default)
  enable_cpu_utilization_alarm             = var.enable_cpu_utilization_alarm
  enable_disk_iops_utilization_alarm       = var.enable_disk_iops_utilization_alarm
  enable_disk_throughput_utilization_alarm = var.enable_disk_throughput_utilization_alarm
  file_server_names                        = var.file_server_names
  volume_ids                               = var.volume_ids

  tags = var.tags
}
