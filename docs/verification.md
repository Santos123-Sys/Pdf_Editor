# Verification record

Scope: local personal PDF workspace, 2026-10-21.

## Completed locally

- Python 3.12, required Python libraries installed from `api/requirements.txt`.
- `PYTHONPATH=api python3 -m pytest -q api/tests/test_pdf_ops.py`: **10 passed**. Covers merge/split/reorder, rotate/compress/watermark, actual Tesseract OCR, region redaction, rotated coordinates, annotation, immutable source/destination paths, passwordless encryption rejection, ordinary AcroForm preservation and field name collision handling, invalid options, and unavailable Office tooling.
- `NEXT_TELEMETRY_DISABLED=1 npm run build` from `web`: **passed**, including TypeScript checks and production compilation.
- `python3 -m compileall -q api/app`: passed.
- `git diff --check`: passed.

## Complete stack gate — passed

[Verified CI run](https://github.com/Santos123-Sys/Pdf_Editor/actions/runs/39622480055), implementation commit `139274d80ad216e5348ae26157f7677480d9e777`:

- Documented Compose build/start: passed; all services healthy.
- Real PDF + PostgreSQL regressions: **20 passed**.
- Full API path through the web proxy: passed, including actual LibreOffice conversion, consent, seal, export, expiry and deletion.
- Playwright: **2 passed**, including signer review without valid owner credentials, owner-route denial, middleware bypass attempt, mobile overflow check and recoverable API failure.
- Consistent backup and isolated PostgreSQL/volume restoration: passed; **6 file hashes and 19 audit events verified**.
- [Browser screenshots](https://github.com/Santos123-Sys/Pdf_Editor/actions/runs/39622480055/artifacts/12354997828) are retained as CI artifacts.

These checks use generated fixtures, not personal documents. This certifies those journeys on the CI Linux environment; it does not certify every possible PDF/Office feature or the user's computer.


[PR #1](https://github.com/Santos123-Sys/Pdf_Editor/pull/1) runs the Compose/PostgreSQL/LibreOffice/Playwright workflow. The CI result is the authoritative complete-stack gate; a local unit-test pass alone does not certify it. Workflow commands:

```sh
docker compose up --build -d --wait --wait-timeout 300
docker compose exec -T db createdb -U pdf pdf_test
docker compose run --rm -e DATABASE_URL=postgresql://pdf:change-me-for-local-use@db:5432/pdf_test -e STORE_DIR=/tmp/test-store api python -m pytest -q tests
docker compose exec -T -e TEST_API_URL=http://web:3000/api -e TEST_WORKSPACE_PASSWORD="$TEST_WORKSPACE_PASSWORD" api python tests/e2e.py
# In web:
npm ci --no-audit --no-fund
npx playwright install --with-deps chromium
npx playwright test
```

The workflow generates a two-page PDF fixture before Playwright, uses a disposable shared workspace password, and stores browser screenshots/traces as an artifact. No personal documents are used. Integration tests use a separate database; running them against a real library is unsupported.

## Boundaries

- Docker and PostgreSQL are absent in the local execution environment. The complete stack is therefore tested in GitHub CI.
- The local Playwright browser download failed; browser verification is delegated to the CI runner, with screenshots/traces retained.
- No Railway service for this repository existed in the connected account. Local storage remains the default; no remote store was created.
- A complete backup restoration drill passed in CI against a separate database and file volume. Periodic recovery checks on the actual installation are still the owner’s responsibility.
- Arbitrary PDFs, XFA forms, unusual fonts, signed/encrypted inputs, and Office layout fidelity are not comprehensively certified. Warnings remain visible. Redaction does not certify that all sensitive data is absent from the document.
- Filesystem publication and SQL commit are not a single atomic transaction. Orphans fail closed and require deliberate reconciliation; existing bytes are never overwritten.
