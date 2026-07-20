# hench

Self-hosted personal finance dashboard — a Monarch alternative. Pulls bank
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

Auth is network-level only (single-user homelab); OIDC middleware can be added
in `backend/app/main.py` without touching routers.
