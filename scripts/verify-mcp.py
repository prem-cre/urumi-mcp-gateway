#!/usr/bin/env python3
"""Deploy and clean an ephemeral verification Job with the future gateway identity."""

import json
import subprocess
import sys
from pathlib import Path

if len(sys.argv) != 2 or not sys.argv[1].isdigit():
    raise SystemExit(
        "Usage: verify-mcp.py DUMMY_COD_ORDER_ID (creates product, changes/restores fixture status)"
    )
root = Path(__file__).resolve().parents[1]
name = "urumi-mcp-verification"
image = subprocess.check_output(
    [
        "kubectl",
        "-n",
        "urumi",
        "get",
        "deployment",
        "urumi-woocommerce-mcp",
        "-o",
        "jsonpath={.spec.template.spec.containers[0].image}",
    ],
    text=True,
)
cm = {
    "apiVersion": "v1",
    "kind": "ConfigMap",
    "metadata": {"name": name, "namespace": "urumi"},
    "data": {"smoke.py": (root / "scripts/mcp-smoke.py").read_text()},
}
job = {
    "apiVersion": "batch/v1",
    "kind": "Job",
    "metadata": {"name": name, "namespace": "urumi"},
    "spec": {
        "backoffLimit": 0,
        "activeDeadlineSeconds": 180,
        "template": {
            "metadata": {"labels": {"app": "gateway", "instance": "urumi"}},
            "spec": {
                "restartPolicy": "Never",
                "automountServiceAccountToken": False,
                "securityContext": {"runAsNonRoot": True, "runAsUser": 10001},
                "containers": [
                    {
                        "name": "verify",
                        "image": image,
                        "command": ["python", "/verify/smoke.py"],
                        "env": [
                            {"name": "FIXTURE_ORDER_ID", "value": sys.argv[1]},
                            {
                                "name": "GATEWAY_TOKEN",
                                "valueFrom": {
                                    "secretKeyRef": {
                                        "name": "urumi-woocommerce-mcp",
                                        "key": "gateway-token",
                                    }
                                },
                            },
                        ],
                        "resources": {
                            "requests": {"cpu": "50m", "memory": "64Mi"},
                            "limits": {"cpu": "300m", "memory": "128Mi"},
                        },
                        "volumeMounts": [{"name": "script", "mountPath": "/verify"}],
                    }
                ],
                "volumes": [{"name": "script", "configMap": {"name": name}}],
            },
        },
    },
}
try:
    for document in (cm, job):
        subprocess.run(
            ["kubectl", "create", "-f", "-"], input=json.dumps(document), text=True, check=True
        )
    import time

    for _ in range(100):
        status = json.loads(
            subprocess.check_output(
                ["kubectl", "-n", "urumi", "get", "job", name, "-o", "json"], text=True
            )
        )["status"]
        if status.get("succeeded") or status.get("failed"):
            break
        time.sleep(2)
    subprocess.run(["kubectl", "-n", "urumi", "logs", f"job/{name}"], check=False)
    if not status.get("succeeded"):
        raise SystemExit("MCP verification failed; fixture may require status reconciliation")
finally:
    subprocess.run(
        ["kubectl", "-n", "urumi", "delete", "job,configmap", name, "--ignore-not-found"],
        check=False,
    )
