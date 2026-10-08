# Runnable root configuration for the fsxn-ontap-custom-metrics module.
#
# required_providers lives in versions.tf only: Terraform rejects a second
# declaration in the same module ("Duplicate required providers configuration").
#
# Outside this repository, replace `source = "../.."` with one of the sources
# in "Obtaining the module" of the module README.

provider "aws" {
  region = var.region
}

module "ontap_custom_metrics" {
  source = "../.."

  name_prefix                   = var.name_prefix
  file_system_id                = var.file_system_id
  ontap_management_ip           = var.ontap_management_ip
  ontap_credentials_secret_arn  = var.ontap_credentials_secret_arn
  ontap_credentials_kms_key_arn = var.ontap_credentials_kms_key_arn

  vpc_id                         = var.vpc_id
  subnet_ids                     = var.subnet_ids
  aws_api_egress_cidr_blocks     = var.aws_api_egress_cidr_blocks
  create_monitoring_endpoint     = var.create_monitoring_endpoint
  create_secretsmanager_endpoint = var.create_secretsmanager_endpoint

  enable_qtree_collector      = var.enable_qtree_collector
  enable_snapmirror_collector = var.enable_snapmirror_collector
  qtree_svm_name              = var.qtree_svm_name

  poll_interval_minutes            = var.poll_interval_minutes
  qtree_quota_threshold_percent    = var.qtree_quota_threshold_percent
  snapmirror_lag_threshold_seconds = var.snapmirror_lag_threshold_seconds
  snapmirror_max_relationships     = var.snapmirror_max_relationships

  ca_cert_path       = var.ca_cert_path
  ca_cert_layer_arn  = var.ca_cert_layer_arn
  log_retention_days = var.log_retention_days
  notification_email = var.notification_email

  tags = var.tags
}
