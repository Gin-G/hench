---
status: deployed
progress: 70
---

# Hench

Self-hosted personal finance dashboard. Plaid pulls bank transactions; the UI renders a monthly Sankey cash-flow chart plus a transactions table with inline category overrides. FastAPI + async SQLAlchemy + CNPG Postgres, Svelte 5 + ECharts, deployed to K3s by ArgoCD.

Working end to end in Plaid **sandbox**. No real bank linked yet, nothing run against Plaid production. Secrets come from OpenBao through the `openbao-k8s-backend` ClusterSecretStore (Kubernetes auth, no per-namespace token). `fernet_key` must never be rotated casually — it encrypts Plaid access tokens at rest, so replacing it orphans every linked Item.

Full review notes, including the reasoning behind the open items, are in `REVIEW.md`.

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
- [ ] Confirm whether hench.nickknows.net answers off-network, since every route is unauthenticated
- [ ] Put auth in front of the API via Traefik forwardAuth, an OIDC proxy or a LAN-only ingress
- [ ] Verify the Plaid-Verification JWT on the webhook, which is currently unchecked
- [ ] Fix sync_all error handling, which leaves the session in pending-rollback so one bad Item fails the rest
- [ ] Make category filtering honour overrides instead of matching only category_primary and pfc_primary
- [ ] Add a way to create rules, since the Rule table and categorize() exist but nothing writes to them
- [ ] Add a recategorize backfill CLI, because rules currently apply only at sync time
- [ ] Add a ScheduledBackup for CNPG, since spec.backup is set but nothing triggers a snapshot
- [ ] Add backend tests for categorize, build_sankey, _asyncpg_url and sync upsert idempotency
- [ ] Run tests and lint in CI ahead of the image build
- [ ] Set PLAID_REDIRECT_URI in the chart, without which OAuth banks will not link
- [ ] Request Plaid production access and write a production secret to hench/plaid
- [ ] Add an unlink endpoint and UI, as Items cannot be removed once linked
- [ ] Add an accounts endpoint, since /transactions takes account_id but nothing lists accounts
- [ ] Break out income sources in the Sankey, where Income is a stub node with no inbound links
- [ ] Handle money as Decimal rather than float end to end
- [ ] Surface or drop pfc_confidence, which is stored and never used
- [ ] Handle the Plaid Link CDN script failing to load, where undefined window.Plaid throws a raw TypeError
- [ ] Replace $effect with onMount in App.svelte and surface loadMeta errors
- [ ] Promote _month_bounds out of services.sankey, imported privately by routers.transactions
- [ ] Fix the README pointer to deploy/argocd/hench.yaml, which lives in argo-k8s-stuff at personal/templates/hench.yaml
- [ ] Drop the non-existent deploy/** path from build-deploy.yml paths-ignore
- [ ] Update the README ESO section for the openbao-k8s-backend ClusterSecretStore
