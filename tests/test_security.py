from __future__ import annotations

import os
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest

from async_storage_kit import FileSystemStorage, MemoryStorage, SuspiciousFileOperation
from async_storage_kit.utils import _comparison_path, safe_join


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (PurePosixPath("/storage/file"), PurePosixPath("/storage/file")),
        (PureWindowsPath(r"C:\storage\file"), PureWindowsPath(r"C:\storage\file")),
        (PureWindowsPath(r"\\?\C:\storage\file"), PureWindowsPath(r"C:\storage\file")),
        (
            PureWindowsPath(r"\\?\UNC\server\share\file"),
            PureWindowsPath(r"\\server\share\file"),
        ),
        (
            PureWindowsPath(r"\\?\unc\server\share\file"),
            PureWindowsPath(r"\\server\share\file"),
        ),
        (
            PureWindowsPath(r"\\?\Volume{abc}\file"),
            PureWindowsPath(r"\\?\Volume{abc}\file"),
        ),
    ],
)
def test_containment_comparison_normalizes_only_drive_and_unc_aliases(path, expected):
    assert _comparison_path(path) == expected


@pytest.mark.skipif(os.name != "nt", reason="Windows path resolution only")
@pytest.mark.parametrize(
    ("root", "candidate"),
    [
        (r"C:\storage", r"\\?\C:\storage\file.bin"),
        (r"\\?\C:\storage", r"C:\storage\file.bin"),
        (r"\\?\C:\storage", r"\\?\C:\storage\file.bin"),
        (r"\\server\share\storage", r"\\?\UNC\server\share\storage\file.bin"),
        (r"\\?\UNC\server\share\storage", r"\\server\share\storage\file.bin"),
    ],
)
@pytest.mark.parametrize("escape", [False, True])
def test_safe_join_handles_mixed_windows_namespaces(
    monkeypatch, root, candidate, escape
):
    if escape:
        candidate = candidate.replace("storage", "storage-private")
    resolved = iter([Path(root), Path(candidate)])
    with monkeypatch.context() as patch:
        patch.setattr(Path, "resolve", lambda self: next(resolved))
        if escape:
            with pytest.raises(SuspiciousFileOperation, match="escapes"):
                safe_join(root, "file.bin")
        else:
            assert safe_join(root, "file.bin") == str(Path(candidate))


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
