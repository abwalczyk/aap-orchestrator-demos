#!/usr/bin/env python3
"""Test connections to AO and MLflow."""

import os
from dotenv import load_dotenv
import mlflow
import requests

load_dotenv()

# Test MLflow connection
print("Testing MLflow connection...")
mlflow_uri = os.getenv("MLFLOW_TRACKING_URI")
print(f"MLflow URI: {mlflow_uri}")

try:
    mlflow.set_tracking_uri(mlflow_uri)
    # Try to list experiments
    experiments = mlflow.search_experiments()
    print(f"✅ Connected to MLflow! Found {len(experiments)} experiments")
    for exp in experiments[:3]:
        print(f"  - {exp.name}")
except Exception as e:
    print(f"❌ MLflow connection failed: {e}")

# Test AO connection
print("\nTesting AO connection...")
ao_base_url = os.getenv("AO_BASE_URL")
ao_username = os.getenv("AO_USERNAME")
ao_password = os.getenv("AO_PASSWORD")
print(f"AO URL: {ao_base_url}")

try:
    # Try to login
    response = requests.post(
        f"{ao_base_url}/api/v1/auth/login",
        json={"username": ao_username, "password": ao_password},
        timeout=10
    )
    if response.ok:
        print(f"✅ Connected to AO! Logged in as {ao_username}")
        token = response.json().get("access_token")

        # Try to list workflows
        headers = {"Authorization": f"Bearer {token}"}
        workflows_response = requests.get(
            f"{ao_base_url}/api/v1/workflows",
            headers=headers,
            timeout=10
        )
        if workflows_response.ok:
            workflows = workflows_response.json()
            count = len(workflows) if isinstance(workflows, list) else workflows.get('count', 0)
            print(f"  Found {count} workflows")
    else:
        print(f"❌ AO login failed: {response.status_code} {response.text}")
except Exception as e:
    print(f"❌ AO connection failed: {e}")

print("\n✅ Setup complete! You can now run the harness.")
print("\nExample commands:")
print("  cd harness")
print("  source ../venv/bin/activate")
print("  python3 run.py model-sweep --workflow ticket_classifier --step classify_bug --iterations 1 --skip-judges")
