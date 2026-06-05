"""Content hashing for change detection.

A match is reprocessed only when the bytes of its source file change. Hashing
content rather than mtime keeps incremental decisions stable across copies,
clock skew, and re-checkouts, and it makes the corrected re-export of match 1003
detectable.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

_CHUNK = 1 << 20  # 1 MiB


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()
