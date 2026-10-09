terraform {
  # Same minimum as terraform/fsxn-ontap-custom-metrics: 1.11.0 is the first
  # release whose `terraform test` supports `override_during = plan` on a mock
  # provider, which the offline tests rely on.
  required_version = ">= 1.11.0"
  required_providers {
    aws = {
      source = "hashicorp/aws"
      # Lower bound only, as in the other modules: Terraform intersects a
      # child module's constraint with the caller's, so an exact pin here would
      # break callers on another 6.x release. 6.67.0 is the release the offline
      # tests used; earlier 6.x releases are untested. The exact pin and lock
      # file for reproducible runs live in examples/basic/.
      version = ">= 6.67.0"
    }
    archive = {
      source = "hashicorp/archive"
      # Builds the Lambda zip from shared/lambda/ssd_auto_increase at plan
      # time. Lower bound only; the exact pin is in examples/basic/.
      version = ">= 2.8.1"
    }
  }
}
