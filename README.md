# Papertrail PDF

A personal PDF desk built with Next.js 15, TypeScript, FastAPI, Python 3.12, pikepdf, LibreOffice headless, Tesseract OCR, and PostgreSQL. Originals stay immutable; operations write new numbered PDF versions. The audit log records timestamps, SHA-256 hashes, and signature-link delivery outcomes.

> **Low-stakes personal use only.** Typed signature links are not qualified or regulated electronic signatures. Do not use this project for government identity verification, full Acrobat-style editing, or enterprise document governance.

## First run

From the repository root, run one command:

```sh
docker compose up --build -d
```

Open `http://localhost:3000`. The API is at `http://localhost:8000` and its interactive docs at `/docs`. Both ports bind to localhost. Compose uses local development defaults. For a private machine, copy `.env.example` to `.env`, set a strong `POSTGRES_PASSWORD`, and set the matching password in `DATABASE_URL` before the first run. Never commit `.env`.

## What it does

- Upload PDFs and Office files (DOCX, XLSX, PPTX, ODT, ODS, ODP), up to 50 MB each. Office uploads retain the original and create a PDF version using LibreOffice.
- Merge, split, reorder, rotate, compress, watermark, redact, annotate, and OCR PDFs. Redaction removes content within the chosen rectangle; OCR runs Tesseract on rendered pages. Coordinates are PDF points from the top left for redaction, annotation, and signature placement.
- Preview the first page, download any version, set an expiry, delete files, and export all active documents with versions, manifest, and audit events as a ZIP.
- Create a link for a signer. The signer types their invited name and consents. The service places the name and records consent time plus the final SHA-256 hash. **The link is not sent automatically**; the audit event records `link_created_not_sent`. This is a simple typed signature, with no identity verification.

Inspect outputs carefully: fonts, forms, existing signatures, and encryption may change or become invalid. Encrypted inputs are rejected with a recoverable error. Compression is lossless stream/object repackaging, so size reduction varies.

## Architecture and data

The browser talks to a same-origin Next.js API route, which forwards requests to a private FastAPI service. PostgreSQL stores document metadata, versions, invitations, and an append-only event log. PDF bytes live in a local Docker volume (`pdfdata`), with original files marked read-only. Database bytes live in `pgdata`. Paths inside the API container are under `/data/<document-id>/`. No accounts, billing, analytics, telemetry, or hosted control plane are included. The app does not send documents to an external conversion vendor. Office and OCR tools run inside the API container.

The API has no user authentication and is intended for a trusted local machine or private network. The web service can require one shared `WORKSPACE_PASSWORD` using browser Basic authentication; this is a gate, not an account system. Signature invitation links are accessible to anyone who has the token. Do not expose the API port publicly. A Railway deployment is remote storage, so documents uploaded there no longer stay on your local machine.

## Railway deployment

Create a Railway project with three services:

1. **PostgreSQL:** add a Railway PostgreSQL database.
2. **API:** deploy this repository with root directory `api` and its Dockerfile. Attach a persistent volume at `/data`. Set `DATABASE_URL` to the Railway PostgreSQL connection variable, `STORE_DIR=/data`, `PORT=8000`, and `APP_PUBLIC_URL` to the public web URL. Give this service **private networking only**; do not generate a public domain.
3. **Web:** deploy this repository with root directory `web` and its Dockerfile. Set `API_INTERNAL_URL` to the API service's private address, such as `http://api.railway.internal:8000`, and set a strong `WORKSPACE_PASSWORD` through Railway variables. Generate a public domain for this service. The signer links use this domain.

Set `CORS_ORIGINS` on the API to the web domain if calling the API directly from a trusted development browser. Same-origin web requests do not need browser CORS. Keep `WORKSPACE_PASSWORD` and database credentials out of Git. On Railway, the file store and database belong to your Railway project, rather than your local computer.

## Backup and restore

Use **Export everything** for a portable ZIP of active files and audit events. For a complete backup, also preserve the PostgreSQL database and both Compose volumes:

```sh
docker compose exec -T db pg_dump -U pdf -d pdf > papertrail.sql
docker run --rm -v pdf_editor_pdfdata:/source:ro -v "$PWD":/backup alpine tar -C /source -czf /backup/papertrail-files.tar.gz .
```

The volume name may differ with the Compose project name; check `docker volume ls`. Restore the database with `psql` and extract the files archive into the `pdfdata` volume. The ZIP export excludes previously deleted files, while database audit events remain.

## Tests

```sh
docker compose run --rm api python -m pytest -q tests/test_pdf_ops.py
docker compose exec -T -e TEST_API_URL=http://web:3000/api api python tests/e2e.py
```

The end-to-end test uses the running API and its PostgreSQL and file store. The PDF operation tests use temporary files and verify source preservation. Run `docker compose build` and `docker compose up -d` after changing dependencies or code.

## Permissions and limits

Anyone who can reach the API can use it. Compose binds the web and API ports to localhost; use firewall/private-network rules in shared environments. A background sweep applies expiry about once per minute, and `POST /maintenance/expire` triggers an immediate sweep. Deletion removes file bytes and hides metadata while retaining the audit event. The audit log is append-only at the application level; database administrators can still alter it. For high assurance, export and independently preserve audit hashes. The backup ZIP is generated in memory and is suited to personal-size libraries, not large archives.
