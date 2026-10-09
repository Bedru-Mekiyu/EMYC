# Terraform AWS Staging Environment Configuration for EMYC 100k Capacity Validation

terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "EMYC"
      Environment = "staging"
      ManagedBy   = "Terraform"
      Purpose     = "100k-Capacity-Validation"
    }
  }
}
