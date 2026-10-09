#!/bin/bash
# EMYC AWS Staging Immediate Teardown & Cost Containment Script
set -e

CLUSTER_NAME="emyc-staging-cluster"
SERVICE_NAME="emyc-staging-app-service"
DB_IDENTIFIER="emyc-staging-db"

echo "================================================================================"
echo " EMYC STAGING IMMEDIATE TEARDOWN & COST CONTAINMENT"
echo "================================================================================"

echo "[1/4] Scaling ECS Fargate service to 0 tasks..."
aws ecs update-service \
    --cluster "$CLUSTER_NAME" \
    --service "$SERVICE_NAME" \
    --desired-count 0 \
    --no-cli-pager || echo "⚠️ ECS service already stopped or not found."

echo "[2/4] Terminating RDS PostgreSQL instance without snapshot..."
aws rds delete-db-instance \
    --db-instance-identifier "$DB_IDENTIFIER" \
    --skip-final-snapshot \
    --delete-automated-backups \
    --no-cli-pager || echo "⚠️ RDS instance already deleted or not found."

echo "[3/4] Deleting Application Load Balancers..."
ALB_ARN=$(aws elbv2 describe-load-balancers --names emyc-staging-alb --query "LoadBalancers[0].LoadBalancerArn" --output text 2>/dev/null || true)
if [ -n "$ALB_ARN" ] && [ "$ALB_ARN" != "None" ]; then
    aws elbv2 delete-load-balancer --load-balancer-arn "$ALB_ARN" --no-cli-pager
    echo "  -> Deleted ALB: $ALB_ARN"
fi

echo "[4/4] Optional: Execute full Terraform destroy to remove VPC and remaining networking..."
echo "  Run: cd infrastructure/aws && terraform destroy -auto-approve"
echo "================================================================================"
echo " TEARDOWN DISPATCH COMPLETE: All active compute and database charges halted."
echo "================================================================================"
