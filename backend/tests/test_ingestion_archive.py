import stat
from pathlib import Path

import pytest
from erp_backend.ingestion import archive


def test_store_and_load_roundtrip_is_content_addressed(tmp_path: Path) -> None:
    payload = b'{"facts": {"us-gaap": {}}}' * 50
    first = archive.store(tmp_path, "sec_edgar", payload)
    again = archive.store(tmp_path, "sec_edgar", payload)
    assert not first.already_present and again.already_present
    assert first.relative_path == f"sec_edgar/{first.sha256[:2]}/{first.sha256}.zst"
    assert first.stored_size < first.size
    assert archive.load(tmp_path, first.relative_path, first.sha256) == payload
    mode = (tmp_path / first.relative_path).stat().st_mode
    assert not mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)  # read-only on disk


def test_tampered_archive_is_detected(tmp_path: Path) -> None:
    stored = archive.store(tmp_path, "sec_edgar", b"original")
    other = archive.store(tmp_path, "sec_edgar", b"different")
    with pytest.raises(archive.ArchiveIntegrityError):
        archive.load(tmp_path, other.relative_path, stored.sha256)


def test_provider_code_is_validated(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        archive.store(tmp_path, "../escape", b"x")
