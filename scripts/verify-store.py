#!/usr/bin/env python3
"""Exercise real visitor cart/checkout. Explicit --checkout creates one COD demo order."""

import argparse
import json
from pathlib import Path

import httpx

parser = argparse.ArgumentParser()
parser.add_argument("--checkout", action="store_true", help="Place an actual COD demo order")
parser.add_argument("--admin-order", type=int, help="Confirm a stored order in the admin page")
args = parser.parse_args()
with httpx.Client(
    base_url="http://127.0.0.1:8080",
    headers={"Host": "store.localhost:8080"},
    timeout=60,
    trust_env=False,
) as client:
    response = client.get("/")
    response.raise_for_status()
    assert "Urumi Demo Store" in response.text, "Store title missing"
    products = client.get("/wp-json/wc/store/v1/products", params={"sku": "URUMI-DEMO-001"})
    products.raise_for_status()
    product = products.json()[0]
    assert product["is_purchasable"], "Seed product is not purchasable"
    print(f"Storefront OK; product {product['id']}: {product['name']}")
    cart = client.get("/wp-json/wc/store/v1/cart")
    cart.raise_for_status()
    token = cart.headers["Cart-Token"]
    client.headers["Cart-Token"] = token
    added = client.post(
        "/wp-json/wc/store/v1/cart/add-item", json={"id": product["id"], "quantity": 1}
    )
    added.raise_for_status()
    assert added.json()["items"][0]["quantity"] == 1
    print("Visitor cart: product added successfully")
    if args.checkout:
        address = {
            "first_name": "Demo",
            "last_name": "Buyer",
            "company": "",
            "address_1": "1 Assessment Street",
            "address_2": "",
            "city": "Bangalore",
            "state": "KA",
            "postcode": "560001",
            "country": "IN",
            "phone": "9000000000",
        }
        billing = {**address, "email": "buyer@example.test"}
        updated = client.post(
            "/wp-json/wc/store/v1/cart/update-customer",
            json={
                "billing_address": billing,
                "shipping_address": address,
            },
        )
        updated.raise_for_status()
        checkout = client.post(
            "/wp-json/wc/store/v1/checkout",
            json={
                "billing_address": billing,
                "shipping_address": address,
                "payment_method": "cod",
                "payment_data": [],
                "customer_note": "Foundation verification: real visitor checkout, no payment.",
            },
        )
        if checkout.is_error:
            print(json.dumps(checkout.json(), indent=2))
        checkout.raise_for_status()
        order = checkout.json()
        assert order["payment_method"] == "cod"
        assert order["order_id"] > 0
        print(f"COD order placed: #{order['order_id']}, status={order['status']}")

    if args.admin_order:
        credentials = json.loads(
            (Path(__file__).resolve().parents[1] / ".local/credentials.json").read_text()
        )
        client.get("/wp-login.php").raise_for_status()
        logged_in = client.post(
            "/wp-login.php",
            data={
                "log": credentials["admin-user"],
                "pwd": credentials["admin-password"],
                "wp-submit": "Log In",
                "testcookie": "1",
                "redirect_to": "http://store.localhost:8080/wp-admin/",
            },
        )
        assert logged_in.status_code == 302, "Admin login did not redirect"
        assert any("wordpress_logged_in" in key for key in client.cookies), "Admin login failed"
        admin = client.get(
            "/wp-admin/admin.php",
            params={
                "page": "wc-orders",
                "action": "edit",
                "id": args.admin_order,
            },
        )
        # WooCommerce may redirect to the legacy order editor when HPOS is disabled.
        for _ in range(4):
            if admin.status_code not in (301, 302):
                break
            target = httpx.URL(admin.headers["location"])
            assert target.host == "store.localhost", "Unexpected admin redirect host"
            admin = client.get(target.raw_path.decode())
        admin.raise_for_status()
        assert product["name"] in admin.text, "Order product missing from WooCommerce admin"
        assert "Cash on delivery" in admin.text, "COD method missing from admin order"
        print(f"WooCommerce admin confirms order #{args.admin_order}, its product and COD method")
