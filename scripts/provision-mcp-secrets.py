#!/usr/bin/env python3
"""Provision a dedicated WooCommerce API user/key via WordPress, then create a Secret.

Run with the dedicated kubeconfig. Captured PHP stdout contains secrets and is never printed.
The MCP runtime does not need database access or Kubernetes API privileges.
"""

import base64
import json
import os
import secrets
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ["KUBECONFIG"] = str(ROOT / ".local" / "kubeconfig")
NAMESPACE = os.environ.get("URUMI_NAMESPACE", "urumi")
NAME = "urumi-woocommerce-mcp"
exists = subprocess.check_output(
    ["kubectl", "-n", NAMESPACE, "get", "secret", NAME, "--ignore-not-found", "-o", "name"],
    text=True,
)
if exists.strip():
    print("MCP Secret already exists; retained (use an explicit rotation procedure to replace it).")
    raise SystemExit(0)
php = r"""<?php
require '/var/www/html/wp-load.php';
global $wpdb;
$user = get_user_by('login', 'urumi-mcp-service');
if (!$user) {
  $id = wp_insert_user(['user_login'=>'urumi-mcp-service',
    'user_pass'=>wp_generate_password(64, true, true), 'role'=>'shop_manager',
    'user_email'=>'mcp-service@example.test']);
  if (is_wp_error($id)) { fwrite(STDERR, "Service user creation failed\n"); exit(1); }
} else { $id = $user->ID; }
// Provisioning can be safely rerun after a Secret creation failure.
$wpdb->delete($wpdb->prefix . 'woocommerce_api_keys', ['description'=>'urumi-mcp-service']);
$key = 'ck_' . wc_rand_hash();
$secret = 'cs_' . wc_rand_hash();
$ok = $wpdb->insert($wpdb->prefix . 'woocommerce_api_keys', [
  'user_id'=>$id, 'description'=>'urumi-mcp-service', 'permissions'=>'read_write',
  'consumer_key'=>wc_api_hash($key), 'consumer_secret'=>$secret,
  'truncated_key'=>substr($key, -7),
]);
if (!$ok) { fwrite(STDERR, "REST key provisioning failed\n"); exit(1); }
echo json_encode(['consumer-key'=>$key, 'consumer-secret'=>$secret]);
"""
result = subprocess.run(
    ["kubectl", "-n", NAMESPACE, "exec", "-i", "deployment/urumi-wordpress", "--", "php"],
    input=php,
    capture_output=True,
    text=True,
    check=True,
)
values = json.loads(result.stdout)
values["gateway-token"] = secrets.token_urlsafe(48)
document = {
    "apiVersion": "v1",
    "kind": "Secret",
    "type": "Opaque",
    "metadata": {"name": NAME, "namespace": NAMESPACE},
    "data": {k: base64.b64encode(v.encode()).decode() for k, v in values.items()},
}
subprocess.run(["kubectl", "create", "-f", "-"], input=json.dumps(document), text=True, check=True)
print("Dedicated store API credential and gateway service token provisioned; values not displayed.")
