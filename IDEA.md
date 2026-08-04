---
status: deployed
progress: 75
---

# Hench

Self-hosted personal finance dashboard. Plaid pulls bank transactions; the UI renders a monthly Sankey cash-flow chart plus a transactions table with inline category overrides. FastAPI + async SQLAlchemy + CNPG Postgres, Svelte 5 + ECharts, deployed to K3s by ArgoCD.

Running against Plaid **production** since 2026-08-03. Production keys live in OpenBao at `hench/plaid`; the chart pointed at the sandbox host until then, which made every `/link/create_link_token` return 500 on `INVALID_API_KEYS` (Plaid issues a separate secret per environment). Chase is linked and holds real data — 6 accounts, 3 credit-card liabilities, ~300 transactions. The whole vhost sits behind Traefik BasicAuth: the app has no auth of its own and the FQDN is public through Cloudflare, so until that landed anyone who knew the hostname could read the lot. Secrets come from OpenBao through the `openbao-k8s-backend` ClusterSecretStore (Kubernetes auth, no per-namespace token). `fernet_key` must never be rotated casually — it encrypts Plaid access tokens at rest, so replacing it orphans every linked Item.

Full review notes, including the reasoning behind the open items, are in `REVIEW.md`.

## Direction

The end goal is not just retrospective spending analysis. It is a forward-looking
planner: upcoming bills and due dates, current balances, predicted paydays, and a
what-if that answers "if I pay more than the minimum on this card, what am I left
with?" The Sankey stays as the backward-looking view over a closed month; the
planner is a separate forecast of dated cash events over a horizon.

That decomposes into a cash-flow forecast engine plus the data it needs:

| Input | Plaid source | State |
|-------|--------------|-------|
| Spending history | `/transactions/sync` | built |
| Current balances | `/accounts/balance/get` | built — `Account` balance columns, `GET /accounts` |
| Bills, due dates, APR, minimum payments | `/liabilities/get` | built — `Liability`, nested on `GET /accounts` |
| Paydays and subscriptions | `/transactions/recurring/get` | built — `RecurringStream`, `GET /recurring`; stays empty until ~90d of history |
| Leftover after overpaying | derived amortization | not started — the remaining piece |

Two ordering constraints matter. The products requested at Link time govern what
an Item can pull, so `liabilities` must be consented **before** any bank is
linked or every institution needs re-authenticating through update mode;
`additional_consented_products` gets that consent without restricting which
institutions Link offers or billing until the endpoint is called. And recurring
streams need transaction history Plaid does not have yet, so paydays and
subscription bills stay empty for the first few months while balances and
liabilities populate immediately on link.

## Todos

- [x] Plaid Link connect flow
- [x] Plaid Link update mode for re-auth
- [x] Cursor-based /transactions/sync with idempotent upserts on Plaid natural keys
- [x] Fernet encryption of Plaid access tokens at rest
- [x] Transactions list with filters, search and pagination
- [x] Per-transaction category overrides in their own table
- [x] Monthly Sankey cash-flow chart
- [x] Month and category selectors
- [x] Nightly sync CronJob
- [x] On-demand sync endpoint and UI button
- [x] Plaid webhook receiver for SYNC_UPDATES_AVAILABLE
- [x] Helm chart covering API, frontend, CNPG cluster, Traefik ingress and ESO
- [x] Alembic migrations in an init container
- [x] CI/CD build, push, tag bump and ArgoCD rollout
- [x] Secrets via the openbao-k8s-backend ClusterSecretStore
- [x] Confirm whether hench.nickknows.net answers off-network, since every route is unauthenticated — it did, unauthenticated, serving real Chase data over Cloudflare
- [x] Put auth in front of the API via Traefik forwardAuth, an OIDC proxy or a LAN-only ingress — Traefik BasicAuth over the whole vhost, htpasswd from OpenBao at hench/basicauth
- [x] Add NetworkPolicies so the API and database are not reachable from other namespaces — in-cluster pods could hit hench-backend:8000 directly and bypass ingress BasicAuth entirely
- [ ] Replace ingress BasicAuth with Cloudflare Access or an OIDC proxy, since basic auth has no session, no MFA and one shared credential
- [ ] Consider authenticating the API itself rather than only at the ingress, since NetworkPolicy is now the only thing preventing an in-cluster bypass
- [ ] Encrypt the CNPG volume at rest: rook-ceph-block has no encrypted:true, so transactions, balances, masks and liabilities sit in plaintext on the OSDs (Plaid access tokens are Fernet-encrypted and unaffected)
- [ ] Restore Plaid webhook delivery, which BasicAuth now blocks — needs a path exemption plus Plaid-Verification JWT checking, so sync is nightly-only until then
- [x] Fix the UI not re-rendering after a sync or a bank link: App.svelte reloads only months and items, while Sankey and Transactions re-fetch solely on a month prop change
- [ ] Verify the Plaid-Verification JWT on the webhook, which is currently unchecked
- [x] Fix sync_all error handling, which leaves the session in pending-rollback so one bad Item fails the rest
- [ ] Make category filtering honour overrides instead of matching only category_primary and pfc_primary
- [ ] Add a way to create rules, since the Rule table and categorize() exist but nothing writes to them
- [ ] Add a recategorize backfill CLI, because rules currently apply only at sync time
- [ ] Add a ScheduledBackup for CNPG, since spec.backup is set but nothing triggers a snapshot
- [ ] Add backend tests for categorize, build_sankey, _asyncpg_url and sync upsert idempotency
- [ ] Run tests and lint in CI ahead of the image build
- [x] Request Plaid production access and write a production secret to hench/plaid
- [x] Point PLAID_ENV at production, since production keys against the sandbox host return INVALID_API_KEYS
- [ ] Register https://hench.nickknows.net as an allowed redirect URI in the Plaid dashboard
- [ ] Set PLAID_REDIRECT_URI in the chart, without which OAuth banks will not link, but only after it is registered — an unregistered value fails with INVALID_FIELD
- [x] Consent to the liabilities product via additional_consented_products before linking any bank, so no institution needs re-auth later
- [x] Add balance columns to Account and populate them from /accounts/balance/get during sync
- [x] Add a Liability model and populate it from /liabilities/get, tolerating institutions that do not support the product
- [x] Add a RecurringStream model and populate it from /transactions/recurring/get, tolerating PRODUCT_NOT_READY until history accrues
- [ ] Add a /forecast endpoint projecting dated cash events and a running balance to a horizon
- [ ] Add an extra-payment what-if over the forecast, returning payoff date, interest saved and cash left over
- [ ] Add an allocation view for splitting income across bills, debt and savings
- [x] Surface the Plaid error_code and error_message on ApiException instead of an unhandled 500, which made INVALID_API_KEYS opaque to the UI
- [ ] Add an unlink endpoint and UI, as Items cannot be removed once linked
- [x] Add an accounts endpoint, since /transactions takes account_id but nothing lists accounts
- [ ] Break out income sources in the Sankey, where Income is a stub node with no inbound links
- [ ] Fix the Mapped[float] annotation on Transaction.amount, which is Numeric(14,2) and already yields Decimal at runtime — the lie is the annotation, not the storage
- [ ] Decide whether money crosses the API as a string rather than a float, since TransactionOut.amount and SankeyResponse coerce Decimal to float at the Pydantic boundary; harmless for a Sankey, lossy for amortization done client-side
- [ ] Surface or drop pfc_confidence, which is stored and never used
- [ ] Handle the Plaid Link CDN script failing to load, where undefined window.Plaid throws a raw TypeError
- [ ] Replace $effect with onMount in App.svelte and surface loadMeta errors
- [ ] Promote _month_bounds out of services.sankey, imported privately by routers.transactions
- [ ] Fix the README pointer to deploy/argocd/hench.yaml, which lives in argo-k8s-stuff at personal/templates/hench.yaml
- [ ] Drop the non-existent deploy/** path from build-deploy.yml paths-ignore
- [ ] Update the README ESO section for the openbao-k8s-backend ClusterSecretStore
