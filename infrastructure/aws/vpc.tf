# VPC Networking for EMYC Staging Capacity Validation

data "aws_availability_zones" "available" {
  state = "available"
}

resource "aws_vpc" "staging" {
  cidr_block           = "10.100.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = {
    Name = "emyc-staging-vpc"
  }
}

# Internet Gateway for ALB public traffic
resource "aws_internet_gateway" "gw" {
  vpc_id = aws_vpc.staging.id

  tags = {
    Name = "emyc-staging-igw"
  }
}

# Public Subnets (Multi-AZ for ALB)
resource "aws_subnet" "public" {
  count                   = 3
  vpc_id                  = aws_vpc.staging.id
  cidr_block              = cidrsubnet(aws_vpc.staging.cidr_block, 8, count.index + 1) # 10.100.1.0/24, 2.0/24, 3.0/24
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true

  tags = {
    Name = "emyc-staging-public-subnet-${count.index + 1}"
  }
}

# NAT Gateway for Outbound Egress from Private Subnets (Single NAT for cost optimization in staging)
resource "aws_eip" "nat" {
  domain = "vpc"

  tags = {
    Name = "emyc-staging-nat-eip"
  }
}

resource "aws_nat_gateway" "nat" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public[0].id

  tags = {
    Name = "emyc-staging-nat-gw"
  }

  depends_on = [aws_internet_gateway.gw]
}

# Private App Subnets (For ECS Fargate Tasks)
resource "aws_subnet" "private_app" {
  count             = 3
  vpc_id            = aws_vpc.staging.id
  cidr_block        = cidrsubnet(aws_vpc.staging.cidr_block, 8, count.index + 10) # 10.100.10.0/24, 11.0/24, 12.0/24
  availability_zone = data.aws_availability_zones.available.names[count.index]

  tags = {
    Name = "emyc-staging-private-app-subnet-${count.index + 1}"
  }
}

# Private Database Subnets (Isolated for RDS PostgreSQL)
resource "aws_subnet" "private_db" {
  count             = 3
  vpc_id            = aws_vpc.staging.id
  cidr_block        = cidrsubnet(aws_vpc.staging.cidr_block, 8, count.index + 20) # 10.100.20.0/24, 21.0/24, 22.0/24
  availability_zone = data.aws_availability_zones.available.names[count.index]

  tags = {
    Name = "emyc-staging-private-db-subnet-${count.index + 1}"
  }
}

# Route Tables
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.staging.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.gw.id
  }

  tags = {
    Name = "emyc-staging-public-rt"
  }
}

resource "aws_route_table_association" "public" {
  count          = 3
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.staging.id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.nat.id
  }

  tags = {
    Name = "emyc-staging-private-rt"
  }
}

resource "aws_route_table_association" "private_app" {
  count          = 3
  subnet_id      = aws_subnet.private_app[count.index].id
  route_table_id = aws_route_table.private.id
}

resource "aws_db_subnet_group" "rds" {
  name       = "emyc-staging-rds-subnet-group"
  subnet_ids = aws_subnet.private_db[*].id

  tags = {
    Name = "emyc-staging-rds-subnet-group"
  }
}

# Security Groups
resource "aws_security_group" "alb" {
  name        = "emyc-staging-alb-sg"
  description = "Security group for staging Application Load Balancer"
  vpc_id      = aws_vpc.staging.id

  ingress {
    description = "HTTP Ingress from Internet"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "HTTPS Ingress from Internet"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "Egress to App Tasks"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "emyc-staging-alb-sg"
  }
}

resource "aws_security_group" "ecs_app" {
  name        = "emyc-staging-ecs-app-sg"
  description = "Security group for ECS Fargate Application Tasks"
  vpc_id      = aws_vpc.staging.id

  ingress {
    description     = "Allow traffic from ALB on port 8000"
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    description = "Allow all outbound traffic (NAT to Telegram API, DB)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "emyc-staging-ecs-app-sg"
  }
}

resource "aws_security_group" "rds" {
  name        = "emyc-staging-rds-sg"
  description = "Security group for RDS PostgreSQL database"
  vpc_id      = aws_vpc.staging.id

  ingress {
    description     = "Allow PostgreSQL access strictly from ECS App tasks"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs_app.id]
  }

  egress {
    description = "Zero egress required for isolated database"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "emyc-staging-rds-sg"
  }
}
