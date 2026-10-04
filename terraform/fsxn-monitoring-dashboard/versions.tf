terraform {
  # 1.11.0 is the first release whose `terraform test` supports
  # `override_during = plan` on a mock provider. The offline tests rely on it
  # so that mocked ARNs are known at plan time and alarm wiring can be asserted.
  required_version = ">= 1.11.0"

  required_providers {
    aws = {
      source = "hashicorp/aws"
      # Exact pin, per the repository's dependency rule. Callers on a
      # different 6.x release must change this pin to use the module.
      version = "= 6.67.0"
    }
  }
}
