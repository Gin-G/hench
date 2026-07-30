---
status: deployed
progress: 70
---

# Hench

Self-hosted personal finance dashboard. Plaid pulls bank transactions; the UI
renders a monthly Sankey cash-flow chart plus a transactions table with inline
category overrides. FastAPI + async SQLAlchemy + CNPG Postgres, Svelte 5 +
ECharts, deployed to K3s by ArgoCD.

Last reviewed: 2026-07-30.

## Where it stands

The whole path works end to end in **sandbox**: images build and deploy on push
to `main`, migrations run in an init container, secrets flow from OpenBao, the
API serves the SPA behind Traefik, and the nightly sync CronJob is scheduled.

What has *not* happened yet: no real bank has been linked, and nothing has run
against Plaid production.

### Built and deployed

| Area | State |
|---|---|
| Plaid Link (connect + update-mode re-auth) | Done |
| `/transactions/sync` cursor sync, idempotent upserts | Done |
| Access tokens encrypted at rest (Fernet) | Done |
| Transactions list w/ filters, search, pagination | Done |
| Per-transaction category overrides | Done |
| Monthly Sankey + month/category selectors | Done |
| Nightly sync CronJob + on-demand sync + webhook receiver | Done |
| Helm chart: API, frontend, CNPG, ingress, ESO | Done |
| CI/CD: build → push → tag bump → ArgoCD | Done |
| Secrets via OpenBao ClusterSecretStore | Done (2026-07-30) |

### Secrets

Reads through `openbao-k8s-backend`, the cluster-wide `ClusterSecretStore` in
`Gin-G/argo-k8s-stuff` that authenticates with OpenBao's Kubernetes auth
method. No per-namespace token secret is needed. OpenBao kv path `hench/plaid`
holds `client_id`, `secret`, `fernet_key`.

The chart can still render its own namespaced token-based store via
`secrets.store.create: true`, for the case where hench needs a narrower OpenBao
policy than the shared read-only one.

**`fernet_key` must never be rotated casually.** It encrypts Plaid access
tokens in Postgres; replacing it orphans every linked Item and forces a
re-link through Plaid Link.

## Remaining work

### 1. The API is unauthenticated and appears to be internet-facing

The single largest open item. `main.py` and the README both state auth is
"network-level only (single-user homelab)", but the chart publishes
`hench.nickknows.net` through Traefik with an external-dns target of a routable
address and a Let's Encrypt cert. If that host resolves and answers from the
public internet, then so does all of this, with no credential:

- `GET /api/transactions` — every transaction, amount, merchant and account
- `POST /api/sync` — anyone can trigger Plaid syncs
- `POST /api/webhook` — unverified; can flag Items `login_required`
- `POST /api/link/create_link_token` — mints Plaid Link tokens

**First step is to confirm the exposure** — curl the FQDN from off-network. If
it answers, pick one: a Traefik `forwardAuth`/basic-auth middleware, an OIDC
proxy, or restricting the ingress to the LAN and reaching it over Tailscale.
`main.py` is already structured so middleware can be added without touching
routers.

Note the webhook endpoint has to stay publicly reachable if Plaid is to deliver
`SYNC_UPDATES_AVAILABLE`, so it needs signature verification rather than
network restriction — see below.

### 2. Plaid webhook JWT verification is not implemented

`routers/sync.py` flags this in a comment. The `Plaid-Verification` header is
never checked, so any POST with a known `item_id` can trigger a sync or mark an
Item as needing re-auth. Required before trusting the endpoint on a public
address.

### 3. `sync_all` cannot actually survive a failing Item

```python
except Exception:  # noqa: BLE001 - one bad item shouldn't stop the rest
```

The intent doesn't hold: the exception leaves the `AsyncSession` in a
pending-rollback state, so every subsequent `sync_item` on that session fails
too. One bad Item takes down the whole nightly run. Fix by rolling back (or
using a fresh session) per Item.

### 4. Category filtering ignores overrides

`GET /transactions?category=` is documented as "effective primary category" but
matches only `category_primary` / `pfc_primary`. A transaction whose category
comes from a `CategoryOverride` won't match its own displayed category. Needs
an outer join to `category_overrides` in the filter.

### 5. The rules engine has no way to create rules

`Rule`, `load_rules()` and `categorize()` are implemented and wired into sync,
but nothing writes to the `rules` table — no router, no CLI subcommand, no
seed. The feature is unreachable without hand-written SQL.

Related: rules are applied **only at sync time**, so adding a rule doesn't
recategorize existing rows. Wants a `python -m app.cli recategorize` backfill
alongside whatever creates rules.

### 6. CNPG backups are configured but never taken

`cnpg-cluster.yaml` sets `spec.backup.volumeSnapshot` with a retention policy,
but there's no `ScheduledBackup` resource and no manual `Backup`, so nothing
triggers one. Add a `ScheduledBackup` to the chart, then verify a snapshot
actually lands.

### 7. No tests, no lint

Nothing under `backend/tests/`, no pytest dependency, no frontend test setup,
and `build-deploy.yml` goes straight to building images. The highest-value
targets, roughly in order:

- `services/rules.categorize` — pure function, trivial to cover
- `services/sankey.build_sankey` — sign conventions, transfer exclusion,
  leftover/savings behaviour
- `db._asyncpg_url` — URL rewriting
- sync upsert idempotency against a throwaway Postgres

### 8. Production Plaid readiness

- `PLAID_ENV` is `sandbox`; production needs a Plaid production access request
  and a separate secret written to `hench/plaid`
- `PLAID_REDIRECT_URI` is supported by `config.py` but never set by the chart.
  OAuth institutions (most large US banks) will not link without a registered
  redirect URI — this blocks real-bank linking, not just polish
- Sandbox credentials produce fake data only; nothing has exercised the real
  `/transactions/sync` pagination path at volume

## Smaller items

- **No unlink.** Once an Item is linked there's no endpoint or UI to remove it.
- **No accounts endpoint.** `GET /transactions` accepts `account_id`, but
  nothing lists accounts, so the filter isn't reachable from the UI.
- **Sankey income is a stub node.** `Income` has no inbound links — income
  sources aren't broken out by category. If spending exceeds income the
  `Savings / Unspent` link disappears and the chart shows more leaving `Income`
  than entering it.
- **Money is handled as `float`.** `Numeric(14,2)` is cast to float on the way
  out and rounded at 2dp. Fine at homelab scale; worth revisiting before
  trusting totals.
- **`pfc_confidence` is stored and never surfaced.** Either show it (to flag
  low-confidence categorisations worth overriding) or drop the column.
- **Plaid Link JS is a CDN `<script>`** in `index.html`. If it fails to load,
  `window.Plaid` is undefined and the connect button throws a raw TypeError.
- **`App.svelte` uses `$effect` for initial load** where `onMount` is the
  intent, and `loadMeta()` failures are unhandled — the header silently stays
  empty. `Transactions.svelte` does surface errors.
- **`routers/transactions.py` imports `_month_bounds`** from
  `services.sankey` — a private name across module boundaries. Promote it.

## Doc drift to fix

- README points to `deploy/argocd/hench.yaml` for the ArgoCD Application. That
  path doesn't exist; the Application actually lives in
  `Gin-G/argo-k8s-stuff` at `personal/templates/hench.yaml`, gated on
  `hench.enable` in `personal/values.yaml`.
- `build-deploy.yml` has `paths-ignore: deploy/**`, referring to the same
  non-existent directory.
- README's ESO section predates the ClusterSecretStore switch and should
  mention `openbao-k8s-backend`.

## Decisions worth remembering

- Plaid natural keys (`item_id`, `account_id`, `transaction_id`) are the
  primary keys, which is what makes re-running a sync idempotent.
- Plaid's sign convention is kept as-is: **positive = money out**, negative =
  money in. `services/sankey.py` depends on this.
- Overrides live in their own table rather than a column, so a sync can
  refresh every synced field without clobbering manual corrections.
- The API serves routes at root; `/api` is stripped by the Traefik
  `strip-api` middleware in-cluster and by the Vite proxy locally. Changing one
  means changing the other.
- CI commits image-tag bumps back to `main` with `[skip ci]`, and
  `paths-ignore` keeps chart-only pushes from rebuilding images.
