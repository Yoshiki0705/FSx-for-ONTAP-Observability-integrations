terraform {
  # 1.11.0 is the first release whose `terraform test` supports
  # `override_during = plan` on a mock provider. The offline tests rely on it
  # so that mocked ARNs are known at plan time and alarm wiring can be asserted.
  required_version = ">= 1.11.0"

  required_providers {
    aws = {
      source = "hashicorp/aws"
      # Lower bound only: 6.67.0 is the release the offline tests and the
      # live runs used; earlier 6.x releases are untested. Terraform
      # intersects a child module's constraint with the caller's, so an
      # exact pin here made a root on `~> 6.60.0` fail `init` with
      # "no available releases match the given constraints ~> 6.60.0, 6.67.0".
      # The exact pin and lock file for reproducible runs live in
      # examples/basic/. data.aws_region.current.region needs 6.0.0 or later.
      version = ">= 6.67.0"
    }
  }
}
