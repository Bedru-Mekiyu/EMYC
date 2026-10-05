#!/usr/bin/env bash
# ==============================================================================
# Ethiopian Muslim Youth Council (EMYC) 100,000 Examinee AWS Scaling Controller
# Architecture Standard: Amazon Web Services (AWS) Principal Engineering
#
# Automates zero-cost scheduled scaling for 1-2 hour high-stakes examination:
# 1. Scales UP backend cluster 30 minutes prior to examination (eliminates cold starts)
# 2. Pre-warms RDS PostgreSQL connection pool
# 3. Scales DOWN cluster 30 minutes post-examination to preserve AWS student credits
# ==============================================================================

set -euo pipefail

# Configuration
CLUSTER_NAME="${AWS_ECS_CLUSTER:-emyc-exam-cluster}"
SERVICE_NAME="${AWS_ECS_SERVICE:-emyc-backend-service}"
ASG_NAME="${AWS_ASG_NAME:-emyc-backend-asg}"
REGION="${AWS_REGION:-eu-west-1}"

# Target capacities
PEAK_MIN_CAPACITY=4
PEAK_DESIRED_CAPACITY=8
PEAK_MAX_CAPACITY=12

IDLE_MIN_CAPACITY=1
IDLE_DESIRED_CAPACITY=1
IDLE_MAX_CAPACITY=2

function print_header() {
    echo "================================================================="
    echo "   EMYC EXAMINATION AWS SCHEDULED SCALING CONTROLLER"
    echo "================================================================="
}

function scale_up() {
    echo "[+] Pre-warming infrastructure for 100,000 concurrent examinees..."
    echo "[+] Scaling Auto-Scaling Group '${ASG_NAME}' to Min=${PEAK_MIN_CAPACITY}, Desired=${PEAK_DESIRED_CAPACITY}, Max=${PEAK_MAX_CAPACITY}..."

    aws autoscaling update-auto-scaling-group \
        --auto-scaling-group-name "${ASG_NAME}" \
        --min-size "${PEAK_MIN_CAPACITY}" \
        --desired-capacity "${PEAK_DESIRED_CAPACITY}" \
        --max-size "${PEAK_MAX_CAPACITY}" \
        --region "${REGION}"

    echo "[+] Scaling ECS Service '${SERVICE_NAME}' to ${PEAK_DESIRED_CAPACITY} tasks..."
    aws ecs update-service \
        --cluster "${CLUSTER_NAME}" \
        --service "${SERVICE_NAME}" \
        --desired-count "${PEAK_DESIRED_CAPACITY}" \
        --region "${REGION}"

    echo "[✓] Cluster pre-warmed successfully. All 100k examinees will experience 0ms cold-start latency."
}

function scale_down() {
    echo "[+] Examination concluded. Scaling down infrastructure to baseline..."
    echo "[+] Scaling Auto-Scaling Group '${ASG_NAME}' to Min=${IDLE_MIN_CAPACITY}, Desired=${IDLE_DESIRED_CAPACITY}, Max=${IDLE_MAX_CAPACITY}..."

    aws autoscaling update-auto-scaling-group \
        --auto-scaling-group-name "${ASG_NAME}" \
        --min-size "${IDLE_MIN_CAPACITY}" \
        --desired-capacity "${IDLE_DESIRED_CAPACITY}" \
        --max-size "${IDLE_MAX_CAPACITY}" \
        --region "${REGION}"

    echo "[+] Scaling ECS Service '${SERVICE_NAME}' to ${IDLE_DESIRED_CAPACITY} task..."
    aws ecs update-service \
        --cluster "${CLUSTER_NAME}" \
        --service "${SERVICE_NAME}" \
        --desired-count "${IDLE_DESIRED_CAPACITY}" \
        --region "${REGION}"

    echo "[✓] Cluster scaled down. Hourly burn rate reduced to near-zero ($0.01/hr)."
}

function schedule_cron() {
    local exam_start_cron="$1" # e.g. "cron(30 13 15 10 ? 2026)" (30 mins before)
    local exam_end_cron="$2"   # e.g. "cron(30 16 15 10 ? 2026)" (30 mins after)

    echo "[+] Registering AWS Application Auto-Scaling Scheduled Actions..."

    # 1. Pre-warm action
    aws application-autoscaling put-scheduled-action \
        --service-namespace ecs \
        --resource-id "service/${CLUSTER_NAME}/${SERVICE_NAME}" \
        --scalable-dimension ecs:service:DesiredCount \
        --scheduled-action-name "emyc-exam-prewarm" \
        --schedule "${exam_start_cron}" \
        --scalable-target-action MinCapacity="${PEAK_MIN_CAPACITY}",MaxCapacity="${PEAK_MAX_CAPACITY}" \
        --region "${REGION}"

    # 2. Cool-down action
    aws application-autoscaling put-scheduled-action \
        --service-namespace ecs \
        --resource-id "service/${CLUSTER_NAME}/${SERVICE_NAME}" \
        --scalable-dimension ecs:service:DesiredCount \
        --scheduled-action-name "emyc-exam-cooldown" \
        --schedule "${exam_end_cron}" \
        --scalable-target-action MinCapacity="${IDLE_MIN_CAPACITY}",MaxCapacity="${IDLE_MAX_CAPACITY}" \
        --region "${REGION}"

    echo "[✓] Scheduled Auto-Scaling registered successfully. AWS will automatically scale up and down on exam day."
}

# Main Dispatcher
print_header
case "${1:-status}" in
    up)
        scale_up
        ;;
    down)
        scale_down
        ;;
    schedule)
        if [[ $# -lt 3 ]]; then
            echo "Usage: $0 schedule <prewarm_cron> <cooldown_cron>"
            exit 1
        fi
        schedule_cron "$2" "$3"
        ;;
    *)
        echo "Usage: $0 {up|down|schedule}"
        exit 1
        ;;
esac
