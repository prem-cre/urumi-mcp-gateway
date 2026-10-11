#!/usr/bin/env python3
"""Create cluster Secrets without exposing generated values in process arguments."""

import base64
import json
import os
import secrets
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / ".local"
LOCAL.mkdir(mode=0o700, exist_ok=True)
CREDENTIALS = LOCAL / "credentials.json"
if CREDENTIALS.exists():
    data = json.loads(CREDENTIALS.read_text())
else:
    data = {
        "database-password": secrets.token_urlsafe(32),
        "database-root-password": secrets.token_urlsafe(32),
        "admin-user": "urumi-admin",
        "admin-password": secrets.token_urlsafe(24),
        "admin-email": "admin@example.test",
        "postgres-password": secrets.token_urlsafe(32),
    }
    fd = os.open(CREDENTIALS, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(data, handle, indent=2)
namespace = os.environ.get("URUMI_NAMESPACE", "urumi")
namespace_check = subprocess.run(
    ["kubectl", "get", "namespace", namespace, "--ignore-not-found", "-o", "name"],
    capture_output=True,
    text=True,
    check=True,
)
if not namespace_check.stdout.strip():
    subprocess.run(["kubectl", "create", "namespace", namespace], check=True)
for name, entries in [
    ("urumi-store", {k: v for k, v in data.items() if k != "postgres-password"}),
    ("urumi-gateway-db", {"password": data["postgres-password"]}),
]:
    exists = subprocess.run(
        ["kubectl", "-n", namespace, "get", "secret", name, "--ignore-not-found", "-o", "name"],
        capture_output=True,
        text=True,
        check=True,
    )
    if exists.stdout.strip():
        print(f"{name}: existing Secret retained")
        continue
    document = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": name, "namespace": namespace},
        "type": "Opaque",
        "data": {k: base64.b64encode(v.encode()).decode() for k, v in entries.items()},
    }
    subprocess.run(
        ["kubectl", "create", "-f", "-"], input=json.dumps(document), text=True, check=True
    )
print("Local admin credentials: .local/credentials.json (private, ignored by Git)")
