# hench

Self-hosted personal finance dashboard. Pulls bank
transactions via Plaid and renders a monthly Sankey cash-flow chart
(income → category → subcategory), plus a transactions table with inline
category overrides.

Stack: FastAPI + async SQLAlchemy + Postgres (CNPG), Svelte + ECharts, deployed
to K3s via ArgoCD.

## Layout

| Path | What |
|------|------|
| `backend/` | FastAPI API, Plaid sync, Alembic migrations, sync CLI |
| `frontend/` | Vite + Svelte SPA (Sankey + transactions) |
| `helm/hench/` | Helm chart (api, frontend, nightly sync CronJob, CNPG, ESO) |
| `deploy/argocd/` | ArgoCD Application manifest |

## Local development

```sh
cp .env.example .env        # add Plaid sandbox creds + a FERNET_KEY
docker compose up           # Postgres + API (:8000) + Vite dev server (:5173)
```

Then open http://localhost:5173. The Vite dev server proxies `/api` to the API
(stripping the prefix, mirroring the in-cluster Traefik middleware).

`FERNET_KEY`: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`

Switch Plaid env with `PLAID_ENV=sandbox|production`.

## Deploy

CI/CD: every push to `main` runs `.github/workflows/build-deploy.yml`, which
builds and pushes `ncging/hench-backend` / `ncging/hench-frontend` tagged with
the commit SHA, then commits the new tags into `helm/hench/values.yaml` —
ArgoCD syncs the rollout from there. Requires the `DOCKERHUB_USERNAME` and
`DOCKERHUB_TOKEN` repo secrets. Chart-only pushes skip the image build and go
straight to ArgoCD.

The chart mirrors the homelab conventions in
`Gin-G/argo-k8s-stuff` — Traefik ingress with an `/api` strip middleware,
`letsencrypt-cloudflare` TLS, a CNPG `Cluster`, and ExternalSecrets from
OpenBao. Add the app via `deploy/argocd/hench.yaml`.

OpenBao kv path `hench/plaid` must hold: `client_id`, `secret`, `fernet_key`
(see `helm/hench/values.yaml → secrets`).

The API serves routes at root (e.g. `/sankey`); the ingress maps `/api/*`.
Migrations run in an init container; transactions sync runs nightly via the
CronJob and on demand via the `SYNC_UPDATES_AVAILABLE` webhook at `/api/webhook`.

## Users and per-user encryption

Sign-in is Cloudflare Access (Google). Every allow-listed email
(`cfAccess.allowedEmails`, and the Access policy) is its own user, created on
first sign-in, with their own linked banks, bills, plan and net worth. Every
query is filtered to the signed-in user by one session hook
(`backend/app/tenancy.py`); a query run with no user in scope fails.

Each user's identifying text (account names and numbers, transaction and
merchant names, bill and holding names and notes, Liability and bill source
details, Plaid and Google tokens) is encrypted with their own data key. That
key is stored wrapped by an OpenBao Transit key of their own,
`hench-user-<id>` (`backend/app/keys.py`). Amounts and dates stay plain so
SQL can still filter and sum them. A database dump or backup alone shows
numbers with no names attached. The app's OpenBao identity can unwrap every
user's key (the nightly sync writes data while users are away), so this
does not hide data from whoever runs the cluster.

One-time OpenBao setup, before deploying:

```sh
bao secrets enable transit
bao policy write hench-transit - <<'POLICY'
path "transit/keys/hench-user-*"    { capabilities = ["create", "update"] }
path "transit/encrypt/hench-user-*" { capabilities = ["update"] }
path "transit/decrypt/hench-user-*" { capabilities = ["update"] }
POLICY
bao write auth/kubernetes/role/hench \
  bound_service_account_names=default \
  bound_service_account_namespaces=hench \
  policies=hench-transit ttl=1h
```

The backend refuses to start in production without `OPENBAO_ADDR`. The init
container runs `python -m app.cli encrypt` after migrations, which encrypts
anything still in plaintext (a no-op once done). Other maintenance:
`python -m app.cli users` lists users, `users set-email ID EMAIL` fixes one,
and `keys rewrap` moves keys wrapped locally (dev, with `FERNET_KEY`) into
OpenBao.

Data from before there were users belongs to `cfAccess.ownerEmail`, or the
first allowed email when that is empty. The Longmont utility login is that
user's too; a Gmail-read bill (Xcel) is read for whoever connected Gmail.
