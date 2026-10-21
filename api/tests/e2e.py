"""End-to-end check against running Compose API: upload, version, download, audit."""
import io
import os
import time
import zipfile

import fitz
import httpx

base = os.getenv("TEST_API_URL", "http://localhost:8000")
pdf = fitz.open()
pdf.new_page().insert_text((70, 90), "e2e source")
source = pdf.tobytes()
pdf.close()
password = os.getenv("TEST_WORKSPACE_PASSWORD")
with httpx.Client(base_url=base, timeout=180, auth=("workspace", password) if password else None) as client:
    health = None
    for _ in range(30):
        try:
            health = client.get("/health")
            if health.status_code == 200:
                break
        except httpx.RequestError:
            pass
        time.sleep(1)
    assert health is not None, "API did not start"
    health.raise_for_status()
    uploaded = client.post("/documents", files={"file": ("happy.pdf", source, "application/pdf")})
    uploaded.raise_for_status()
    document_id = uploaded.json()["id"]
    transformed = client.post(f"/documents/{document_id}/operations", json={"operation": "rotate", "options": {"degrees": 90}})
    transformed.raise_for_status()
    version = transformed.json()["version"]
    downloaded = client.get(f"/documents/{document_id}/files/{version}")
    downloaded.raise_for_status()
    with fitz.open(stream=downloaded.content, filetype="pdf") as output:
        assert output[0].rotation == 90
        assert "e2e source" in output[0].get_text()
    detail = client.get(f"/documents/{document_id}").json()
    assert sorted(e["kind"] for e in detail["events"]) == ["downloaded", "rotate", "uploaded"]
    assert len(detail["versions"]) == 1
    invite = client.post(f"/documents/{document_id}/invitations", json={"signer_name": "Jane Doe", "version": version, "page": 1, "x": 80, "y": 120})
    invite.raise_for_status()
    token = invite.json()["token"]
    assert invite.json()["delivery"] == "link_created_not_sent"
    consent = client.post(f"/invitations/{token}/seal", json={"agreed": True, "typed_name": "Jane Doe"})
    consent.raise_for_status()
    sealed = client.get(f"/documents/{document_id}/files/{consent.json()['version']}")
    sealed.raise_for_status()
    with fitz.open(stream=sealed.content, filetype="pdf") as signed:
        assert "Jane Doe" in signed[0].get_text()
    assert consent.json()["hash"] == client.get(f"/invitations/{token}").json()["sealed_hash"]
    backup = client.get("/export")
    backup.raise_for_status()
    assert backup.content[:2] == b"PK"
    office_bytes = io.BytesIO()
    with zipfile.ZipFile(office_bytes, "w") as odt:
        odt.writestr("mimetype", "application/vnd.oasis.opendocument.text", compress_type=zipfile.ZIP_STORED)
        odt.writestr("content.xml", """<?xml version="1.0" encoding="UTF-8"?>
          <office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
           xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" office:version="1.2">
          <office:body><office:text><text:p>Office conversion check</text:p></office:text></office:body>
          </office:document-content>""")
        odt.writestr("META-INF/manifest.xml", """<?xml version="1.0" encoding="UTF-8"?>
          <manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"
          manifest:version="1.2"><manifest:file-entry manifest:full-path="/"
          manifest:media-type="application/vnd.oasis.opendocument.text"/>
          <manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/></manifest:manifest>""")
    office = client.post("/documents", files={"file": ("office-check.odt", office_bytes.getvalue(), "application/vnd.oasis.opendocument.text")})
    office.raise_for_status()
    assert office.json()["latest_version"] == 1
    converted = client.get(f"/documents/{office.json()['id']}/files/1")
    converted.raise_for_status()
    with fitz.open(stream=converted.content, filetype="pdf") as made:
        assert "Office conversion check" in made[0].get_text()
    expires = client.put(f"/documents/{office.json()['id']}/expiry", json={"days": 1})
    expires.raise_for_status()
    assert expires.json()["expires_at"]
    deleted = client.delete(f"/documents/{office.json()['id']}")
    deleted.raise_for_status()
    assert client.get(f"/documents/{office.json()['id']}").status_code == 404
    print(f"PASS: upload -> rotate -> download -> audit -> consent -> seal -> export; Office -> PDF -> expiry -> delete ({document_id})")

