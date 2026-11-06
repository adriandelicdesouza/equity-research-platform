"""Content-addressed raw payload archive (Blueprint §3.1, §4.1).

Payloads are stored once per SHA-256 as ``<root>/<provider>/<aa>/<sha256>.zst`` (zstd level
10), written atomically (temporary file + rename), never modified. Reading verifies the
hash, so a corrupted archive cannot be parsed silently.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

import zstandard

LEVEL = 10
_PROVIDER = re.compile(r"^[a-z][a-z0-9_]{1,31}$")


class ArchiveIntegrityError(Exception):
    pass


@dataclass(frozen=True)
class StoredPayload:
    sha256: str
    relative_path: str
    size: int
    stored_size: int
    already_present: bool


def _relative(provider: str, sha: str) -> str:
    if not _PROVIDER.fullmatch(provider):
        raise ValueError(f"Invalid provider code {provider!r}")
    return f"{provider}/{sha[:2]}/{sha}.zst"


def store(root: Path, provider: str, payload: bytes) -> StoredPayload:
    sha = hashlib.sha256(payload).hexdigest()
    rel = _relative(provider, sha)
    target = root / rel
    if target.exists():
        return StoredPayload(sha, rel, len(payload), target.stat().st_size, True)
    target.parent.mkdir(parents=True, exist_ok=True)
    compressed = zstandard.ZstdCompressor(level=LEVEL, write_checksum=True).compress(payload)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(compressed)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o444)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return StoredPayload(sha, rel, len(payload), len(compressed), False)


def load(root: Path, relative_path: str, expected_sha256: str) -> bytes:
    data = zstandard.ZstdDecompressor().decompress((root / relative_path).read_bytes())
    if hashlib.sha256(data).hexdigest() != expected_sha256:
        raise ArchiveIntegrityError(f"Hash mismatch for {relative_path}")
    return data
