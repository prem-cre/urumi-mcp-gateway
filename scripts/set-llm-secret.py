#!/usr/bin/env python3
"""Prompt for an LLM provider key and store it in the local Kubernetes Secret."""

import base64
import getpass
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ["KUBECONFIG"] = str(ROOT / ".local" / "kubeconfig")
if not Path(os.environ["KUBECONFIG"]).is_file():
    raise SystemExit("Missing .local/kubeconfig; run scripts/local-up.sh first.")

credential = getpass.getpass("LLM provider API key (input hidden): ").strip()
if not credential:
    raise SystemExit("No key entered; the Kubernetes Secret was not changed.")

try:
    result = subprocess.run(
        ["kubectl", "-n", "urumi", "get", "secret", "urumi-gateway-app", "-o", "json"],
        capture_output=True,
        text=True,
        check=True,
    )
    secret = json.loads(result.stdout)
except (subprocess.CalledProcessError, json.JSONDecodeError) as exc:
    raise SystemExit("Could not read urumi-gateway-app; deploy the local gateway first.") from exc

data = secret.get("data", {})
data["llm-api-key"] = base64.b64encode(credential.encode()).decode()
secret = {
    "apiVersion": "v1",
    "kind": "Secret",
    "metadata": {"name": "urumi-gateway-app", "namespace": "urumi"},
    "type": "Opaque",
    "data": data,
}
try:
    subprocess.run(
        ["kubectl", "apply", "-f", "-"],
        input=json.dumps(secret),
        capture_output=True,
        text=True,
        check=True,
    )
except subprocess.CalledProcessError as exc:
    raise SystemExit("Could not update the LLM key in Kubernetes.") from exc
print(
    "LLM provider key stored in urumi/urumi-gateway-app; the key was not printed or saved to disk."
)
