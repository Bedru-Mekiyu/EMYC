# Amazon RDS for PostgreSQL Staging Database

resource "aws_db_parameter_group" "pg16_tuned" {
  name   = "emyc-staging-pg16-tuned"
  family = "postgres16"

  parameter {
    name  = "max_connections"
    value = "1200"
  }

  parameter {
    name  = "shared_buffers"
    value = "{DBInstanceClassMemory/32768}" # 4 GB shared buffers (25% of 16 GB RAM in 8KB blocks)
  }

  parameter {
    name  = "work_mem"
    value = "16384" # 16 MB
  }

  parameter {
    name  = "maintenance_work_mem"
    value = "131072" # 128 MB
  }

  parameter {
    name  = "wal_buffers"
    value = "2048" # 16 MB (2048 * 8KB)
  }

  parameter {
    name  = "checkpoint_completion_target"
    value = "0.9"
  }

  parameter {
    name  = "statement_timeout"
    value = "180000" # 180s
  }

  tags = {
    Name = "emyc-staging-pg16-param-group"
  }
}

resource "aws_db_instance" "staging_db" {
  identifier           = "emyc-staging-db"
  engine               = "postgres"
  engine_version       = "16.3"
  instance_class       = var.db_instance_class # db.m6g.xlarge (4 vCPU, 16 GB RAM)
  allocated_storage    = var.db_allocated_storage # 100 GB
  max_allocated_storage = 500 # Auto-scale up to 500 GB if needed
  storage_type         = "gp3"
  iops                 = var.db_iops # 12,000 Provisioned IOPS
  storage_throughput   = 500 # 500 MB/s

  db_name  = var.db_name
  username = var.db_username
  password = var.db_password

  db_subnet_group_name   = aws_db_subnet_group.rds.name
  vpc_security_group_ids = [aws_security_group.rds.id]
  parameter_group_name   = aws_db_parameter_group.pg16_tuned.name

  multi_az               = false # Single-AZ for staging cost minimization
  publicly_accessible    = false # Strictly private
  skip_final_snapshot    = true  # Staging tear-down ease
  deletion_protection    = false

  backup_retention_period = 1
  backup_window           = "03:00-04:00"
  maintenance_window      = "Sun:04:30-Sun:05:30"

  enabled_cloudwatch_logs_exports = ["postgresql", "upgrade"]
  monitoring_interval             = 1 # 1-second Enhanced Monitoring for granular disk I/O profiling
  monitoring_role_arn             = aws_iam_role.rds_monitoring.arn

  tags = {
    Name = "emyc-staging-rds-postgres"
  }
}

# IAM Role for RDS Enhanced Monitoring
resource "aws_iam_role" "rds_monitoring" {
  name = "emyc-staging-rds-monitoring-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "monitoring.rds.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "rds_monitoring" {
  role       = aws_iam_role.rds_monitoring.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonRDSEnhancedMonitoringRole"
}
