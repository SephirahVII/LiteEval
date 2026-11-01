"""Content-addressed immutable resources shared by releases and submissions."""
import hashlib
import os
from pathlib import Path
import tempfile


class BlobStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, data):
        digest = hashlib.sha256(data).hexdigest()
        target = self.root / digest
        if not target.exists():
            fd, temporary = tempfile.mkstemp(dir=self.root)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(data)
                os.replace(temporary, target)
            finally:
                Path(temporary).unlink(missing_ok=True)
        return digest

    def get(self, digest):
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            raise ValueError('Invalid blob digest')
        data = (self.root / digest).read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError('Blob integrity mismatch')
        return data
