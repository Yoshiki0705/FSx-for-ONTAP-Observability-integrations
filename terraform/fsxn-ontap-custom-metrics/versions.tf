terraform {
  # Same minimum as terraform/fsxn-monitoring-dashboard: 1.11.0 is the first
  # release whose `terraform test` supports `override_during = plan` on a mock
  # provider, which the offline tests rely on.
  required_version = ">= 1.11.0"
  required_providers {
    aws = {
      source = "hashicorp/aws"
      # Lower bound only, as in the dashboard module: Terraform intersects a
      # child module's constraint with the caller's, so an exact pin here would
      # break callers on another 6.x release. 6.67.0 is the release the offline
      # tests used; earlier 6.x releases are untested. The exact pin and lock
      # file for reproducible runs live in examples/basic/.
      version = ">= 6.67.0"
    }
    archive = {
      source = "hashicorp/archive"
      # Builds the Lambda zip from shared/lambda/ontap_metrics at plan time.
      # Lower bound only. 2.8.1 was the latest release on the Terraform
      # Registry when checked on 2026-10-07 (published 2026-09-11); the exact
      # pin is in examples/basic/.
      version = ">= 2.8.1"
    }
  }
}
