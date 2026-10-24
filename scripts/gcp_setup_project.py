#!/usr/bin/env python3
"""
Enable necessary Google Cloud APIs for EMYC Platform deployment.
Project: emyc-production (377271595555)
"""

import sys
import time
from google.oauth2 import service_account
from googleapiclient.discovery import build

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

KEY_PATH = "gcp-key.json"
PROJECT_ID = "gen-lang-client-0319906522"

APIS_TO_ENABLE = [
    "cloudbilling.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "run.googleapis.com",
    "cloudbuild.googleapis.com",
    "artifactregistry.googleapis.com",
    "secretmanager.googleapis.com",
]

def main():
    credentials = service_account.Credentials.from_service_account_file(
        KEY_PATH,
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    print(f"Authenticated as: {credentials.service_account_email}")

    serviceusage = build("serviceusage", "v1", credentials=credentials)

    print("\nEnabling required Google Cloud APIs...")
    for api in APIS_TO_ENABLE:
        try:
            print(f"-> Enabling {api}...", end=" ", flush=True)
            op = serviceusage.services().enable(
                name=f"projects/{PROJECT_ID}/services/{api}"
            ).execute()
            print("OK")
        except Exception as e:
            print(f"FAILED: {e}")

    # Check billing info
    print("\nChecking Billing Status...")
    try:
        cloudbilling = build("cloudbilling", "v1", credentials=credentials)
        info = cloudbilling.projects().getBillingInfo(name=f"projects/{PROJECT_ID}").execute()
        billing_enabled = info.get("billingEnabled", False)
        billing_account = info.get("billingAccountName", "None")
        print(f"Billing Enabled: {billing_enabled}")
        print(f"Billing Account: {billing_account}")
    except Exception as e:
        print(f"Billing check error: {e}")

if __name__ == "__main__":
    main()
