terraform {
  # 1.11.0 is the first release whose `terraform test` supports
  # `override_during = plan` on a mock provider. The offline tests rely on it
  # so that mocked ARNs are known at plan time and alarm wiring can be asserted.
  required_version = ">= 1.11.0"

  required_providers {
    aws = {
      source = "hashicorp/aws"
      # Lower bound only: 6.67.0 is the release the offline tests used; earlier
      # 6.x releases are untested. Terraform intersects a child module's
      # constraint with the caller's, so an exact pin here would make a root on
      # a different 6.x line fail `init` with "no available releases match the
      # given constraints". The exact pin and lock file for reproducible runs
      # live in examples/basic/. Only the aws provider is needed here.
      version = ">= 6.67.0"
    }
  }
}
