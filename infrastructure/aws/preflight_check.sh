#!/bin/bash
# EMYC AWS Staging Preflight Environmental Check Script
# Validates local dependencies, CLI availability, and AWS credentials WITHOUT deploying resources.

set -e

echo "================================================================================"
echo " EMYC STAGING ENVIRONMENT PREFLIGHT VERIFICATION"
echo "================================================================================"

FAILED=0

# 1. Check Terraform CLI
echo -n "[1/4] Checking Terraform CLI... "
if command -v terraform >/dev/null 2>&1; then
    TF_VER=$(terraform version | head -n 1)
    echo "OK ($TF_VER)"
else
    echo "FAILED: terraform is not installed or not in PATH."
    FAILED=1
fi

# 2. Check AWS CLI
echo -n "[2/4] Checking AWS CLI... "
if command -v aws >/dev/null 2>&1; then
    AWS_VER=$(aws --version 2>&1 | head -n 1)
    echo "OK ($AWS_VER)"
else
    echo "FAILED: aws CLI is not installed or not in PATH."
    FAILED=1
fi

# 3. Check Docker CLI
echo -n "[3/4] Checking Docker CLI... "
if command -v docker >/dev/null 2>&1; then
    DOCKER_VER=$(docker --version)
    echo "OK ($DOCKER_VER)"
else
    echo "FAILED: docker is not installed or not in PATH."
    FAILED=1
fi

# 4. Check AWS Authentication & Region (Only if AWS CLI present)
if command -v aws >/dev/null 2>&1; then
    echo -n "[4/4] Checking AWS Authentication via STS... "
    if CALLER=$(aws sts get-caller-identity --output json 2>/dev/null); then
        ACCOUNT=$(echo "$CALLER" | grep -o '"Account": "[^"]*' | cut -d'"' -f4)
        ARN=$(echo "$CALLER" | grep -o '"Arn": "[^"]*' | cut -d'"' -f4)
        echo "OK (Account: $ACCOUNT, Identity: $ARN)"
    else
        echo "FAILED: Unable to authenticate to AWS. Check AWS_ACCESS_KEY_ID / AWS_PROFILE."
        FAILED=1
    fi
else
    echo "[4/4] Skipping AWS STS check (AWS CLI missing)."
fi

echo "================================================================================"
if [ "$FAILED" -eq 0 ]; then
    echo " PREFLIGHT PASSED: All local tools and credentials are ready."
    echo "================================================================================"
    exit 0
else
    echo " PREFLIGHT FAILED: Please resolve the missing dependencies before proceeding."
    echo "================================================================================"
    exit 1
fi
