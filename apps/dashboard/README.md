# React dashboard

Minimal React + TypeScript + Vite frontend with ivory, white and muted green styling.

```sh
cd apps/dashboard
npm ci
npm run dev -- --port 5173
# TypeScript checks and production bundle:
npm run build
```

Views: Overview, Servers, Assistant, Team and Activity. Interactive demo actions include adding/disabling servers, expanding tools, searching, preview invitations, marking a demo key revoked, copying an example endpoint and a scripted chat response. Everything is local React state and resets on reload. The preview banner identifies sample data throughout.

No backend, authentication, real key generation, gateway calls or LLM connection is implemented here. Upstream credentials are never collected. The optional Open store link points to the existing local WooCommerce storefront. Future API integration must preserve the documented gateway and credential boundaries.
