"""Adversarial and concurrency tests for Phase 2 filesystem semantics."""

from __future__ import annotations

import asyncio
import multiprocessing
import os
import stat
import threading
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pytest

from async_storage_kit import (
    FileAlreadyExists,
    FileSystemStorage,
    SuspiciousFileOperation,
)
from async_storage_kit.backends import _filesystem_common


def _process_save(root: str, number: int) -> tuple[str, bytes]:
    async def run() -> tuple[str, bytes]:
        storage = FileSystemStorage(root)
        payload = f"from process {number}".encode()
        return await storage.save("parallel/shared.dat", payload), payload

    return asyncio.run(run())


@pytest.mark.asyncio
async def test_save_publishes_complete_file_only(tmp_path: Path) -> None:
    storage = FileSystemStorage(tmp_path / "media")
    started = asyncio.Event()
    release = asyncio.Event()

    async def stream():
        yield b"partial-"
        started.set()
        await release.wait()
        yield b"complete"

    task = asyncio.create_task(storage.save("nested/file.dat", stream()))
    await started.wait()
    assert not await storage.exists("nested/file.dat")
    assert await storage.listdir("nested") == ([], [])
    release.set()
    assert await task == "nested/file.dat"
    assert await storage.read("nested/file.dat") == b"partial-complete"
    assert list((tmp_path / "media" / "nested").glob(".async-storage-*.tmp")) == []


@pytest.mark.asyncio
async def test_overwrite_keeps_old_file_visible_until_commit(tmp_path: Path) -> None:
    storage = FileSystemStorage(tmp_path / "media")
    await storage.save("report.txt", b"original")
    started = asyncio.Event()
    release = asyncio.Event()

    async def stream():
        yield b"replacement-"
        started.set()
        await release.wait()
        yield b"complete"

    task = asyncio.create_task(
        storage.save("report.txt", stream(), on_conflict="overwrite")
    )
    await started.wait()
    assert await storage.read("report.txt") == b"original"
    assert await storage.listdir() == ([], ["report.txt"])
    release.set()
    assert await task == "report.txt"
    assert await storage.read("report.txt") == b"replacement-complete"


@pytest.mark.asyncio
async def test_cancelled_upload_removes_temporary_file(tmp_path: Path) -> None:
    storage = FileSystemStorage(tmp_path / "media")
    started = asyncio.Event()

    async def stream():
        yield b"first"
        started.set()
        await asyncio.Event().wait()
        yield b"unreachable"

    task = asyncio.create_task(storage.save("upload.bin", stream()))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not await storage.exists("upload.bin")
    assert await storage.listdir() == ([], [])
    assert not list(Path(storage.location).glob(".async-storage-*.tmp"))


@pytest.mark.asyncio
async def test_cancellation_during_thread_write_waits_for_fd(
    tmp_path: Path, monkeypatch
) -> None:
    storage = FileSystemStorage(tmp_path / "media")
    write_started = threading.Event()
    allow_write_to_finish = threading.Event()
    original = _filesystem_common._write_all

    def blocked_write(fd: int, data: bytes) -> None:
        write_started.set()
        assert allow_write_to_finish.wait(timeout=5)
        original(fd, data)

    monkeypatch.setattr(_filesystem_common, "_write_all", blocked_write)
    task = asyncio.create_task(storage.save("cancelled.txt", b"hello"))
    assert await asyncio.to_thread(write_started.wait, 5)
    task.cancel()
    allow_write_to_finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not await storage.exists("cancelled.txt")
    assert not list(Path(storage.location).glob(".async-storage-*.tmp"))


@pytest.mark.asyncio
async def test_collision_consumes_stream_once(tmp_path: Path) -> None:
    storage = FileSystemStorage(tmp_path / "media")
    await storage.save("same.bin", b"original")
    calls = 0

    async def stream():
        nonlocal calls
        calls += 1
        yield b"new"

    created = await storage.save("same.bin", stream())
    assert created != "same.bin"
    assert calls == 1
    assert await storage.read(created) == b"new"
    assert await storage.read("same.bin") == b"original"


@pytest.mark.asyncio
async def test_existing_symlink_is_rejected_for_every_operation(tmp_path: Path) -> None:
    storage = FileSystemStorage(tmp_path / "media")
    secret = tmp_path / "secret.txt"
    secret.write_bytes(b"keep secret")
    link = Path(storage.location) / "link.txt"
    try:
        link.symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink privileges unavailable")

    for operation in (
        storage.read("link.txt"),
        storage.exists("link.txt"),
        storage.size("link.txt"),
        storage.delete("link.txt"),
        storage.save("link.txt", b"attack", on_conflict="overwrite"),
        storage.listdir(),
    ):
        with pytest.raises(SuspiciousFileOperation):
            await operation
    assert secret.read_bytes() == b"keep secret"
    assert link.is_symlink()


@pytest.mark.asyncio
async def test_symlink_to_inside_root_is_also_rejected(tmp_path: Path) -> None:
    storage = FileSystemStorage(tmp_path / "media")
    await storage.save("original.txt", b"original")
    link = Path(storage.location) / "alias.txt"
    try:
        link.symlink_to("original.txt")
    except (OSError, NotImplementedError):
        pytest.skip("Symlink privileges unavailable")
    with pytest.raises(SuspiciousFileOperation):
        await storage.read("alias.txt")
    with pytest.raises(SuspiciousFileOperation):
        storage.path("alias.txt")
    assert await storage.read("original.txt") == b"original"


@pytest.mark.asyncio
@pytest.mark.parametrize("portable_driver", [False, True])
async def test_dangling_symlink_cannot_be_overwritten(
    tmp_path: Path, monkeypatch, portable_driver: bool
) -> None:
    if portable_driver:
        from async_storage_kit.backends import filesystem

        monkeypatch.setattr(filesystem, "is_supported", lambda: False)
    storage = FileSystemStorage(tmp_path / "media")
    link = Path(storage.location) / "dangling.txt"
    try:
        link.symlink_to(tmp_path / "does-not-exist")
    except (OSError, NotImplementedError):
        pytest.skip("Symlink privileges unavailable")
    with pytest.raises(SuspiciousFileOperation):
        await storage.save("dangling.txt", b"not allowed", on_conflict="overwrite")
    assert link.is_symlink()
    # POSIX no-clobber publication encounters the existing directory entry;
    # the portable driver rejects links during its earlier path validation.
    expected_error = (
        FileAlreadyExists
        if storage.symlink_race_protection
        else SuspiciousFileOperation
    )
    with pytest.raises(expected_error):
        await storage.save("dangling.txt", b"no", on_conflict="error")
    assert link.is_symlink()
    assert not (tmp_path / "does-not-exist").exists()
    assert list(Path(storage.location).iterdir()) == [link]


@pytest.mark.asyncio
async def test_symlink_parent_refused_even_when_it_points_inside(
    tmp_path: Path,
) -> None:
    storage = FileSystemStorage(tmp_path / "media")
    root = Path(storage.location)
    (root / "real").mkdir()
    link = root / "alias"
    try:
        link.symlink_to(root / "real", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink privileges unavailable")
    with pytest.raises(SuspiciousFileOperation):
        await storage.save("alias/attack.txt", b"not allowed")
    assert not (root / "real" / "attack.txt").exists()


@pytest.mark.asyncio
async def test_root_replacement_is_detected_on_posix(tmp_path: Path) -> None:
    storage = FileSystemStorage(tmp_path / "media")
    if not storage.symlink_race_protection:
        pytest.skip("POSIX dirfd implementation only")
    root = Path(storage.location)
    root.rename(tmp_path / "moved-root")
    root.mkdir()
    with pytest.raises(SuspiciousFileOperation, match="root has been replaced"):
        await storage.save("file", b"refuse")
    assert not (root / "file").exists()
    assert not (tmp_path / "moved-root" / "file").exists()


@pytest.mark.asyncio
async def test_directory_swap_during_upload_cannot_follow_symlink(
    tmp_path: Path,
) -> None:
    storage = FileSystemStorage(tmp_path / "media")
    if not storage.symlink_race_protection:
        pytest.skip("POSIX dirfd implementation only")
    root = Path(storage.location)
    outside = tmp_path / "outside"
    outside.mkdir()
    started = asyncio.Event()
    release = asyncio.Event()

    async def stream():
        yield b"a"
        started.set()
        await release.wait()
        yield b"b"

    task = asyncio.create_task(storage.save("parent/file", stream()))
    await started.wait()
    (root / "parent").rename(root / "renamed")
    try:
        (root / "parent").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        release.set()
        await task
        pytest.skip("Symlink privileges unavailable")
    release.set()
    await task
    # The opened descriptor stays anchored to the original folder.
    assert not (outside / "file").exists()
    assert (root / "renamed" / "file").read_bytes() == b"ab"
    with pytest.raises(SuspiciousFileOperation):
        await storage.read("parent/file")


@pytest.mark.asyncio
async def test_invalid_permission_modes_rejected(tmp_path: Path) -> None:
    for mode in (-1, 0o1000, "644"):
        with pytest.raises(ValueError):
            FileSystemStorage(tmp_path / "root", file_permissions_mode=mode)


@pytest.mark.asyncio
async def test_exact_permissions_do_not_modify_umask(tmp_path: Path) -> None:
    if os.name != "posix":
        pytest.skip("POSIX permission bits only")
    storage = FileSystemStorage(
        tmp_path / "media",
        file_permissions_mode=0o600,
        directory_permissions_mode=0o700,
    )
    name = await storage.save("private/nested/key.bin", b"private")
    assert stat.S_IMODE((Path(storage.location) / name).stat().st_mode) == 0o600
    for path in (
        Path(storage.location) / "private",
        Path(storage.location) / "private/nested",
    ):
        assert stat.S_IMODE(path.stat().st_mode) == 0o700


@pytest.mark.asyncio
async def test_non_regular_objects_are_not_opened(tmp_path: Path) -> None:
    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFO creation unavailable")
    storage = FileSystemStorage(tmp_path / "media")
    fifo = Path(storage.location) / "not-a-file"
    os.mkfifo(fifo)
    from async_storage_kit import StorageError

    with pytest.raises(StorageError):
        await storage.read("not-a-file")


@pytest.mark.asyncio
async def test_many_processes_publish_unique_names(tmp_path: Path) -> None:
    # Confirms no-clobber semantics across processes, not just asyncio tasks.
    root = str(tmp_path / "media")
    with ProcessPoolExecutor(
        max_workers=3, mp_context=multiprocessing.get_context("spawn")
    ) as pool:
        results = list(pool.map(_process_save, [root] * 9, range(9)))
    names = [name for name, _ in results]
    assert len(names) == len(set(names))
    storage = FileSystemStorage(root)
    assert sorted([await storage.read(name) for name, _ in results]) == sorted(
        payload for _, payload in results
    )


@pytest.mark.asyncio
async def test_portable_driver_contract_is_exercised(
    tmp_path: Path, monkeypatch
) -> None:
    # Run the Windows-style code path on POSIX so it is not only covered in CI.
    from async_storage_kit.backends import filesystem

    monkeypatch.setattr(filesystem, "is_supported", lambda: False)
    storage = filesystem.FileSystemStorage(tmp_path / "portable")
    assert not storage.symlink_race_protection
    await storage.save("folder/item.txt", b"first")
    other = await storage.save("folder/item.txt", b"second")
    assert other != "folder/item.txt"
    assert await storage.read(other) == b"second"
    await storage.save("folder/item.txt", b"third", on_conflict="overwrite")
    assert await storage.read("folder/item.txt") == b"third"
    assert await storage.listdir("folder") == (
        [],
        sorted(["item.txt", other.rsplit("/", 1)[-1]]),
    )
    assert await storage.delete(other)


@pytest.mark.asyncio
async def test_internal_staging_names_are_not_user_keys(tmp_path: Path) -> None:
    storage = FileSystemStorage(tmp_path / "media")
    from async_storage_kit import MemoryStorage

    key = ".async-storage-" + "a" * 32 + ".tmp"
    for backend in (storage, MemoryStorage()):
        with pytest.raises(SuspiciousFileOperation, match="reserved"):
            await backend.save(key, b"bad")
        with pytest.raises(SuspiciousFileOperation, match="reserved"):
            await backend.read("somewhere/" + key)


@pytest.mark.asyncio
async def test_failed_overwrite_leaves_no_staging_and_preserves_original(
    tmp_path: Path,
) -> None:
    storage = FileSystemStorage(tmp_path / "media")
    await storage.save("existing.txt", b"keep")

    async def fail():
        yield b"half"
        raise ValueError("source failed")

    with pytest.raises(ValueError, match="source failed"):
        await storage.save("existing.txt", fail(), on_conflict="overwrite")
    assert await storage.read("existing.txt") == b"keep"
    assert not list(Path(storage.location).glob(".async-storage-*.tmp"))
