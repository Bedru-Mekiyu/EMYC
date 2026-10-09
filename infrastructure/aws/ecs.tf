# AWS ECS Fargate Cluster & Service Configuration

resource "aws_ecs_cluster" "staging" {
  name = "emyc-staging-cluster"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = {
    Name = "emyc-staging-cluster"
  }
}

resource "aws_cloudwatch_log_group" "app" {
  name              = "/ecs/emyc-staging-app"
  retention_in_days = 7

  tags = {
    Name = "emyc-staging-app-logs"
  }
}

# IAM Task Execution Role
resource "aws_iam_role" "ecs_execution" {
  name = "emyc-staging-ecs-execution-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "ecs_execution" {
  role       = aws_iam_role.ecs_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# IAM Task Role (Runtime permissions)
resource "aws_iam_role" "ecs_task" {
  name = "emyc-staging-ecs-task-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_ecs_task_definition" "app" {
  family                   = "emyc-staging-app"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "2048" # 2 vCPU
  memory                   = "4096" # 4 GB RAM
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([
    {
      name      = "emyc-app"
      image     = var.app_image
      essential = true

      portMappings = [
        {
          containerPort = 8000
          hostPort      = 8000
          protocol      = "tcp"
        }
      ]

      environment = [
        { name = "ENVIRONMENT", value = "staging" },
        { name = "DEBUG", value = "false" },
        { name = "BOT_MODE", value = "disabled" }, # Bot disabled during load benchmark
        { name = "DATABASE_URL", value = "postgresql+asyncpg://${var.db_username}:${var.db_password}@${aws_db_instance.staging_db.endpoint}/${var.db_name}" },
        { name = "DATABASE_SYNC_URL", value = "postgresql://${var.db_username}:${var.db_password}@${aws_db_instance.staging_db.endpoint}/${var.db_name}" },
        { name = "WEB_CONCURRENCY", value = "4" }, # 4 Uvicorn workers per task
        { name = "UVICORN_KEEP_ALIVE", value = "65" },
        { name = "DB_POOL_SIZE", value = "10" },
        { name = "DB_MAX_OVERFLOW", value = "2" },
        { name = "DB_POOL_TIMEOUT", value = "180.0" },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.app.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "app"
        }
      }

      healthCheck = {
        command     = ["CMD-SHELL", "curl -f http://localhost:8000/health || exit 1"]
        interval    = 10
        timeout     = 5
        retries     = 3
        startPeriod = 15
      }
    }
  ])

  tags = {
    Name = "emyc-staging-task-def"
  }
}

resource "aws_ecs_service" "app" {
  name            = "emyc-staging-app-service"
  cluster         = aws_ecs_cluster.staging.id
  task_definition = aws_ecs_task_definition.app.arn
  desired_count   = var.fargate_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.private_app[*].id
    security_groups  = [aws_security_group.ecs_app.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.app.arn
    container_name   = "emyc-app"
    container_port   = 8000
  }

  deployment_controller {
    type = "ECS"
  }

  tags = {
    Name = "emyc-staging-app-service"
  }

  depends_on = [aws_lb_listener.http]
}

# Auto Scaling Target (Scale 4 -> 32 tasks based on target tracking)
resource "aws_appautoscaling_target" "ecs" {
  max_capacity       = var.fargate_max_count
  min_capacity       = var.fargate_desired_count
  resource_id        = "service/${aws_ecs_cluster.staging.name}/${aws_ecs_service.app.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"
}

resource "aws_appautoscaling_policy" "ecs_cpu" {
  name               = "emyc-staging-cpu-autoscaling"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.ecs.resource_id
  scalable_dimension = aws_appautoscaling_target.ecs.scalable_dimension
  service_namespace  = aws_appautoscaling_target.ecs.service_namespace

  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
    target_value       = 65.0
    scale_in_cooldown  = 60
    scale_out_cooldown = 15
  }
}
