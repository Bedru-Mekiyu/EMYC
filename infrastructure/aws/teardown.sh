#!/bin/bash
# EMYC AWS Staging Immediate Teardown & Cost Containment Script
set -e

CLUSTER_NAME="emyc-staging-cluster"
SERVICE_NAME="emyc-staging-app-service"
DB_IDENTIFIER="emyc-staging-db"

echo "================================================================================"
echo " EMYC STAGING IMMEDIATE TEARDOWN & COST CONTAINMENT"
echo "================================================================================"

echo "[1/6] Scaling ECS Fargate application and loadgen services to 0 tasks..."
aws ecs update-service \
    --cluster "$CLUSTER_NAME" \
    --service "$SERVICE_NAME" \
    --desired-count 0 \
    --no-cli-pager 2>/dev/null || echo "ℹ️ ECS app service already stopped or not found."

aws ecs update-service \
    --cluster "$CLUSTER_NAME" \
    --service "emyc-staging-loadgen-service" \
    --desired-count 0 \
    --no-cli-pager 2>/dev/null || true

echo "[2/6] Terminating RDS PostgreSQL instance without snapshot..."
aws rds delete-db-instance \
    --db-instance-identifier "$DB_IDENTIFIER" \
    --skip-final-snapshot \
    --delete-automated-backups \
    --no-cli-pager 2>/dev/null || echo "ℹ️ RDS instance already deleted or not found."

echo "[3/6] Deleting Application Load Balancers and Target Groups..."
ALB_ARN=$(aws elbv2 describe-load-balancers --names emyc-staging-alb --query "LoadBalancers[0].LoadBalancerArn" --output text 2>/dev/null || true)
if [ -n "$ALB_ARN" ] && [ "$ALB_ARN" != "None" ]; then
    aws elbv2 delete-load-balancer --load-balancer-arn "$ALB_ARN" --no-cli-pager
    echo "  -> Deleted ALB: $ALB_ARN"
fi

TG_ARN=$(aws elbv2 describe-target-groups --names emyc-staging-app-tg --query "TargetGroups[0].TargetGroupArn" --output text 2>/dev/null || true)
if [ -n "$TG_ARN" ] && [ "$TG_ARN" != "None" ]; then
    aws elbv2 delete-target-group --target-group-arn "$TG_ARN" --no-cli-pager 2>/dev/null || true
    echo "  -> Deleted Target Group: $TG_ARN"
fi

echo "[4/6] Deleting NAT Gateway and Releasing Elastic IP (CRITICAL COST CONTAINMENT)..."
NAT_GW_ID=$(aws ec2 describe-nat-gateways --filter "Name=tag:Name,Values=emyc-staging-nat-gw" "Name=state,Values=available,pending" --query "NatGateways[0].NatGatewayId" --output text 2>/dev/null || true)
if [ -n "$NAT_GW_ID" ] && [ "$NAT_GW_ID" != "None" ]; then
    echo "  -> Deleting NAT Gateway: $NAT_GW_ID..."
    aws ec2 delete-nat-gateway --nat-gateway-id "$NAT_GW_ID" --no-cli-pager
    echo "  -> Waiting for NAT Gateway to delete before releasing Elastic IP..."
    aws ec2 wait nat-gateway-deleted --nat-gateway-ids "$NAT_GW_ID" 2>/dev/null || true
fi

EIP_ALLOC_ID=$(aws ec2 describe-addresses --filters "Name=tag:Name,Values=emyc-staging-nat-eip" --query "Addresses[0].AllocationId" --output text 2>/dev/null || true)
if [ -n "$EIP_ALLOC_ID" ] && [ "$EIP_ALLOC_ID" != "None" ]; then
    echo "  -> Releasing Elastic IP: $EIP_ALLOC_ID..."
    aws ec2 release-address --allocation-id "$EIP_ALLOC_ID" --no-cli-pager 2>/dev/null || true
fi

echo "[5/6] Setting CloudWatch log group retention to 1 day to contain log storage..."
aws logs put-retention-policy --log-group-name "/ecs/emyc-staging-app" --retention-in-days 1 2>/dev/null || true

echo "[6/6] Full Infrastructure Purge via Terraform Destroy..."
echo "  Run: cd infrastructure/aws && terraform destroy -auto-approve"

echo "================================================================================"
echo " POST-TEARDOWN AUDIT VERIFICATION (PROVE ZERO BILLABLE RESOURCES REMAIN)"
echo "================================================================================"
echo "Run these inspection commands to verify zero billable resources remain:"
echo "  1. NAT Gateways (must be empty/deleted):"
echo "     aws ec2 describe-nat-gateways --filter \"Name=state,Values=available,pending\" --query \"NatGateways[*].[NatGatewayId,State]\" --output table"
echo "  2. Elastic IPs (must be empty):"
echo "     aws ec2 describe-addresses --query \"Addresses[*].[PublicIp,AllocationId]\" --output table"
echo "  3. RDS Instances (must be empty/deleting):"
echo "     aws rds describe-db-instances --query \"DBInstances[*].[DBInstanceIdentifier,DBInstanceStatus]\" --output table"
echo "  4. Application Load Balancers (must be empty):"
echo "     aws elbv2 describe-load-balancers --query \"LoadBalancers[*].[LoadBalancerName,State.Code]\" --output table"
echo "  5. Running ECS Tasks (must be empty):"
echo "     aws ecs list-tasks --cluster emyc-staging-cluster --desired-status RUNNING --output table"
echo "================================================================================"
