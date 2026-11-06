from __future__ import annotations

from pathlib import Path

import pytest

from async_storage_kit import FileSystemStorage, MemoryStorage, SuspiciousFileOperation
from async_storage_kit.utils import safe_join


@pytest.mark.parametrize(
    "name",
    [
        "../bad",
        "child/../bad",
        "/etc/passwd",
        "C:\\Windows\\test",
        "\\\\server\\share",
        "a//b",
        "a/./b",
        "a/",
        ".",
        "..",
        "a\x00b",
        "a\nb",
    ],
)
@pytest.mark.asyncio
async def test_reject_unsafe_keys(name, tmp_path):
    for storage in (MemoryStorage(), FileSystemStorage(tmp_path / "root")):
        with pytest.raises(SuspiciousFileOperation):
            await storage.save(name, b"nope")
        with pytest.raises(SuspiciousFileOperation):
            await storage.exists(name)


def test_sibling_directory_escape_is_rejected(tmp_path):
    root = tmp_path / "storage"
    sibling = tmp_path / "storage-private"
    root.mkdir()
    sibling.mkdir()
    with pytest.raises(SuspiciousFileOperation):
        safe_join(root, "../storage-private/private.txt")
    assert safe_join(root, "dir/file.txt").startswith(str(root))


def test_symlink_escape_is_rejected(tmp_path):
    root = tmp_path / "media"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    link = root / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip("Symlink creation requires appropriate privileges")
    with pytest.raises(SuspiciousFileOperation):
        safe_join(root, "escape/secret.txt")


@pytest.mark.asyncio
async def test_max_length_and_validation(tmp_path):
    storage = FileSystemStorage(tmp_path / "media")
    with pytest.raises(SuspiciousFileOperation):
        await storage.save("verylong.txt", b"a", max_length=4)
    with pytest.raises(ValueError):
        await storage.save("ok.txt", b"a", on_conflict="unknown")


@pytest.mark.asyncio
async def test_filesystem_rejects_symlink_to_outside(tmp_path):
    storage = FileSystemStorage(tmp_path / "media")
    outside = tmp_path / "outside"
    outside.mkdir()
    link = Path(storage.location) / "outside-link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip("Symlink creation requires appropriate privileges")
    with pytest.raises(SuspiciousFileOperation):
        await storage.save("outside-link/file.txt", b"nope")
    assert not (outside / "file.txt").exists()
