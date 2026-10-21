"""Read-only verification of a restored PostgreSQL + /data backup."""
import hashlib
from pathlib import Path
import os
import psycopg

with psycopg.connect(os.environ['DATABASE_URL']) as conn:
    checked = 0
    for path, digest in conn.execute('SELECT original_path,original_hash FROM documents WHERE deleted_at IS NULL').fetchall():
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest, path
        checked += 1
    for path, digest in conn.execute('SELECT v.path,v.digest FROM versions v JOIN documents d ON d.id=v.document_id WHERE d.deleted_at IS NULL').fetchall():
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest, path
        checked += 1
    assert checked > 0, 'Restore test needs at least one document'
    events = conn.execute('SELECT count(*) FROM events').fetchone()[0]
    assert events > 0
    print(f'PASS: restored database and isolated volume; verified {checked} file hashes and {events} audit events')
