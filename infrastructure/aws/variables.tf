variable "aws_region" {
  description = "AWS region for the staging deployment"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Environment name"
  type        = string
  default     = "staging"
}

variable "app_image" {
  description = "ECR image URI for the EMYC application container"
  type        = string
  default     = "123456789012.dkr.ecr.us-east-1.amazonaws.com/emyc-app:latest"
}

variable "db_instance_class" {
  description = "Amazon RDS instance class for PostgreSQL"
  type        = string
  default     = "db.m6g.xlarge"
}

variable "db_allocated_storage" {
  description = "Allocated storage in GB for RDS"
  type        = number
  default     = 100
}

variable "db_iops" {
  description = "Provisioned IOPS for gp3 storage"
  type        = number
  default     = 12000
}

variable "db_name" {
  description = "Database name"
  type        = string
  default     = "competition_staging_db"
}

variable "db_username" {
  description = "Database master username"
  type        = string
  default     = "postgres"
}

variable "db_password" {
  description = "Database master password (passed securely via tfvars or AWS Secrets Manager)"
  type        = string
  sensitive   = true
}

variable "fargate_desired_count" {
  description = "Initial number of ECS Fargate tasks"
  type        = number
  default     = 4
}

variable "fargate_max_count" {
  description = "Maximum number of ECS Fargate tasks for 100k peak burst"
  type        = number
  default     = 32
}
