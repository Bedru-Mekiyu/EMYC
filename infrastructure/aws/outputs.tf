# Output Definitions for EMYC Staging Infrastructure

output "alb_dns_name" {
  description = "Public DNS hostname of the staging Application Load Balancer"
  value       = aws_lb.staging.dns_name
}

output "alb_arn" {
  description = "Amazon Resource Name (ARN) of the staging Application Load Balancer"
  value       = aws_lb.staging.arn
}

output "target_group_arn" {
  description = "ARN of the staging ECS Target Group"
  value       = aws_lb_target_group.app.arn
}

output "ecs_cluster_name" {
  description = "Name of the ECS staging cluster"
  value       = aws_ecs_cluster.staging.name
}

output "ecs_service_name" {
  description = "Name of the ECS staging application service"
  value       = aws_ecs_service.app.name
}

output "rds_endpoint" {
  description = "Connection endpoint of the PostgreSQL RDS staging database (host:port)"
  value       = aws_db_instance.staging_db.endpoint
}

output "rds_address" {
  description = "Hostname address of the PostgreSQL RDS staging database"
  value       = aws_db_instance.staging_db.address
}

output "vpc_id" {
  description = "ID of the staging VPC"
  value       = aws_vpc.staging.id
}

output "nat_gateway_id" {
  description = "ID of the single NAT Gateway deployed for staging egress"
  value       = aws_nat_gateway.nat.id
}

output "nat_eip_allocation_id" {
  description = "Allocation ID of the Elastic IP associated with the NAT Gateway"
  value       = aws_eip.nat.id
}
