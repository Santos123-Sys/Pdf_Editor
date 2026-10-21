# Railway production deployment

The owner authorized hosting on Railway on 2026-10-21. Documents uploaded to that installation will live in the owner's Railway project, rather than on the local computer.

## Current deployment receipt

Deployment is **blocked, not completed**. Railway rejected project creation with `Deploys have been paused temporarily`. A subsequent project inventory confirmed that no partial Papertrail PDF project existed. The [Railway status page](https://status.railway.com/) reported paused builds across all regions and partial API unavailability. No hosted URL or new credential exists yet.

## Concrete configuration

[deploy/railway-plan.json](../deploy/railway-plan.json) records the complete intended service settings and variable references. It is a deployment plan for the Railway tools, not an automatically applied IaC file. It contains no credentials. Prefer the EU West region for this installation and keep all services in the same region.

| Service | Source | Health check | Exposure | Persistent data |
| --- | --- | --- | --- | --- |
| Postgres | Railway PostgreSQL template | Database startup | Private only | `/var/lib/postgresql/data` |
| api | This repository, `/api`, Dockerfile | `/health` | Private only | 5 GB volume at `/data`; one replica |
| web | This repository, `/web`, Dockerfile | `/healthz` | Public HTTPS with shared password | Document bytes remain in the API volume |

The public readiness route returns only `{ "ok": true/false }`. It checks the upstream API, PostgreSQL/store readiness, and the presence of the required remote workspace password. It does not expose document metadata or unlock protected owner routes.

## Execution order after Railway resumes provisioning

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
