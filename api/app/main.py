from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
import zipfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

import fitz
import psycopg
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from .pdf_ops import PdfError, inspect_pdf, office_to_pdf, transform

STORE = Path(os.getenv("STORE_DIR", "/data"))
DB_URL = os.getenv("DATABASE_URL", "postgresql://pdf:change-me-for-local-use@localhost:5432/pdf")
MAX_BYTES = 50 * 1024 * 1024
ALLOWED_OFFICE = {".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp"}
app = FastAPI(title="Papertrail PDF", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:3000").split(","), allow_methods=["*"], allow_headers=["*"])


def db():
    return psycopg.connect(DB_URL)


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def event(conn, document_id: str, kind: str, digest: str, details: dict | None = None):
    conn.execute("INSERT INTO events (id, document_id, kind, digest, details) VALUES (%s,%s,%s,%s,%s::jsonb)",
                 (str(uuid.uuid4()), document_id, kind, digest, json.dumps(details or {})))


def safe_name(name: str) -> str:
    name = Path(name.replace("\\", "/")).name
    name = re.sub(r"[^a-zA-Z0-9._-]", "_", name).strip("._")[:120]
    return name or "document.pdf"


def doc_row(conn, document_id: str):
    row = conn.execute("SELECT id, name, original_path, original_hash, created_at, expires_at FROM documents WHERE id=%s AND deleted_at IS NULL", (document_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Document not found")
    return row


def version_row(conn, document_id: str, version: int):
    row = conn.execute("SELECT path, digest FROM versions WHERE document_id=%s AND number=%s", (document_id, version)).fetchone()
    if not row:
        raise HTTPException(404, "Version not found")
    return row


@app.on_event("startup")
def startup():
    STORE.mkdir(parents=True, exist_ok=True)
    for attempt in range(30):
        try:
            with db() as conn:
                conn.execute("""CREATE TABLE IF NOT EXISTS documents (
                  id uuid PRIMARY KEY, name text NOT NULL, original_path text NOT NULL,
                  original_hash text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
                  expires_at timestamptz, deleted_at timestamptz)""")
                conn.execute("""CREATE TABLE IF NOT EXISTS versions (
                  document_id uuid NOT NULL REFERENCES documents(id), number integer NOT NULL,
                  path text NOT NULL, digest text NOT NULL, operation text NOT NULL,
                  created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(document_id, number))""")
                conn.execute("""CREATE TABLE IF NOT EXISTS events (
                  id uuid PRIMARY KEY, document_id uuid NOT NULL REFERENCES documents(id),
                  kind text NOT NULL, digest text NOT NULL, details jsonb NOT NULL DEFAULT '{}',
                  created_at timestamptz NOT NULL DEFAULT now())""")
                conn.execute("""CREATE TABLE IF NOT EXISTS invitations (
                  token uuid PRIMARY KEY, document_id uuid NOT NULL REFERENCES documents(id),
                  version integer NOT NULL, signer_name text NOT NULL, signer_email text,
                  page integer NOT NULL, x real NOT NULL, y real NOT NULL,
                  consent_at timestamptz, sealed_hash text, created_at timestamptz NOT NULL DEFAULT now())""")
            threading.Thread(target=expiry_worker, daemon=True, name="expiry-worker").start()
            return
        except psycopg.OperationalError:
            if attempt == 29:
                raise
            time.sleep(1)


def expiry_worker():
    while True:
        try:
            expire_documents()
        except Exception:
            # The next sweep retries after a transient database failure.
            pass
        time.sleep(60)


@app.get("/health")
def health():
    with db() as conn:
        conn.execute("SELECT 1")
    return {"ok": True, "store": STORE.exists()}


@app.get("/documents")
def list_documents():
    with db() as conn:
        rows = conn.execute("""SELECT d.id, d.name, d.original_hash, d.created_at, d.expires_at,
          coalesce(max(v.number),0) FROM documents d LEFT JOIN versions v ON v.document_id=d.id
          WHERE d.deleted_at IS NULL GROUP BY d.id ORDER BY d.created_at DESC""").fetchall()
    return [{"id": str(r[0]), "name": r[1], "hash": r[2], "created_at": r[3], "expires_at": r[4], "latest_version": r[5]} for r in rows]


@app.post("/documents", status_code=201)
async def upload(file: UploadFile = File(...)):
    filename = safe_name(file.filename or "document.pdf")
    suffix = Path(filename).suffix.lower()
    if suffix != ".pdf" and suffix not in ALLOWED_OFFICE:
        raise HTTPException(422, "Upload a PDF or supported Office document.")
    document_id = str(uuid.uuid4())
    folder = STORE / document_id
    folder.mkdir(mode=0o700)
    original = folder / ("original" + suffix)
    size = 0
    try:
        with original.open("xb") as target:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise HTTPException(413, "File exceeds the 50 MB limit.")
                target.write(chunk)
        if size == 0:
            raise HTTPException(422, "The file is empty.")
        if suffix == ".pdf":
            metadata = inspect_pdf(original)
        else:
            if original.read_bytes()[:2] != b"PK":
                raise HTTPException(422, "Office file has an invalid format.")
            converted = folder / "v1.pdf"
            metadata = office_to_pdf(original, converted)
        original.chmod(0o400)
        digest = sha(original)
        with db() as conn:
            conn.execute("INSERT INTO documents (id,name,original_path,original_hash) VALUES (%s,%s,%s,%s)", (document_id, filename, str(original), digest))
            event(conn, document_id, "uploaded", digest, {"name": filename, "bytes": size})
            if suffix != ".pdf":
                converted_digest = sha(converted)
                conn.execute("INSERT INTO versions (document_id,number,path,digest,operation) VALUES (%s,1,%s,%s,'office_to_pdf')", (document_id, str(converted), converted_digest))
                event(conn, document_id, "office_to_pdf", converted_digest, {"version": 1})
        return {"id": document_id, "name": filename, "hash": digest, "metadata": metadata,
                "latest_version": 0 if suffix == ".pdf" else 1}
    except (PdfError, ValueError) as exc:
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(422, str(exc)) from exc
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise


@app.get("/documents/{document_id}")
def get_document(document_id: uuid.UUID):
    with db() as conn:
        row = doc_row(conn, str(document_id))
        versions = conn.execute("SELECT number,digest,operation,created_at FROM versions WHERE document_id=%s ORDER BY number", (str(document_id),)).fetchall()
        events = conn.execute("SELECT kind,digest,details,created_at FROM events WHERE document_id=%s ORDER BY created_at,id", (str(document_id),)).fetchall()
    return {"id": str(row[0]), "name": row[1], "hash": row[3], "created_at": row[4], "expires_at": row[5],
            "versions": [{"number": v[0], "hash": v[1], "operation": v[2], "created_at": v[3]} for v in versions],
            "events": [{"kind": e[0], "hash": e[1], "details": e[2], "created_at": e[3]} for e in events]}


class Operation(BaseModel):
    operation: str
    source_version: int = Field(0, ge=0)
    merge_document_ids: list[uuid.UUID] = []
    options: dict = {}


@app.post("/documents/{document_id}/operations", status_code=201)
def operate(document_id: uuid.UUID, request: Operation):
    doc_id = str(document_id)
    with db() as conn:
        doc = doc_row(conn, doc_id)
        path = Path(doc[2]) if request.source_version == 0 else Path(version_row(conn, doc_id, request.source_version)[0])
        sources = [path]
        if request.operation == "merge":
            for other_id in request.merge_document_ids:
                other = doc_row(conn, str(other_id))
                if Path(other[2]).suffix == ".pdf":
                    sources.append(Path(other[2]))
                else:
                    sources.append(Path(version_row(conn, str(other_id), 1)[0]))
        number = conn.execute("SELECT coalesce(max(number),0)+1 FROM versions WHERE document_id=%s", (doc_id,)).fetchone()[0]
        destination = STORE / doc_id / f"v{number}.pdf"
        try:
            metadata = transform(sources, destination, request.operation, request.options)
            digest = sha(destination)
            conn.execute("INSERT INTO versions (document_id,number,path,digest,operation) VALUES (%s,%s,%s,%s,%s)", (doc_id, number, str(destination), digest, request.operation))
            event(conn, doc_id, request.operation, digest, {"version": number, "source_version": request.source_version, "options": request.options})
        except (PdfError, ValueError, IndexError, subprocess.CalledProcessError) as exc:
            destination.unlink(missing_ok=True)
            raise HTTPException(422, str(exc)) from exc
    return {"version": number, "hash": digest, "metadata": metadata}


@app.get("/documents/{document_id}/files/{version}")
def download(document_id: uuid.UUID, version: int):
    with db() as conn:
        row = doc_row(conn, str(document_id))
        path = Path(row[2]) if version == 0 else Path(version_row(conn, str(document_id), version)[0])
        digest = sha(path)
        event(conn, str(document_id), "downloaded", digest, {"version": version})
    return FileResponse(path, media_type="application/pdf" if path.suffix == ".pdf" else "application/octet-stream", filename=safe_name(row[1] if version == 0 else f"{Path(row[1]).stem}-v{version}.pdf"))


@app.get("/documents/{document_id}/preview/{version}/{page}")
def preview(document_id: uuid.UUID, version: int, page: int):
    with db() as conn:
        row = doc_row(conn, str(document_id))
        path = Path(row[2]) if version == 0 else Path(version_row(conn, str(document_id), version)[0])
    if path.suffix != ".pdf":
        raise HTTPException(422, "Preview the converted PDF version.")
    with fitz.open(path) as pdf:
        if page < 1 or page > len(pdf):
            raise HTTPException(404, "Page not found")
        image = pdf[page - 1].get_pixmap(matrix=fitz.Matrix(1.2, 1.2), alpha=False).tobytes("png")
    return Response(image, media_type="image/png")


class Expiry(BaseModel):
    days: int | None = Field(None, ge=1, le=3650)


@app.put("/documents/{document_id}/expiry")
def expiry(document_id: uuid.UUID, value: Expiry):
    with db() as conn:
        row = doc_row(conn, str(document_id))
        expires = datetime.now(timezone.utc) + timedelta(days=value.days) if value.days else None
        conn.execute("UPDATE documents SET expires_at=%s WHERE id=%s", (expires, str(document_id)))
        event(conn, str(document_id), "expiry_changed", row[3], {"expires_at": expires.isoformat() if expires else None})
    return {"expires_at": expires}


@app.delete("/documents/{document_id}")
def delete(document_id: uuid.UUID):
    with db() as conn:
        row = doc_row(conn, str(document_id))
        conn.execute("UPDATE documents SET deleted_at=now() WHERE id=%s", (str(document_id),))
        event(conn, str(document_id), "deleted", row[3])
    shutil.rmtree(STORE / str(document_id), ignore_errors=True)
    return {"deleted": True}


@app.post("/maintenance/expire")
def expire_documents():
    with db() as conn:
        ids = [r[0] for r in conn.execute("SELECT id FROM documents WHERE expires_at < now() AND deleted_at IS NULL").fetchall()]
    for document_id in ids:
        delete(document_id)
    return {"expired": len(ids)}


@app.get("/export")
def export_all():
    output = io.BytesIO()
    with db() as conn, zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        manifest = []
        for row in conn.execute("SELECT id,name,original_path,original_hash FROM documents WHERE deleted_at IS NULL").fetchall():
            document_id = str(row[0])
            manifest.append({"id": document_id, "name": row[1], "original_hash": row[3]})
            archive.write(row[2], f"{document_id}/{Path(row[2]).name}")
            for version in conn.execute("SELECT number,path FROM versions WHERE document_id=%s", (document_id,)).fetchall():
                archive.write(version[1], f"{document_id}/v{version[0]}.pdf")
        archive.writestr("manifest.json", json.dumps(manifest, indent=2))
        events = conn.execute("SELECT document_id,kind,digest,details,created_at FROM events ORDER BY created_at,id").fetchall()
        archive.writestr("events.json", json.dumps([{"document_id": str(e[0]), "kind": e[1], "hash": e[2], "details": e[3], "at": e[4].isoformat()} for e in events], indent=2))
    output.seek(0)
    return StreamingResponse(output, media_type="application/zip", headers={"Content-Disposition": "attachment; filename=papertrail-backup.zip"})


class Invite(BaseModel):
    signer_name: str = Field(min_length=1, max_length=120)
    signer_email: str | None = None
    version: int = Field(ge=0)
    page: int = Field(ge=1)
    x: float = Field(ge=0)
    y: float = Field(ge=0)


@app.post("/documents/{document_id}/invitations", status_code=201)
def invite(document_id: uuid.UUID, value: Invite):
    doc_id = str(document_id)
    with db() as conn:
        row = doc_row(conn, doc_id)
        path = Path(row[2]) if value.version == 0 else Path(version_row(conn, doc_id, value.version)[0])
        with fitz.open(path) as pdf:
            if value.page > len(pdf) or value.x >= pdf[value.page - 1].rect.width or value.y >= pdf[value.page - 1].rect.height:
                raise HTTPException(422, "Signature position is outside the page.")
        token = str(uuid.uuid4())
        conn.execute("INSERT INTO invitations (token,document_id,version,signer_name,signer_email,page,x,y) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                     (token, doc_id, value.version, value.signer_name, value.signer_email, value.page, value.x, value.y))
        event(conn, doc_id, "invited", sha(path), {"token": token, "signer": value.signer_name, "delivery": "link_created_not_sent"})
    return {"token": token, "url": os.getenv("APP_PUBLIC_URL", "http://localhost:3000") + "/sign/" + token, "delivery": "link_created_not_sent"}


@app.get("/invitations/{token}")
def invitation(token: uuid.UUID):
    with db() as conn:
        row = conn.execute("SELECT signer_name,document_id,version,consent_at,sealed_hash FROM invitations WHERE token=%s", (str(token),)).fetchone()
        if not row:
            raise HTTPException(404, "Invitation not found")
        doc = doc_row(conn, str(row[1]))
    return {"signer_name": row[0], "document_name": doc[1], "version": row[2], "consent_at": row[3], "sealed_hash": row[4]}


class Consent(BaseModel):
    agreed: bool
    typed_name: str = Field(min_length=1, max_length=120)


@app.post("/invitations/{token}/seal")
def seal(token: uuid.UUID, value: Consent):
    if not value.agreed:
        raise HTTPException(422, "Consent is required.")
    with db() as conn:
        invite_row = conn.execute("SELECT document_id,version,signer_name,page,x,y,sealed_hash FROM invitations WHERE token=%s FOR UPDATE", (str(token),)).fetchone()
        if not invite_row:
            raise HTTPException(404, "Invitation not found")
        if invite_row[6]:
            raise HTTPException(409, "Invitation already completed.")
        if value.typed_name.strip().casefold() != invite_row[2].strip().casefold():
            raise HTTPException(422, "Typed name must match the invitation.")
        doc_id = str(invite_row[0])
        doc = doc_row(conn, doc_id)
        source = Path(doc[2]) if invite_row[1] == 0 else Path(version_row(conn, doc_id, invite_row[1])[0])
        number = conn.execute("SELECT coalesce(max(number),0)+1 FROM versions WHERE document_id=%s", (doc_id,)).fetchone()[0]
        destination = STORE / doc_id / f"v{number}.pdf"
        with fitz.open(source) as pdf:
            page = pdf[invite_row[3] - 1]
            point = fitz.Point(invite_row[4], invite_row[5])
            page.insert_text(point, value.typed_name.strip(), fontsize=14, color=(0.1, 0.2, 0.4))
            page.insert_text(fitz.Point(point.x, min(point.y + 12, page.rect.height - 2)), "Consented electronic signature", fontsize=7)
            pdf.save(destination)
        digest = sha(destination)
        conn.execute("INSERT INTO versions (document_id,number,path,digest,operation) VALUES (%s,%s,%s,%s,'signature_seal')", (doc_id, number, str(destination), digest))
        conn.execute("UPDATE invitations SET consent_at=now(),sealed_hash=%s WHERE token=%s", (digest, str(token)))
        event(conn, doc_id, "consent_recorded", digest, {"token": str(token), "typed_name": value.typed_name})
        event(conn, doc_id, "signature_sealed", digest, {"version": number, "token": str(token)})
    return {"version": number, "hash": digest}
