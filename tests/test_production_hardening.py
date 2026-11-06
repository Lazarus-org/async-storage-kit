"""Regressions for input safety, failure cleanup, and concurrent resource use."""

from __future__ import annotations

import asyncio
import os
import threading
from pathlib import Path

import pytest

from async_storage_kit import FileSystemStorage, MemoryStorage, SuspiciousFileOperation
from async_storage_kit._async import wait_for_completion
from async_storage_kit.backends import _filesystem_portable as portable
from async_storage_kit.backends import _filesystem_posix as posix
from async_storage_kit.streams import iter_content


@pytest.mark.parametrize(
    "name",
    [
        "a:stream",
        "CON",
        "nul.txt",
        "LPT1.log",
        "com¹",
        "a.",
        "a ",
        "sub/aux",
        "a?b",
        'a"b',
        "CONIN$",
    ],
)
@pytest.mark.asyncio
async def test_filesystem_rejects_aliases_before_consuming_content(tmp_path, name):
    storage = FileSystemStorage(tmp_path / "media")

    async def content():
        pytest.fail("Unsafe input must be rejected before the producer is consumed")
        yield b"unused"

    with pytest.raises(SuspiciousFileOperation):
        await storage.save(name, content())
    with pytest.raises(SuspiciousFileOperation):
        await storage.read(name)
    with pytest.raises(SuspiciousFileOperation):
        storage.path(name)
    assert list(Path(storage.location).iterdir()) == []


@pytest.mark.asyncio
async def test_delete_control_character_rejected(backend):
    with pytest.raises(SuspiciousFileOperation):
        await backend.save("hidden\x7f.txt", b"no")


def test_portable_open_closes_descriptor_if_fstat_fails(tmp_path, monkeypatch):
    root = tmp_path / "media"
    root.mkdir()
    (root / "file").write_bytes(b"data")
    driver = portable.PortableFileIO(str(root), 0o600, 0o700)
    descriptors = []
    original_open = os.open
    original_stat = os.fstat

    def tracked_open(*args, **kwargs):
        fd = original_open(*args, **kwargs)
        descriptors.append(fd)
        return fd

    def fail(_fd):
        raise OSError("injected fstat failure")

    with monkeypatch.context() as patch:
        patch.setattr(os, "open", tracked_open)
        patch.setattr(os, "fstat", fail)
        with pytest.raises(OSError, match="injected"):
            driver._open_regular_file("file")
    assert len(descriptors) == 1
    with pytest.raises(OSError):
        original_stat(descriptors[0])


def test_posix_root_descriptor_closed_on_fstat_error(monkeypatch):
    driver = object.__new__(posix.PosixFileIO)
    driver.location = "unused"
    closed = []
    with monkeypatch.context() as patch:
        patch.setattr(os, "open", lambda *a, **k: 42)
        patch.setattr(
            os, "fstat", lambda _fd: (_ for _ in ()).throw(OSError("stat failed"))
        )
        patch.setattr(os, "close", closed.append)
        with pytest.raises(OSError, match="stat failed"):
            driver._root_fd()
    assert closed == [42]


def test_posix_directory_descriptors_closed_on_permission_error(monkeypatch):
    driver = object.__new__(posix.PosixFileIO)
    driver.directory_permissions_mode = 0o700
    driver._root_fd = lambda: 10
    closed = []
    calls = 0

    def opened(*a, **k):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise FileNotFoundError
        return 20

    def fail(*a):
        raise PermissionError("chmod failed")

    with monkeypatch.context() as patch:
        patch.setattr(os, "open", opened)
        patch.setattr(os, "mkdir", lambda *a, **k: None)
        patch.setattr(os, "fchmod", fail, raising=False)
        patch.setattr(os, "close", closed.append)
        with pytest.raises(PermissionError):
            driver._directory_fd(["new"], create=True)
    assert closed == [20, 10]


@pytest.mark.asyncio
async def test_posix_parent_descriptor_closed_when_cleanup_fails(monkeypatch):
    driver = object.__new__(posix.PosixFileIO)
    driver.file_permissions_mode = None
    driver._parent_fd = lambda *a, **k: 10
    driver._new_temporary = lambda fd: ("temp", 20)
    closed = []

    async def fail_write(*a):
        raise RuntimeError("producer failed")

    def fail_unlink(*a, **k):
        raise PermissionError("cleanup failed")

    with monkeypatch.context() as patch:
        patch.setattr(posix, "write_content", fail_write)
        patch.setattr(os, "close", closed.append)
        patch.setattr(os, "unlink", fail_unlink)
        with pytest.raises(PermissionError):
            await driver.save("file", b"x", max_length=None, on_conflict="error")
    assert closed == [20, 10]


@pytest.mark.asyncio
async def test_readonly_staging_file_removed_after_collision(tmp_path):
    storage = FileSystemStorage(tmp_path / "media", file_permissions_mode=0o400)
    name = await storage.save("file", b"original")
    from async_storage_kit import FileAlreadyExists

    try:
        with pytest.raises(FileAlreadyExists):
            await storage.save(name, b"replacement", on_conflict="error")
        assert await storage.listdir() == ([], [name])
        assert await storage.read(name) == b"original"
    finally:
        Path(storage.path(name)).chmod(0o600)


@pytest.mark.asyncio
async def test_sync_input_read_finishes_before_cancellation_returns():
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    class Reader:
        def read(self, count):
            started.set()
            assert release.wait(5)
            finished.set()
            return b"data"

    task = asyncio.create_task(MemoryStorage().save("file", Reader()))
    assert await asyncio.to_thread(started.wait, 5)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()


@pytest.mark.asyncio
async def test_wait_for_completion_preserves_cancellation_on_worker_error():
    started = asyncio.Event()
    release = asyncio.Event()

    async def operation():
        started.set()
        await release.wait()
        raise OSError("worker failed")

    task = asyncio.create_task(wait_for_completion(operation()))
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_stream_adapters_current_position_async_reader_and_errors():
    from io import BytesIO

    reader = BytesIO(b"skip-body")
    reader.seek(5)
    assert b"".join([part async for part in iter_content(reader, 2)]) == b"body"
    assert not reader.closed

    class AsyncReader:
        async def read(self, count):
            return b""

    assert [part async for part in iter_content(AsyncReader())] == []

    class BadReader:
        def read(self, count):
            return "text"

    with pytest.raises(TypeError):
        _ = [part async for part in iter_content(BadReader())]

    async def bad_stream():
        yield "text"

    with pytest.raises(TypeError):
        _ = [part async for part in iter_content(bad_stream())]
    with pytest.raises(ValueError):
        _ = [part async for part in iter_content(b"data", 0)]


@pytest.mark.asyncio
async def test_portable_rejects_root_replaced_by_symlink(tmp_path, monkeypatch):
    from async_storage_kit.backends import filesystem

    monkeypatch.setattr(filesystem, "is_supported", lambda: False)
    storage = FileSystemStorage(tmp_path / "media")
    root = Path(storage.location)
    root.rmdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        root.symlink_to(outside, target_is_directory=True)
    except OSError:
        root.mkdir()
        pytest.skip("Symlink privileges unavailable")
    with pytest.raises(SuspiciousFileOperation):
        await storage.save("file", b"outside")
    assert not (outside / "file").exists()
