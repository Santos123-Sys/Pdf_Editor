# Railway production deployment

The owner authorized hosting on Railway on 2026-10-21. Documents uploaded to that installation will live in the owner's Railway project, rather than on the local computer.

## Current deployment receipt

Provisioning resumed on 2026-10-24. Project `d2558fef-083f-47b9-b2a8-7a02de2f3c11`, production environment `875ce382-40fa-43e4-af77-2e989dda695b`, now contains PostgreSQL, API with a 5 GB /data volume, and web. Public URL: https://web-production-af9ec.up.railway.app . Deployment acceptance checks are still pending; provisioning alone does not establish a working application.

Railway's builder rejects optional BuildKit secret mounts in the local Dockerfiles. Both services must use `Dockerfile.railway` under their respective root directories. These variants install the same dependencies using normal TLS verification; local Dockerfiles retain optional certificate support. CI builds both Railway images in addition to exercising the local stack.

The workspace password is generated cryptographically and stored only in Railway variables. Retrieve `WORKSPACE_PASSWORD` from web → Variables; the login username is `workspace`. API and Postgres have no public domain or TCP proxy. The document volume is in EU West; the PostgreSQL template initially placed its volume in US East. Region alignment remains an operational follow-up.

## Concrete configuration

[deploy/railway-plan.json](../deploy/railway-plan.json) records the complete intended service settings and variable references. It is a deployment plan for the Railway tools, not an automatically applied IaC file. It contains no credentials. Prefer the EU West region for this installation and keep all services in the same region.

| Service | Source | Health check | Exposure | Persistent data |
| --- | --- | --- | --- | --- |
| Postgres | Railway PostgreSQL template | Database startup | Private only | `/var/lib/postgresql/data` |
| api | This repository, `/api`, Dockerfile.railway | `/health` | Private only | 5 GB volume at `/data`; one replica |
| web | This repository, `/web`, Dockerfile.railway | `/healthz` | Public HTTPS with shared password | Document bytes remain in the API volume |

The public readiness route returns only `{ "ok": true/false }`. It checks the upstream API, PostgreSQL/store readiness, and the presence of the required remote workspace password. It does not expose document metadata or unlock protected owner routes.

## Deployment and verification procedure

1. Inventory projects again before retrying creation, to avoid duplicates. Create `Papertrail PDF` in the specified workspace, with the `production` environment.
2. Describe and deploy Railway's PostgreSQL template. Use its generated database credentials. Inspect the result and remove any template-created public database domain or TCP proxy, since this installation uses private networking.
3. Create empty `api` and `web` services. Apply the root directories, health checks, region, restart settings and one replica before attaching the GitHub source.
4. Attach the document volume at `/data` before the API first starts. Wire the API database URL through `${{Postgres.DATABASE_URL}}`.
5. Generate a cryptographically random workspace password and set it directly in the web service's `WORKSPACE_PASSWORD` variable. Do not commit it, place it in a URL, or print it in logs. The username may be `workspace`. The owner can retrieve or change the value in the web service's Railway Variables tab.
6. Attach this repository's `main` branch to each configured service. Generate only the web HTTPS domain. Resolve `APP_PUBLIC_URL` and `CORS_ORIGINS` to that address and verify private DNS connectivity from web to API. If the environment resolves a private service only over IPv6, configure an appropriate API listener before exposing the web service; do not publish the API as a workaround.
7. Observe `SUCCESS` for all three deployments. Verify public `/healthz` returns 200, owner routes without credentials return 401, authenticated upload/transform/download works, and a signer can review and consent without owner credentials. Use generated fixture documents, then delete those fixture bytes.
8. Record the actual project/service IDs, URL, deployed commit and verification outcomes here. Until these checks pass, do not label the project deployed.

## Backups and operations

Persistent volumes survive service redeploys; they are not a substitute for backups. Preserve both PostgreSQL and the API document volume consistently, plus deployment configuration. The existing Compose backup script applies to local installations; do not run it and assume it captured Railway data. A Railway backup needs a separate verified database-and-file snapshot or a quiesced export from the deployed services.

Use the in-app ZIP export for portable active documents and audit events. Full database/volume restoration was tested for the local stack; repeat a restoration drill for the actual hosted installation before relying on it for important files. Keep the low-stakes-use warning visible.
