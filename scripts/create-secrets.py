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
os.environ["KUBECONFIG"] = str(LOCAL / "kubeconfig")
if not Path(os.environ["KUBECONFIG"]).is_file():
    raise SystemExit(
        "Missing .local/kubeconfig. Start the local cluster with scripts/local-up.sh first."
    )
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
data.setdefault("gateway-admin-password", secrets.token_urlsafe(32))
data.setdefault("gateway-api-key-pepper", secrets.token_urlsafe(48))
data.setdefault("gateway-session-pepper", secrets.token_urlsafe(48))
data.setdefault(
    "gateway-upstream-encryption-key", base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
)
data.setdefault("demo-upstream-token", secrets.token_urlsafe(48))
temporary = CREDENTIALS.with_suffix(".tmp")
fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as handle:
    json.dump(data, handle, indent=2)
os.replace(temporary, CREDENTIALS)
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
    (
        "urumi-store",
        {
            k: data[k]
            for k in (
                "database-password",
                "database-root-password",
                "admin-user",
                "admin-password",
                "admin-email",
            )
        },
    ),
    ("urumi-gateway-db", {"password": data["postgres-password"]}),
    (
        "urumi-gateway-app",
        {
            "admin-password": data["gateway-admin-password"],
            "api-key-pepper": data["gateway-api-key-pepper"],
            "session-pepper": data["gateway-session-pepper"],
            "upstream-encryption-key": data["gateway-upstream-encryption-key"],
            "demo-upstream-token": data["demo-upstream-token"],
        },
    ),
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
print("Local credentials: .local/credentials.json (private, ignored by Git)")
