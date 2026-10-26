# Round 1 live workflow verification — 20 October 2026

Verified on the local `urumi` k3d cluster using `.local/kubeconfig`, the real WordPress/WooCommerce store, the dashboard API, and the official MCP Python client. No production customer data or payment service was used.

## User A: store, chat and order lookup

1. The chart bootstrap now sets WooCommerce site visibility to public. Before this fix, WooCommerce's default `woocommerce_coming_soon=yes` made the store homepage and product pages render a coming-soon message even though the Store API worked. After bootstrap revision 30, both options are `no`, and the home page lists products.
2. `make checkout` placed seed-product COD order **#15** (`Urumi Demo Notebook`, INR 299); `uv run python scripts/verify-store.py --admin-order 15` confirmed it in the authenticated WooCommerce admin page.
3. User A asked dashboard chat to create **Urumi Round One Demo Lamp**, INR 499, SKU `URUMI-ROUND1-LAMP-20261020`. The chat called `woocommerce__create_product` successfully and created product **#16**.
4. A Store API cart/checkout smoke flow placed order **#17** for product #16. The customer-facing storefront was then opened in Chrome; the product page showed the name, INR 499 price, SKU and Add to cart control. Adding it updated the cart.
5. Checkout was completed from the visible storefront with dummy buyer data and Cash on delivery. The confirmation page showed order **#19**, the product, INR 499 total and COD. `uv run python scripts/verify-store.py --sku URUMI-ROUND1-LAMP-20261020 --admin-order 19` confirmed the product and payment method in WooCommerce admin.
6. Dashboard chat asked “What orders came in today?” It called `woocommerce__list_orders` and returned orders #19, #17 and #15 with the configured `Asia/Kolkata` timezone, items, totals and statuses.

## User B: external MCP and cross-server calls

1. The admin invited a distinct `member` account and the member accepted the invitation. A personal API key was generated from that member's signed-in dashboard session.
2. The official MCP Python client connected to `http://gateway.localhost:8080/mcp` with only the gateway URL and `Authorization: Bearer <member-key>`. `tools/list` returned **10 permitted tools** from WooCommerce, inventory and fulfillment. The two WooCommerce write tools were hidden by member grants.
3. `woocommerce__list_orders(today=true)` returned today's orders including order #19.
4. The same external MCP session called `inventory__check_stock` for the SKU from order #19. This exercised routing from one downstream gateway endpoint to both WooCommerce and inventory.
5. As User B, a separate dashboard chat prompt asked for today's order SKUs and inventory. The tool trace showed `woocommerce__list_orders` and inventory tool calls, all successful.
6. The admin revoked both test keys issued during the walkthrough. The next external MCP initialize request returned **HTTP 401**. The existing MCP SDK session also received HTTP 401 on its next request after revocation.
7. The audit API showed User A's successful create-product/order-list calls under the admin identity and User B's WooCommerce/inventory calls under the member identity, including per-call duration and outcome. Revocation attempts were logged as gateway denials.

## Local setup change and repeatable checks

WooCommerce 11.2.1 defaults a fresh store to “coming soon”. The idempotent Helm bootstrap now disables this mode so the storefront is publicly browseable in the local demo. The change is in `charts/platform/templates/bootstrap.yaml`; `./scripts/local-up.sh` reruns the safe bootstrap without replacing PVCs or Secrets.

To reproduce checkout/admin verification with any product SKU:

```sh
make checkout
uv run python scripts/verify-store.py --admin-order ORDER_ID
uv run python scripts/verify-store.py --sku PRODUCT_SKU --admin-order ORDER_ID
```

After the original walkthrough, its test keys were revoked. On 25 October, a fresh User B demo key was issued and validated against tool discovery, today's WooCommerce orders, and inventory lookup through the same external MCP client session. The key is in the ignored, owner-only `.local/user-b-demo-key` file for the final demo. Local admin/member passwords and provider credentials remain in ignored, owner-only files and are not included here.

## Assessment status after this run

The store/chat/order journey, external client catalog/orders, cross-server calls and key revocation are runtime-verified. Prior local records cover disable filtering, dead-upstream isolation/recovery, three-source routing, roles/grants and NetworkPolicy isolation. On 25 October, the k3d cluster was resumed and checked again: all platform pods became Ready; dashboard and storefront returned HTTP 200; the authenticated dashboard reported all three upstreams connected; a read-only chat turn successfully called WooCommerce; and User B's fresh key successfully called WooCommerce and inventory with member write tools hidden. Runtime-control follow-up and environment-dependent checks are below. Demo recording and submission are the user's deliverables.

## Runtime-control follow-up

- The deployed gateway's `/metrics` endpoint returned HTTP 200 and valid Prometheus exposition from inside its pod and from a debug client attached to the already-allowed Traefik pod identity. A separate pod matching the configured Prometheus namespace/pod selectors could not connect to the gateway service in local k3d. The Helm release was restored and temporary pods/namespaces and the Traefik debug container were removed afterward. The custom scraper NetworkPolicy path still needs a working cluster-level reproduction before it is claimed as verified.
- The PostgreSQL fixed-window limiter was exercised concurrently inside the gateway image using a temporary random principal: 20 simultaneous calls at a limit of 5 produced exactly 5 allowed and 15 limited calls; the next test window was allowed. The temporary bucket row was deleted.
- Automated tests now cover OIDC state-cookie integrity, HTTPS issuer configuration, signed ID-token signature/issuer/audience/nonce/verified-email checks, and circuit breaker threshold, open state, half-open exclusion and successful close. These use a generated signing key and mocked identity provider; they do not replace a login against a configured OIDC provider or breaker recovery against a live upstream.
- The gateway was restored to the local Helm values, the inventory deployment was returned to one replica, default breaker settings were restored, and the temporary API key was deleted. No generated test credentials were printed or retained.
- Run `make test` for the gateway and WooCommerce MCP unit suite. The current suite reports 31 passing tests and one upstream Starlette/httpx deprecation warning.

The production values render successfully with the same chart, but no VPS was supplied for a live deployment. A provider registration and client secret are also required to demonstrate OIDC sign-in. These are environment prerequisites, not missing local assessment source code.
