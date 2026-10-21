"""Integration tests require a dedicated PostgreSQL database."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
import hashlib
import io
import json
import os
import zipfile

import fitz
from fastapi.testclient import TestClient
import pytest
from app import main

pytestmark = pytest.mark.skipif(not os.getenv('DATABASE_URL'), reason='Set DATABASE_URL to a dedicated PostgreSQL test database')


@pytest.fixture(scope='module')
def client(tmp_path_factory):
    main.STORE = tmp_path_factory.mktemp('store')
    with TestClient(main.app) as client:
        yield client


def upload(client, labels=('alpha', 'bravo')):
    with fitz.open() as pdf:
        for label in labels:
            pdf.new_page().insert_text((70,90), label)
        data = pdf.tobytes()
    response = client.post('/documents', files={'file': ('../../personal.pdf', data, 'application/pdf')})
    assert response.status_code == 201, response.text
    return response.json()['id'], data


def invite(client, doc_id, version=0, **kwargs):
    response = client.post(f'/documents/{doc_id}/invitations', json={'signer_name': 'Jane Doe', 'version': version, 'page': 1, 'x':80, 'y':120, **kwargs})
    assert response.status_code == 201, response.text
    return response.json()['token']


def test_upload_transform_preview_export_and_original_hash(client):
    doc_id, original = upload(client)
    assert client.get(f'/documents/{doc_id}').json()['name'] == 'personal.pdf'
    meta = client.get(f'/documents/{doc_id}/metadata/0').json()
    assert meta['pages'] == 2 and meta['dimensions'][0]['width'] > 0
    assert client.get(f'/documents/{doc_id}/preview/0/2').headers['content-type'] == 'image/png'
    assert client.get(f'/documents/{doc_id}/preview/0/3').status_code == 404
    made = client.post(f'/documents/{doc_id}/operations', json={'operation':'rotate','options':{'degrees':90}})
    assert made.status_code == 201, made.text
    download = client.get(f'/documents/{doc_id}/files/1')
    assert hashlib.sha256(download.content).hexdigest() == made.json()['hash']
    assert client.get(f'/documents/{doc_id}/files/0').content == original
    assert download.headers['cache-control'] == 'no-store'
    with zipfile.ZipFile(io.BytesIO(client.get('/export').content)) as archive:
        doc = next(d for d in json.loads(archive.read('manifest.json')) if d['id'] == doc_id)
        assert doc['original_hash'] == hashlib.sha256(original).hexdigest()
        assert doc['versions'][0]['hash'] == made.json()['hash']
        assert archive.read(f'{doc_id}/original.pdf') == original


def test_concurrent_versions_have_distinct_files_and_audited_inputs(client):
    doc_id, original = upload(client)
    def rotate(degrees):
        return client.post(f'/documents/{doc_id}/operations', json={'operation':'rotate','options':{'degrees':degrees}})
    with ThreadPoolExecutor(3) as pool:
        results = list(pool.map(rotate, [90,180,270]))
    assert [r.status_code for r in results] == [201]*3
    assert sorted(r.json()['version'] for r in results) == [1,2,3]
    for response, degrees in zip(results, [90,180,270]):
        result = response.json()
        file = client.get(f'/documents/{doc_id}/files/{result["version"]}')
        assert hashlib.sha256(file.content).hexdigest() == result['hash']
        with fitz.open(stream=file.content, filetype='pdf') as pdf:
            assert pdf[0].rotation == degrees
    assert client.get(f'/documents/{doc_id}/files/0').content == original
    events = client.get(f'/documents/{doc_id}').json()['events']
    assert all(e['details']['inputs'][0]['hash'] == hashlib.sha256(original).hexdigest() for e in events if e['kind']=='rotate')


def test_signer_review_consent_and_replay_protection(client):
    doc_id, source = upload(client)
    token = invite(client, doc_id)
    assert client.get(f'/invitations/{token}').json()['metadata']['pages'] == 2
    assert client.get(f'/invitations/{token}/file').content == source
    assert client.get(f'/invitations/{token}/preview/2').status_code == 200
    assert client.post(f'/invitations/{token}/seal', json={'agreed':False,'typed_name':'Jane Doe'}).status_code == 422
    assert client.post(f'/invitations/{token}/seal', json={'agreed':True,'typed_name':'Not Jane'}).status_code == 422
    sealed = client.post(f'/invitations/{token}/seal', json={'agreed':True,'typed_name':'Jane Doe'})
    assert sealed.status_code == 200, sealed.text
    final = client.get(f'/invitations/{token}/file').content
    assert hashlib.sha256(final).hexdigest() == sealed.json()['hash']
    with fitz.open(stream=final,filetype='pdf') as pdf:
        assert 'Jane Doe' in pdf[0].get_text()
    assert client.post(f'/invitations/{token}/seal', json={'agreed':True,'typed_name':'Jane Doe'}).status_code == 409
    assert client.get(f'/documents/{doc_id}/files/0').content == source


def test_pending_signatures_cannot_discard_earlier_signatures(client):
    doc_id, _ = upload(client)
    first, stale = invite(client, doc_id), invite(client, doc_id)
    assert client.post(f'/invitations/{first}/seal', json={'agreed':True,'typed_name':'Jane Doe'}).status_code == 200
    assert client.post(f'/invitations/{stale}/seal', json={'agreed':True,'typed_name':'Jane Doe'}).status_code == 409
    current = invite(client, doc_id, 1, y=200)
    assert client.post(f'/invitations/{current}/seal', json={'agreed':True,'typed_name':'Jane Doe'}).status_code == 200
    with fitz.open(stream=client.get(f'/documents/{doc_id}/files/2').content,filetype='pdf') as pdf:
        assert pdf[0].get_text().count('Jane Doe') == 2


def test_expiry_revocation_and_deletion_block_links(client):
    doc_id, _ = upload(client)
    token = invite(client, doc_id)
    assert client.delete(f'/documents/{doc_id}/invitations/{token}').status_code == 200
    assert client.get(f'/invitations/{token}/file').status_code == 410
    token = invite(client, doc_id)
    past = datetime.now(timezone.utc)-timedelta(seconds=1)
    with main.db() as conn:
        conn.execute('UPDATE invitations SET expires_at=%s WHERE token=%s', (past, token))
    assert client.get(f'/invitations/{token}').status_code == 410
    with main.db() as conn:
        conn.execute('UPDATE documents SET expires_at=%s WHERE id=%s', (past, doc_id))
    assert client.get(f'/documents/{doc_id}/files/0').status_code == 410
    assert all(d['id'] != doc_id for d in client.get('/documents').json())
    assert client.post('/maintenance/expire').status_code == 200
    assert not (main.STORE / doc_id).exists()
    assert client.get(f'/documents/{doc_id}').status_code == 404


def test_audit_log_rejects_sql_mutations(client):
    import psycopg
    doc_id, _ = upload(client)
    for sql in ["UPDATE events SET kind='changed' WHERE document_id=%s", 'DELETE FROM events WHERE document_id=%s']:
        with pytest.raises(psycopg.errors.RaiseException, match='append-only'):
            with main.db() as conn:
                conn.execute(sql, (doc_id,))
    with pytest.raises(psycopg.errors.RaiseException, match='append-only'):
        with main.db() as conn:
            conn.execute('TRUNCATE events')


def test_hash_tampering_and_orphan_outputs_fail_closed(client):
    doc_id, _ = upload(client)
    orphan = main.STORE / doc_id / 'v1.pdf'
    orphan.write_bytes(b'preexisting output')
    response = client.post(f'/documents/{doc_id}/operations', json={'operation':'compress'})
    assert response.status_code == 409, response.text
    assert orphan.read_bytes() == b'preexisting output'
    original = main.STORE / doc_id / 'original.pdf'
    original.chmod(0o600)
    original.write_bytes(b'tampered')
    assert client.get(f'/documents/{doc_id}/files/0').status_code == 409
    # Restore and remove the intentionally corrupted fixture before other exports.
    client.delete(f'/documents/{doc_id}')


def test_invalid_inputs_leave_no_output_versions(client):
    for name, data in [('bad.pdf', b'not pdf'),('empty.pdf', b''),('bad.exe', b'hello')]:
        assert client.post('/documents',files={'file':(name,data)}).status_code == 422
    doc_id, _ = upload(client)
    for body in [{'operation':'redact','options':{'x0':'invalid'}},{'operation':'unsupported'},{'operation':'rotate','options':{'degrees':20}}]:
        assert client.post(f'/documents/{doc_id}/operations',json=body).status_code == 422
    assert client.get(f'/documents/{doc_id}').json()['versions'] == []
    assert not list((main.STORE/doc_id).glob('v*.pdf'))
    assert not list((main.STORE/doc_id).glob('.operation-*'))


def test_deletion_failure_retries_without_restoring_access(client, monkeypatch):
    doc_id, _ = upload(client)
    remove = main.remove_bytes
    def fail(_):
        raise PermissionError('busy volume')
    monkeypatch.setattr(main,'remove_bytes',fail)
    deleted = client.delete(f'/documents/{doc_id}')
    assert deleted.status_code == 202 and deleted.json()['cleanup_pending']
    assert client.get(f'/documents/{doc_id}/files/0').status_code == 404
    monkeypatch.setattr(main,'remove_bytes',remove)
    client.post('/maintenance/expire')
    assert not (main.STORE / doc_id).exists()
