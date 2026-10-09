# AWS Application Load Balancer for EMYC Staging

resource "aws_lb" "staging" {
  name               = "emyc-staging-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id

  idle_timeout               = 120 # 120s accommodates heavy client queue drainage
  enable_deletion_protection = false

  tags = {
    Name = "emyc-staging-alb"
  }
}

resource "aws_lb_target_group" "app" {
  name        = "emyc-staging-app-tg"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = aws_vpc.staging.id
  target_type = "ip"

  # Least outstanding requests is optimal for variable exam submission durations
  load_balancing_algorithm_type = "least_outstanding_requests"

  deregistration_delay = 30 # 30s allows in-flight submissions to finalize

  health_check {
    enabled             = true
    path                = "/health"
    port                = "8000"
    protocol            = "HTTP"
    matcher             = "200"
    interval            = 10
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  tags = {
    Name = "emyc-staging-app-tg"
  }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.staging.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app.arn
  }
}

output "alb_dns_name" {
  description = "Public DNS hostname of the staging Application Load Balancer"
  value       = aws_lb.staging.dns_name
}
