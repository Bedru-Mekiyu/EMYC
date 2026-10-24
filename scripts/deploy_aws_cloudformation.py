#!/usr/bin/env python3
"""
Automated AWS CloudFormation Deployment Script for EMYC Platform.
Loads environment parameters from .env and provisions/updates the
auto-scaling serverless infrastructure within the $120 monthly budget cap.
"""

import sys
import os
import time
from pathlib import Path

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")
import boto3
from botocore.exceptions import ClientError
from dotenv import dotenv_values

STACK_NAME = "emyc-production-stack"
TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "infrastructure" / "cloudformation" / "emyc-production-stack.yaml"


def get_aws_session():
    env_path = Path(__file__).resolve().parent.parent / ".env"
    config = dotenv_values(env_path)

    aws_access_key = config.get("AWS_ACCESS_KEY_ID")
    aws_secret_key = config.get("AWS_SECRET_ACCESS_KEY")
    aws_region = config.get("AWS_DEFAULT_REGION", "eu-central-1")

    if not aws_access_key or not aws_secret_key:
        print("❌ Error: AWS_ACCESS_KEY_ID or AWS_SECRET_ACCESS_KEY not found in .env")
        sys.exit(1)

    return boto3.Session(
        aws_access_key_id=aws_access_key,
        aws_secret_access_key=aws_secret_key,
        region_name=aws_region
    ), config, aws_region


def validate_and_deploy():
    session, config, region = get_aws_session()
    sts = session.client("sts")

    try:
        identity = sts.get_caller_identity()
        print(f"✅ Authenticated to AWS as:")
        print(f"   Account: {identity['Account']}")
        print(f"   Arn:     {identity['Arn']}")
        print(f"   Region:  {region}\n")
    except Exception as e:
        print(f"❌ Failed to authenticate with AWS: {e}")
        sys.exit(1)

    cfn = session.client("cloudformation")

    # Read template
    if not TEMPLATE_PATH.exists():
        print(f"❌ Template not found at: {TEMPLATE_PATH}")
        sys.exit(1)

    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        template_body = f.read()

    # Validate template with AWS
    print("🔍 Validating CloudFormation template with AWS API...")
    try:
        cfn.validate_template(TemplateBody=template_body)
        print("✅ Template syntax & schema validated successfully by AWS CloudFormation.\n")
    except ClientError as e:
        print(f"❌ CloudFormation Validation Error: {e}")
        sys.exit(1)

    # Prepare stack parameters
    alert_email = config.get("ALERT_EMAIL", "bedrumekiyu2@gmail.com")
    bot_token = config.get("TELEGRAM_BOT_TOKEN", "")
    webapp_url = config.get("WEBAPP_URL", "https://emyc-exam.pages.dev")
    admin_ids = config.get("ADMIN_TELEGRAM_IDS", "")

    parameters = [
        {"ParameterKey": "EnvironmentName", "ParameterValue": "production"},
        {"ParameterKey": "AlertEmail", "ParameterValue": alert_email},
        {"ParameterKey": "TelegramBotToken", "ParameterValue": bot_token},
        {"ParameterKey": "WebAppUrl", "ParameterValue": webapp_url},
        {"ParameterKey": "AdminTelegramIds", "ParameterValue": admin_ids},
        {"ParameterKey": "MonthlyBudgetLimit", "ParameterValue": "120"},
        {"ParameterKey": "FargateMinCapacity", "ParameterValue": "1"},
        {"ParameterKey": "FargateMaxCapacity", "ParameterValue": "32"},
        {"ParameterKey": "AuroraMinACU", "ParameterValue": "0.5"},
        {"ParameterKey": "AuroraMaxACU", "ParameterValue": "4.0"},
    ]

    capabilities = ["CAPABILITY_IAM", "CAPABILITY_NAMED_IAM"]

    # Check if stack exists
    stack_exists = False
    try:
        cfn.describe_stacks(StackName=STACK_NAME)
        stack_exists = True
        print(f"ℹ️ Stack '{STACK_NAME}' exists. Initiating UPDATE...")
    except ClientError as e:
        if "does not exist" in str(e):
            print(f"🚀 Stack '{STACK_NAME}' does not exist. Initiating CREATE...")
        else:
            print(f"❌ Error checking stack: {e}")
            sys.exit(1)

    try:
        if stack_exists:
            cfn.update_stack(
                StackName=STACK_NAME,
                TemplateBody=template_body,
                Parameters=parameters,
                Capabilities=capabilities
            )
        else:
            cfn.create_stack(
                StackName=STACK_NAME,
                TemplateBody=template_body,
                Parameters=parameters,
                Capabilities=capabilities,
                OnFailure="ROLLBACK",
                EnableTerminationProtection=False
            )
    except ClientError as e:
        if "No updates are to be performed" in str(e):
            print("ℹ️ Stack is already up-to-date. No changes needed.")
            return
        print(f"❌ Stack operation failed: {e}")
        sys.exit(1)

    print("⏳ Monitoring stack deployment events...")
    seen_events = set()
    terminal_statuses = {
        "CREATE_COMPLETE", "UPDATE_COMPLETE", "ROLLBACK_COMPLETE",
        "CREATE_FAILED", "ROLLBACK_FAILED", "UPDATE_FAILED"
    }

    while True:
        time.sleep(10)
        try:
            desc = cfn.describe_stacks(StackName=STACK_NAME)
            current_status = desc["Stacks"][0]["StackStatus"]

            events_resp = cfn.describe_stack_events(StackName=STACK_NAME)
            for event in reversed(events_resp["StackEvents"]):
                event_id = event["EventId"]
                if event_id not in seen_events:
                    seen_events.add(event_id)
                    res_type = event.get("ResourceType", "")
                    logical_id = event.get("LogicalResourceId", "")
                    status = event.get("ResourceStatus", "")
                    reason = event.get("ResourceStatusReason", "")
                    print(f"   [{status}] {res_type} - {logical_id} ({reason})")

            if current_status in terminal_statuses:
                print(f"\n🎯 Final Stack Status: {current_status}")
                if "COMPLETE" in current_status and "FAILED" not in current_status and "ROLLBACK" not in current_status:
                    print("🎉 Deployment succeeded!")
                    outputs = desc["Stacks"][0].get("Outputs", [])
                    print("\n📋 Stack Outputs:")
                    for out in outputs:
                        print(f"   • {out['OutputKey']}: {out['OutputValue']}")
                else:
                    print(f"❌ Deployment did not complete successfully.")
                break
        except Exception as e:
            print(f"Warning while fetching events: {e}")


if __name__ == "__main__":
    validate_and_deploy()
