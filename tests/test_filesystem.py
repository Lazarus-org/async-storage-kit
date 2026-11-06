from __future__ import annotations

import asyncio

import pytest

from async_storage_kit import FileSystemStorage, UnsupportedOperation


@pytest.mark.asyncio
async def test_safe_url_encoding(tmp_path):
    storage = FileSystemStorage(tmp_path / "media", base_url="/media/")
    name = await storage.save("images/a b#c.png", b"ok")
    assert storage.url(name) == "/media/images/a%20b%23c.png"
    with pytest.raises(UnsupportedOperation):
        await storage.get_presigned_url(name)


@pytest.mark.asyncio
async def test_parallel_create_does_not_overwrite(tmp_path):
    storage = FileSystemStorage(tmp_path / "media")
    names = await asyncio.gather(
        *(storage.save("same.txt", str(i).encode()) for i in range(12))
    )
    assert len(set(names)) == len(names)
    assert len(set([await storage.read(name) for name in names])) == len(names)


@pytest.mark.asyncio
async def test_stream_from_file_like_is_bounded(tmp_path):
    storage = FileSystemStorage(tmp_path / "media")

    class Reader:
        def __init__(self):
            self.remaining = 600_000
            self.sizes = []

        def read(self, n):
            self.sizes.append(n)
            got = min(self.remaining, n)
            self.remaining -= got
            return b"x" * got

    reader = Reader()
    name = await storage.save("large.bin", reader)
    assert await storage.size(name) == 600_000
    assert reader.sizes and max(reader.sizes) <= 256 * 1024


@pytest.mark.asyncio
async def test_no_implicit_public_url(tmp_path):
    storage = FileSystemStorage(tmp_path / "media")
    with pytest.raises(UnsupportedOperation):
        storage.url("a")


@pytest.mark.asyncio
async def test_partial_write_cleaned_up(tmp_path):
    storage = FileSystemStorage(tmp_path / "media")

    async def failing():
        yield b"part"
        raise RuntimeError("upload interrupted")

    with pytest.raises(RuntimeError):
        await storage.save("broken.bin", failing())
    assert not await storage.exists("broken.bin")
