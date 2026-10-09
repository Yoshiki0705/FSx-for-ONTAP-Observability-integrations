terraform {
  # Same minimum as the module (`terraform test` with `override_during = plan`).
  required_version = ">= 1.11.0"

  # Exact pin, because this is a root configuration: CONTRIBUTING.md keeps
  # exact pins and a tracked .terraform.lock.hcl for roots and examples, and a
  # lower bound only for the reusable module in ../../versions.tf.
  #
  # No backend block, so state is local (terraform.tfstate in this directory).
  # For shared or long-lived use, add an `s3` backend block here.
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "= 6.67.0"
    }
  }
}
