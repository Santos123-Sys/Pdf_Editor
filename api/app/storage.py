"""Publish once: no transform can replace an existing original or version."""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import tempfile

from fastapi import HTTPException


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def verified_path(path: str, expected: str) -> Path:
    source = Path(path)
    if not source.is_file() or sha(source) != expected:
        raise HTTPException(409, 'Stored file is missing or its hash changed. Restore a verified backup before continuing.')
    return source


@contextmanager
def staged_pdf(folder: Path):
    # Same filesystem as the final file, for atomic hard-link publication.
    with tempfile.TemporaryDirectory(prefix='.operation-', dir=folder) as temp:
        yield Path(temp) / 'output.pdf'


def publish(staged: Path, destination: Path):
    # link(), unlike replace()/save(), refuses to overwrite a preexisting version.
    staged.chmod(0o400)
    with staged.open('rb') as stream:
        os.fsync(stream.fileno())
    os.link(staged, destination)
    directory = os.open(destination.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
