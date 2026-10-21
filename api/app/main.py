from __future__ import annotations

from contextlib import asynccontextmanager
import io
import json
import logging
import os
import re
import shutil
import threading
import uuid
import zipfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

import fitz
import psycopg
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse, JSONResponse
from pydantic import BaseModel, Field, ConfigDict

from .pdf_ops import PdfError, inspect_pdf, office_to_pdf, transform
from .storage import sha, verified_path, staged_pdf, publish

STORE = Path(os.getenv('STORE_DIR', '/data'))
DB_URL = os.getenv('DATABASE_URL', 'postgresql://pdf:change-me-for-local-use@localhost:5432/pdf')
MAX_BYTES = 50 * 1024 * 1024
ALLOWED_OFFICE = {'.docx', '.xlsx', '.pptx', '.odt', '.ods', '.odp'}
logger = logging.getLogger('papertrail')
stop_expiry = threading.Event()


def db():
    return psycopg.connect(DB_URL, connect_timeout=5)


def initialize():
    STORE.mkdir(parents=True, exist_ok=True, mode=0o700)
    for attempt in range(30):
        try:
            with db() as conn:
                # Serialize startup migrations if two processes start together.
                conn.execute('SELECT pg_advisory_xact_lock(90422001)')
                conn.execute(Path(__file__).with_name('schema.sql').read_text())
            return
        except psycopg.OperationalError:
            if attempt == 29:
                raise
            stop_expiry.wait(1)


@asynccontextmanager
async def lifespan(app):
    initialize()
    stop_expiry.clear()
    worker = threading.Thread(target=expiry_worker, daemon=True, name='expiry-worker')
    worker.start()
    yield
    stop_expiry.set()
    worker.join(timeout=6)


app = FastAPI(title='Papertrail PDF', version='1.1.0', lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=os.getenv('CORS_ORIGINS', 'http://localhost:3000').split(','), allow_methods=['GET', 'POST', 'PUT', 'DELETE'], allow_headers=['Content-Type'])


@app.middleware('http')
async def no_cache(request, call_next):
    response = await call_next(request)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


@app.exception_handler(psycopg.OperationalError)
async def database_unavailable(request, exc):
    logger.warning('Database unavailable: %s', type(exc).__name__)
    return JSONResponse(status_code=503, content={'detail': 'Document database is unavailable. Retry shortly; do not re-upload until you check the library.'})


@app.exception_handler(OSError)
async def store_unavailable(request, exc):
    logger.warning('Document store error: %s', type(exc).__name__)
    return JSONResponse(status_code=503, content={'detail': 'The document store could not complete this request. Check disk space and permissions, then refresh the library before retrying.'})


def event(conn, document_id, kind, digest, details=None):
    conn.execute('INSERT INTO events (id,document_id,kind,digest,details) VALUES (%s,%s,%s,%s,%s::jsonb)',
                 (str(uuid.uuid4()), str(document_id), kind, digest, json.dumps(details or {}, allow_nan=False)))


def safe_name(name):
    name = Path(name.replace('\\', '/')).name
    name = re.sub(r'[^a-zA-Z0-9._-]', '_', name).strip('._')
    stem, suffix = os.path.splitext(name)
    return (stem[:110] + suffix[:10]) or 'document.pdf'


def doc_row(conn, document_id, lock='share', allow_expired=False):
    # All file operations hold a row lock until publication/read completes.
    row = conn.execute('SELECT id,name,original_path,original_hash,created_at,expires_at FROM documents WHERE id=%s AND deleted_at IS NULL FOR ' + ('UPDATE' if lock == 'update' else 'SHARE'), (str(document_id),)).fetchone()
    if not row:
        raise HTTPException(404, 'Document not found')
    if not allow_expired and row[5] and row[5] <= datetime.now(timezone.utc):
        raise HTTPException(410, 'Document has expired.')
    return row


def version_row(conn, document_id, version):
    row = conn.execute('SELECT path,digest FROM versions WHERE document_id=%s AND number=%s', (str(document_id), version)).fetchone()
    if not row:
        raise HTTPException(404, 'Version not found')
    return row


def source_path(conn, doc, version):
    row = (doc[2], doc[3]) if version == 0 else version_row(conn, doc[0], version)
    return verified_path(*row)


def next_version(conn, doc_id):
    return conn.execute('SELECT coalesce(max(number),0)+1 FROM versions WHERE document_id=%s', (doc_id,)).fetchone()[0]


def save_version(conn, doc_id, number, staged, operation):
    destination = STORE / doc_id / f'v{number}.pdf'
    digest = sha(staged)
    publish(staged, destination)
    try:
        conn.execute('INSERT INTO versions (document_id,number,path,digest,operation) VALUES (%s,%s,%s,%s,%s)', (doc_id, number, str(destination), digest, operation))
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return destination, digest


def expiry_worker():
    while not stop_expiry.is_set():
        try:
            expire_documents()
        except Exception:
            logger.exception('Expiry sweep failed; will retry')
        stop_expiry.wait(60)


@app.get('/health')
def health():
    with db() as conn:
        conn.execute('SELECT 1')
    writable = STORE.is_dir() and os.access(STORE, os.W_OK)
    if not writable:
        raise HTTPException(503, 'Document store is not writable. Check the data volume permissions.')
    return {'ok': True, 'store': writable, 'capabilities': {'ocr': bool(shutil.which('tesseract')), 'office': bool(shutil.which('libreoffice'))},
            'storage': 'remote' if os.getenv('RAILWAY_ENVIRONMENT_ID') else 'local'}


@app.get('/documents')
def list_documents():
    with db() as conn:
        rows = conn.execute('''SELECT d.id,d.name,d.original_hash,d.created_at,d.expires_at,coalesce(max(v.number),0)
          FROM documents d LEFT JOIN versions v ON v.document_id=d.id
          WHERE d.deleted_at IS NULL AND (d.expires_at IS NULL OR d.expires_at>now())
          GROUP BY d.id ORDER BY d.created_at DESC''').fetchall()
    return [{'id': str(r[0]), 'name': r[1], 'hash': r[2], 'created_at': r[3], 'expires_at': r[4], 'latest_version': r[5]} for r in rows]


@app.post('/documents', status_code=201)
def upload(file: UploadFile = File(...)):
    filename = safe_name(file.filename or 'document.pdf')
    suffix = Path(filename).suffix.lower()
    if suffix != '.pdf' and suffix not in ALLOWED_OFFICE:
        raise HTTPException(422, 'Upload a PDF or supported Office document.')
    document_id = str(uuid.uuid4())
    folder = STORE / document_id
    folder.mkdir(mode=0o700)
    original = folder / ('original' + suffix)
    size = 0
    try:
        with original.open('xb') as target:
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise HTTPException(413, 'File exceeds the 50 MB limit.')
                target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
        if size == 0:
            raise HTTPException(422, 'The file is empty.')
        if suffix == '.pdf':
            metadata = inspect_pdf(original)
        else:
            with original.open('rb') as stream:
                if stream.read(2) != b'PK':
                    raise HTTPException(422, 'Office file has an invalid format.')
            converted = folder / 'v1.pdf'
            metadata = office_to_pdf(original, converted)
            converted.chmod(0o400)
        original.chmod(0o400)
        digest = sha(original)
        with db() as conn:
            conn.execute('INSERT INTO documents (id,name,original_path,original_hash) VALUES (%s,%s,%s,%s)', (document_id, filename, str(original), digest))
            event(conn, document_id, 'uploaded', digest, {'name': filename, 'bytes': size})
            if suffix != '.pdf':
                converted_digest = sha(converted)
                conn.execute("INSERT INTO versions (document_id,number,path,digest,operation) VALUES (%s,1,%s,%s,'office_to_pdf')", (document_id, str(converted), converted_digest))
                event(conn, document_id, 'office_to_pdf', converted_digest, {'version': 1, 'source_hash': digest})
        return {'id': document_id, 'name': filename, 'hash': digest, 'metadata': metadata, 'latest_version': 0 if suffix == '.pdf' else 1}
    except (PdfError, ValueError) as exc:
        shutil.rmtree(folder)
        raise HTTPException(422, str(exc)) from exc
    except Exception:
        shutil.rmtree(folder)
        raise
    finally:
        file.file.close()


@app.get('/documents/{document_id}')
def get_document(document_id: uuid.UUID):
    with db() as conn:
        row = doc_row(conn, document_id)
        versions = conn.execute('SELECT number,digest,operation,created_at FROM versions WHERE document_id=%s ORDER BY number', (str(document_id),)).fetchall()
        events = conn.execute('SELECT kind,digest,details,created_at FROM events WHERE document_id=%s ORDER BY created_at,id', (str(document_id),)).fetchall()
        pending = conn.execute('SELECT token,signer_name,version,expires_at FROM invitations WHERE document_id=%s AND sealed_hash IS NULL AND revoked_at IS NULL AND expires_at>now()', (str(document_id),)).fetchall()
    return {'id': str(row[0]), 'name': row[1], 'hash': row[3], 'created_at': row[4], 'expires_at': row[5],
            'versions': [{'number': v[0], 'hash': v[1], 'operation': v[2], 'created_at': v[3]} for v in versions],
            'events': [{'kind': e[0], 'hash': e[1], 'details': e[2], 'created_at': e[3]} for e in events],
            'invitations': [{'token': str(i[0]), 'signer_name': i[1], 'version': i[2], 'expires_at': i[3]} for i in pending]}


class Operation(BaseModel):
    operation: str = Field(max_length=30)
    source_version: int = Field(0, ge=0)
    merge_document_ids: list[uuid.UUID] = Field(default_factory=list, max_length=30)
    options: dict = Field(default_factory=dict)


@app.post('/documents/{document_id}/operations', status_code=201)
def operate(document_id: uuid.UUID, request: Operation):
    doc_id = str(document_id)
    try:
        json.dumps(request.options, allow_nan=False)
        with db() as conn:
            ids = sorted({doc_id, *[str(i) for i in request.merge_document_ids]})
            locked = {i: doc_row(conn, i, 'update') for i in ids}
            path = source_path(conn, locked[doc_id], request.source_version)
            sources = [path]
            provenance = [{'document_id': doc_id, 'version': request.source_version, 'hash': sha(path)}]
            if request.operation == 'merge':
                if not request.merge_document_ids or len(set(request.merge_document_ids)) != len(request.merge_document_ids) or document_id in request.merge_document_ids:
                    raise PdfError('Choose distinct documents to append, excluding the current document.')
                for other_id in request.merge_document_ids:
                    other = locked[str(other_id)]
                    # Append the latest PDF version, preserving the explicit requested order.
                    other_version = next_version(conn, str(other_id)) - 1
                    source = source_path(conn, other, other_version)
                    sources.append(source)
                    provenance.append({'document_id': str(other_id), 'version': other_version, 'hash': sha(source)})
            elif request.merge_document_ids:
                raise PdfError('Additional documents are only accepted for merge.')
            number = next_version(conn, doc_id)
            with staged_pdf(STORE / doc_id) as staged:
                metadata = transform(sources, staged, request.operation, request.options)
                published, digest = save_version(conn, doc_id, number, staged, request.operation)
                event(conn, doc_id, request.operation, digest, {'version': number, 'source_version': request.source_version, 'inputs': provenance, 'options': request.options})
        return {'version': number, 'hash': digest, 'metadata': metadata}
    except Exception as exc:
        if isinstance(exc, (PdfError, ValueError, IndexError, TypeError, OverflowError)):
            raise HTTPException(422, str(exc)) from exc
        if isinstance(exc, FileExistsError):
            raise HTTPException(409, 'Output version already exists. No file was overwritten; restore or reconcile the store.') from exc
        raise


def file_response(path, filename, media_type=None):
    # Open under the row lock: unlinking later cannot interrupt this download.
    stream = Path(path).open('rb')
    def chunks():
        try:
            while chunk := stream.read(1024 * 1024):
                yield chunk
        finally:
            stream.close()
    return StreamingResponse(chunks(), media_type=media_type or ('application/pdf' if Path(path).suffix == '.pdf' else 'application/octet-stream'),
                             headers={'Content-Disposition': f'attachment; filename="{safe_name(filename)}"', 'Content-Length': str(os.fstat(stream.fileno()).st_size)})


@app.get('/documents/{document_id}/files/{version}')
def download(document_id: uuid.UUID, version: int):
    with db() as conn:
        row = doc_row(conn, document_id)
        path = source_path(conn, row, version)
        event(conn, document_id, 'downloaded', sha(path), {'version': version, 'delivery': 'stream_opened'})
        return file_response(path, row[1] if version == 0 else f'{Path(row[1]).stem}-v{version}.pdf')


def render_page(path, page):
    if path.suffix != '.pdf':
        raise HTTPException(422, 'Select the converted PDF version to preview or edit this Office file.')
    with fitz.open(path) as pdf:
        if not 1 <= page <= len(pdf):
            raise HTTPException(404, 'Page not found')
        sheet = pdf[page - 1]
        scale = min(1.5, 1400 / max(sheet.rect.width, sheet.rect.height))
        image = sheet.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False).tobytes('png')
    return Response(image, media_type='image/png')


@app.get('/documents/{document_id}/metadata/{version}')
def metadata(document_id: uuid.UUID, version: int):
    with db() as conn:
        row = doc_row(conn, document_id)
        path = source_path(conn, row, version)
        if path.suffix != '.pdf':
            raise HTTPException(422, 'Select the converted PDF version to preview or edit this Office file.')
        return inspect_pdf(path)


@app.get('/documents/{document_id}/preview/{version}/{page}')
def preview(document_id: uuid.UUID, version: int, page: int):
    with db() as conn:
        return render_page(source_path(conn, doc_row(conn, document_id), version), page)


class Expiry(BaseModel):
    days: int | None = Field(None, ge=1, le=3650)


@app.put('/documents/{document_id}/expiry')
def expiry(document_id: uuid.UUID, value: Expiry):
    with db() as conn:
        row = doc_row(conn, document_id, 'update')
        expires = datetime.now(timezone.utc) + timedelta(days=value.days) if value.days else None
        conn.execute('UPDATE documents SET expires_at=%s WHERE id=%s', (expires, str(document_id)))
        event(conn, document_id, 'expiry_changed', row[3], {'expires_at': expires.isoformat() if expires else None})
    return {'expires_at': expires}


def remove_bytes(document_id):
    folder = STORE / str(document_id)
    if folder.exists():
        shutil.rmtree(folder)


@app.delete('/documents/{document_id}')
def delete(document_id: uuid.UUID):
    return delete_document(document_id)


def delete_document(document_id, only_expired=False):
    with db() as conn:
        row = doc_row(conn, document_id, 'update', allow_expired=True)
        if only_expired and (row[5] is None or row[5] > datetime.now(timezone.utc)):
            return {'deleted': False}
        conn.execute('UPDATE documents SET deleted_at=now() WHERE id=%s', (str(document_id),))
        conn.execute('UPDATE invitations SET revoked_at=now() WHERE document_id=%s AND sealed_hash IS NULL', (str(document_id),))
        event(conn, document_id, 'deleted', row[3])
    # A committed tombstone blocks every subsequent read, even if disk cleanup fails.
    try:
        remove_bytes(document_id)
    except OSError:
        logger.exception('File cleanup will be retried for %s', document_id)
        return JSONResponse(status_code=202, content={'deleted': True, 'cleanup_pending': True})
    return {'deleted': True, 'cleanup_pending': False}


@app.post('/maintenance/expire')
def expire_documents():
    with db() as conn:
        ids = [r[0] for r in conn.execute('SELECT id FROM documents WHERE expires_at<=now() AND deleted_at IS NULL').fetchall()]
        deleted_ids = [r[0] for r in conn.execute('SELECT id FROM documents WHERE deleted_at IS NOT NULL').fetchall()]
    removed = 0
    for document_id in ids:
        try:
            result = delete_document(document_id, only_expired=True)
            if not isinstance(result, dict) or result.get('deleted'):
                removed += 1
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
    for document_id in deleted_ids:
        remove_bytes(document_id)
    return {'expired': removed}


@app.get('/export')
def export_all():
    # Spool to disk after 8 MiB rather than holding an entire library in memory.
    import tempfile
    output = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
    try:
        with db() as conn, zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            rows = conn.execute('SELECT id,name,original_path,original_hash,created_at,expires_at FROM documents WHERE deleted_at IS NULL AND (expires_at IS NULL OR expires_at>now()) ORDER BY id FOR SHARE').fetchall()
            manifest = []
            for row in rows:
                doc_id = str(row[0])
                archive.write(verified_path(row[2], row[3]), f'{doc_id}/{Path(row[2]).name}')
                versions = []
                for number, path, digest, operation, created_at in conn.execute('SELECT number,path,digest,operation,created_at FROM versions WHERE document_id=%s ORDER BY number', (doc_id,)).fetchall():
                    archive.write(verified_path(path, digest), f'{doc_id}/v{number}.pdf')
                    versions.append({'number': number, 'hash': digest, 'operation': operation, 'created_at': created_at.isoformat()})
                manifest.append({'id': doc_id, 'name': row[1], 'original_hash': row[3], 'created_at': row[4].isoformat(), 'expires_at': row[5].isoformat() if row[5] else None, 'versions': versions})
            archive.writestr('manifest.json', json.dumps(manifest, indent=2))
            events = conn.execute('SELECT id,document_id,kind,digest,details,created_at FROM events ORDER BY created_at,id').fetchall()
            # Capability tokens are intentionally excluded from portable audit exports.
            archive.writestr('events.json', json.dumps([{'id': str(e[0]), 'document_id': str(e[1]), 'kind': e[2], 'hash': e[3], 'details': {k: v for k, v in e[4].items() if k != 'token'}, 'at': e[5].isoformat()} for e in events], indent=2))
        output.seek(0)
    except Exception:
        output.close()
        raise
    def chunks():
        try:
            while chunk := output.read(1024 * 1024):
                yield chunk
        finally:
            output.close()
    return StreamingResponse(chunks(), media_type='application/zip', headers={'Content-Disposition': 'attachment; filename=papertrail-backup.zip'})


class Invite(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    signer_name: str = Field(min_length=1, max_length=120)
    version: int = Field(ge=0)
    page: int = Field(ge=1)
    x: float = Field(ge=0)
    y: float = Field(ge=0)
    width: float = Field(240, ge=80, le=600)
    height: float = Field(50, ge=40, le=200)
    days: int = Field(7, ge=1, le=30)


@app.post('/documents/{document_id}/invitations', status_code=201)
def invite(document_id: uuid.UUID, value: Invite):
    doc_id = str(document_id)
    with db() as conn:
        row = doc_row(conn, doc_id, 'update')
        if value.version != next_version(conn, doc_id) - 1:
            raise HTTPException(409, 'Create invitations from the latest PDF version so earlier edits and signatures are preserved.')
        path = source_path(conn, row, value.version)
        if path.suffix != '.pdf':
            raise HTTPException(422, 'Invite a signer to the converted PDF version.')
        if not value.signer_name.strip():
            raise HTTPException(422, 'Signer name is required.')
        with fitz.open(path) as pdf:
            if value.page > len(pdf):
                raise HTTPException(422, 'Signature page is out of range.')
            page = pdf[value.page - 1]
            rect = fitz.Rect(value.x, value.y, value.x + value.width, value.y + value.height)
            if not page.rect.contains(rect):
                raise HTTPException(422, 'The entire signature field must fit inside the page.')
        token = str(uuid.uuid4())
        expires = datetime.now(timezone.utc) + timedelta(days=value.days)
        conn.execute('INSERT INTO invitations (token,document_id,version,signer_name,page,x,y,width,height,expires_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                     (token, doc_id, value.version, value.signer_name.strip(), value.page, value.x, value.y, value.width, value.height, expires))
        event(conn, doc_id, 'invited', sha(path), {'signer': value.signer_name.strip(), 'version': value.version, 'delivery': 'link_created_not_sent', 'expires_at': expires.isoformat(), 'field': [value.page, value.x, value.y, value.width, value.height]})
    return {'token': token, 'url': os.getenv('APP_PUBLIC_URL', 'http://localhost:3000').rstrip('/') + '/sign/' + token, 'delivery': 'link_created_not_sent', 'expires_at': expires}


def invitation_row(conn, token, lock=False):
    # Lock documents first throughout the API, including concurrent seal/delete.
    ref = conn.execute('SELECT document_id FROM invitations WHERE token=%s', (str(token),)).fetchone()
    if not ref:
        raise HTTPException(404, 'Invitation not found')
    doc = doc_row(conn, ref[0], 'update' if lock else 'share')
    row = conn.execute('SELECT token,document_id,version,signer_name,page,x,y,width,height,consent_at,sealed_hash,sealed_version,expires_at,revoked_at FROM invitations WHERE token=%s FOR ' + ('UPDATE' if lock else 'SHARE'), (str(token),)).fetchone()
    if row[13] or row[12] <= datetime.now(timezone.utc):
        raise HTTPException(410, 'Invitation has expired or was revoked. Ask the document owner for a new link.')
    return row, doc


@app.get('/invitations/{token}')
def invitation(token: uuid.UUID):
    with db() as conn:
        row, doc = invitation_row(conn, token)
        path = source_path(conn, doc, row[11] if row[10] and row[11] else row[2])
        info = inspect_pdf(path)
    return {'signer_name': row[3], 'document_name': doc[1], 'version': row[2], 'consent_at': row[9], 'sealed_hash': row[10], 'expires_at': row[12], 'metadata': info,
            'field': {'page': row[4], 'x': row[5], 'y': row[6], 'width': row[7], 'height': row[8]}}


@app.get('/invitations/{token}/preview/{page}')
def invitation_preview(token: uuid.UUID, page: int):
    with db() as conn:
        row, doc = invitation_row(conn, token)
        return render_page(source_path(conn, doc, row[11] if row[10] and row[11] else row[2]), page)


@app.get('/invitations/{token}/file')
def invitation_file(token: uuid.UUID):
    with db() as conn:
        row, doc = invitation_row(conn, token)
        version = row[11] if row[10] and row[11] else row[2]
        path = source_path(conn, doc, version)
        event(conn, doc[0], 'signer_downloaded', sha(path), {'version': version, 'delivery': 'stream_opened'})
        return file_response(path, f'{Path(doc[1]).stem}-v{version}.pdf')


@app.delete('/documents/{document_id}/invitations/{token}')
def revoke(document_id: uuid.UUID, token: uuid.UUID):
    with db() as conn:
        doc = doc_row(conn, document_id, 'update')
        row = conn.execute('UPDATE invitations SET revoked_at=now() WHERE token=%s AND document_id=%s AND sealed_hash IS NULL AND revoked_at IS NULL RETURNING token', (str(token), str(document_id))).fetchone()
        if not row:
            raise HTTPException(404, 'Pending invitation not found')
        event(conn, document_id, 'invitation_revoked', doc[3])
    return {'revoked': True}


class Consent(BaseModel):
    agreed: bool
    typed_name: str = Field(min_length=1, max_length=120)


@app.post('/invitations/{token}/seal')
def seal(token: uuid.UUID, value: Consent):
    if not value.agreed:
        raise HTTPException(422, 'Consent is required.')
    published = None
    try:
        with db() as conn:
            row, doc = invitation_row(conn, token, lock=True)
            if row[10]:
                raise HTTPException(409, 'Invitation already completed.')
            if value.typed_name.strip().casefold() != row[3].casefold():
                raise HTTPException(422, 'Typed name must match the invitation.')
            doc_id = str(doc[0])
            source = source_path(conn, doc, row[2])
            number = next_version(conn, doc_id)
            # Require sequential signatures so a stale link cannot discard earlier signatures/edits.
            if row[2] != number - 1:
                raise HTTPException(409, 'This invitation targets an older version. Ask the owner for a link to the latest version.')
            with staged_pdf(STORE / doc_id) as staged, fitz.open(source) as pdf:
                page = pdf[row[4] - 1]
                # Normalize rotation for coordinates matching the visible preview.
                page.remove_rotation()
                rect = fitz.Rect(row[5], row[6], row[5] + row[7], row[6] + row[8])
                font = fitz.Font('helv')
                if any(not font.has_glyph(ord(char)) for char in value.typed_name.strip()):
                    raise HTTPException(422, 'This typed signature font cannot represent that name. Use a supported spelling agreed with the owner.')
                if page.insert_textbox(rect, value.typed_name.strip() + '\nConsented electronic signature', fontsize=12, color=(0.1, 0.2, 0.4)) < 0:
                    raise HTTPException(422, 'Name does not fit the signature field. Ask the owner for a larger field.')
                pdf.save(staged)
                published, digest = save_version(conn, doc_id, number, staged, 'signature_seal')
                conn.execute('UPDATE invitations SET consent_at=now(),sealed_hash=%s,sealed_version=%s WHERE token=%s', (digest, number, str(token)))
                event(conn, doc_id, 'consent_recorded', digest, {'typed_name': value.typed_name.strip(), 'source_hash': sha(source), 'source_version': row[2]})
                event(conn, doc_id, 'signature_sealed', digest, {'version': number, 'source_version': row[2]})
        return {'version': number, 'hash': digest}
    except Exception:
        raise
