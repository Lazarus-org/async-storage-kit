"""Fault injection for filesystem races, quotas, and descriptor cleanup."""

from __future__ import annotations

import errno
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from async_storage_kit import (
    FileAlreadyExists,
    FileNotFound,
    FileSystemStorage,
    StorageError,
    SuspiciousFileOperation,
    UnsupportedOperation,
)
from async_storage_kit.backends import _filesystem_common as common
from async_storage_kit.backends import _filesystem_portable as portable
from async_storage_kit.backends import _filesystem_posix as posix
from async_storage_kit.utils import safe_join


@pytest.fixture(params=["portable", "posix"])
def driver(request, tmp_path):
    root = tmp_path / "storage"
    root.mkdir()
    if request.param == "posix":
        if not posix.is_supported():
            pytest.skip("Requires POSIX directory descriptors")
        return posix.PosixFileIO(str(root), None, None)
    return portable.PortableFileIO(str(root), None, None)


@pytest.mark.asyncio
async def test_missing_objects_and_directories(driver):
    assert not await driver.exists("missing")
    assert not await driver.delete("missing/file")
    assert not await driver.delete("missing")
    with pytest.raises(FileNotFound):
        await driver.file_stat("missing/file")
    with pytest.raises(FileNotFound):
        await driver.listdir("missing")
    with pytest.raises(FileNotFound):
        _ = [part async for part in driver.iter_chunks("missing/file", 2)]


@pytest.mark.asyncio
async def test_directory_is_not_a_storage_object(driver):
    (Path(driver.location) / "directory").mkdir()
    for operation in (
        driver.file_stat("directory"),
        driver.delete("directory"),
        driver.save("directory", b"data", max_length=None, on_conflict="overwrite"),
    ):
        with pytest.raises(StorageError):
            await operation
    assert await driver.listdir("") == (["directory"], [])


@pytest.mark.asyncio
async def test_staging_collision_exhaustion_leaves_existing_data(driver, monkeypatch):
    token = "a" * 32
    staging = Path(driver.location) / f".async-storage-{token}.tmp"
    staging.write_bytes(b"another upload")
    monkeypatch.setattr(portable.secrets, "token_hex", lambda size: token)
    with pytest.raises(StorageError, match="staging"):
        await driver.save("file", b"data", max_length=None, on_conflict="error")
    assert staging.read_bytes() == b"another upload"
    assert await driver.listdir("") == ([], [])


@pytest.mark.asyncio
async def test_name_collision_exhaustion_cleans_staging(driver, monkeypatch):
    await driver.save("file", b"old", max_length=None, on_conflict="error")
    module = posix if isinstance(driver, posix.PosixFileIO) else portable
    monkeypatch.setattr(module, "alternative_name", lambda *a, **k: "file")
    with pytest.raises(FileAlreadyExists, match="unique"):
        await driver.save("file", b"new", max_length=None, on_conflict="rename")
    assert await driver.listdir("") == ([], ["file"])
    assert b"".join([p async for p in driver.iter_chunks("file", 2)]) == b"old"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [FileNotFoundError, IsADirectoryError])
async def test_delete_race_after_stat(driver, monkeypatch, failure):
    path = Path(driver.location) / "file"
    path.write_bytes(b"old")

    def fail(*args, **kwargs):
        raise failure("concurrent removal or replacement")

    with monkeypatch.context() as patch:
        patch.setattr(os, "unlink", fail)
        if failure is IsADirectoryError:
            expected = (
                StorageError if isinstance(driver, posix.PosixFileIO) else failure
            )
            with pytest.raises(expected):
                await driver.delete("file")
        else:
            assert not await driver.delete("file")
    assert path.read_bytes() == b"old"


def test_write_all_retries_short_writes_and_rejects_zero_progress(monkeypatch):
    written = []

    def short_write(fd, data):
        written.append(bytes(data[:2]))
        return min(2, len(data))

    monkeypatch.setattr(os, "write", short_write)
    common._write_all(42, b"abcde")
    assert b"".join(written) == b"abcde"
    monkeypatch.setattr(os, "write", lambda *a: 0)
    with pytest.raises(OSError):
        common._write_all(42, b"data")


@pytest.mark.asyncio
async def test_filesystem_time_capabilities(tmp_path, monkeypatch):
    storage = FileSystemStorage(tmp_path)
    await storage.save("file", b"data")
    assert (await storage.get_accessed_time("file")).tzinfo is not None

    async def without_birth(name):
        return SimpleNamespace()

    async def with_birth(name):
        return SimpleNamespace(st_birthtime=1_700_000_000)

    monkeypatch.setattr(storage, "_stat", without_birth)
    with pytest.raises(UnsupportedOperation, match="birth"):
        await storage.get_created_time("file")
    monkeypatch.setattr(storage, "_stat", with_birth)
    assert (await storage.get_created_time("file")).timestamp() == 1_700_000_000


def test_portable_validation_errors_and_reparse_points(tmp_path, monkeypatch):
    def fail(self):
        raise PermissionError("cannot inspect")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "is_symlink", fail)
        with pytest.raises(SuspiciousFileOperation, match="validate"):
            portable.validated_path(str(tmp_path), "file")
    if os.name == "nt":
        with monkeypatch.context() as patch:
            patch.setattr(
                os, "lstat", lambda *a, **k: SimpleNamespace(st_file_attributes=1024)
            )
            patch.setattr(Path, "exists", lambda *a: True)
            with pytest.raises(SuspiciousFileOperation, match="reparse"):
                portable.validated_path(str(tmp_path), "file")


def test_portable_parent_created_concurrently(tmp_path, monkeypatch):
    driver = portable.PortableFileIO(str(tmp_path), None, None)
    mkdir = Path.mkdir

    def raced_mkdir(path, *args, **kwargs):
        mkdir(path, *args, **kwargs)
        raise FileExistsError("another process created the folder")

    monkeypatch.setattr(Path, "mkdir", raced_mkdir)
    assert driver._ensure_parent("folder/file") == tmp_path / "folder" / "file"


def test_portable_parent_without_permission_override(tmp_path):
    driver = portable.PortableFileIO(str(tmp_path), None, None)
    assert driver._ensure_parent("folder/file") == tmp_path / "folder" / "file"
    assert (tmp_path / "folder").is_dir()


@pytest.mark.skipif(os.name != "posix", reason="Requires POSIX symlinks and FIFOs")
@pytest.mark.asyncio
async def test_portable_special_files_and_links(tmp_path):
    driver = portable.PortableFileIO(str(tmp_path), None, None)
    os.mkfifo(tmp_path / "fifo")
    with pytest.raises(StorageError, match="regular"):
        driver._open_regular_file("fifo")
    with pytest.raises(StorageError, match="non-file"):
        await driver.listdir("")
    (tmp_path / "fifo").unlink()
    (tmp_path / "link").symlink_to("missing")
    with pytest.raises(SuspiciousFileOperation):
        await driver.listdir("")
    (tmp_path / "escape").symlink_to(tmp_path.parent)
    with pytest.raises(SuspiciousFileOperation):
        safe_join(tmp_path, "escape")


@pytest.mark.skipif(os.name != "posix", reason="Requires POSIX directory descriptors")
def test_posix_root_open_errors(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    driver = posix.PosixFileIO(str(root), None, None)
    root.rmdir()
    with pytest.raises(FileNotFoundError):
        driver._root_fd()
    root.write_bytes(b"not a directory")
    with pytest.raises(SuspiciousFileOperation, match="root"):
        driver._root_fd()


@pytest.mark.skipif(os.name != "posix", reason="Requires POSIX directory descriptors")
@pytest.mark.parametrize("unsafe", [False, True])
def test_posix_directory_creation_races(tmp_path, monkeypatch, unsafe):
    driver = posix.PosixFileIO(str(tmp_path), None, None)
    mkdir = os.mkdir

    def raced_mkdir(name, mode, *, dir_fd):
        if unsafe:
            os.symlink("outside", name, dir_fd=dir_fd)
        else:
            mkdir(name, mode, dir_fd=dir_fd)
        raise FileExistsError("concurrent creation")

    monkeypatch.setattr(os, "mkdir", raced_mkdir)
    if unsafe:
        with pytest.raises(SuspiciousFileOperation):
            driver._directory_fd(["folder"], create=True)
    else:
        os.close(driver._directory_fd(["folder"], create=True))


@pytest.mark.skipif(os.name != "posix", reason="Requires POSIX directory descriptors")
def test_posix_directory_permission_error_preserved(tmp_path, monkeypatch):
    driver = posix.PosixFileIO(str(tmp_path), None, None)
    original = os.open

    def fail(name, *args, **kwargs):
        if name == "folder":
            raise PermissionError(errno.EACCES, "denied")
        return original(name, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, "open", fail)
        with pytest.raises(PermissionError):
            driver._directory_fd(["folder"], create=False)


@pytest.mark.skipif(os.name != "posix", reason="Requires POSIX directory descriptors")
def test_posix_new_directory_open_permission_error(tmp_path, monkeypatch):
    driver = posix.PosixFileIO(str(tmp_path), None, None)
    original = os.open
    attempts = 0

    def fail(name, *args, **kwargs):
        nonlocal attempts
        if name == "folder":
            attempts += 1
            if attempts == 2:
                raise PermissionError(errno.EACCES, "denied after creation")
        return original(name, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, "open", fail)
        with pytest.raises(PermissionError, match="after creation"):
            driver._directory_fd(["folder"], create=True)
    assert (tmp_path / "folder").is_dir()


@pytest.mark.skipif(os.name != "posix", reason="Requires POSIX directory descriptors")
@pytest.mark.asyncio
async def test_posix_listing_disappearing_entry_and_special_file(tmp_path, monkeypatch):
    driver = posix.PosixFileIO(str(tmp_path), None, None)
    (tmp_path / "file").write_bytes(b"x")
    original = os.stat

    def disappearing(name, *args, **kwargs):
        if name == "file":
            raise FileNotFoundError
        return original(name, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, "stat", disappearing)
        assert await driver.listdir("") == ([], [])
    os.mkfifo(tmp_path / "fifo")
    with pytest.raises(StorageError, match="non-file"):
        await driver.listdir("")
