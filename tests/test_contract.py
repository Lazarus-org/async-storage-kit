from __future__ import annotations

from io import BytesIO

import pytest

from async_storage_kit import FileAlreadyExists, FileNotFound


@pytest.mark.asyncio
async def test_roundtrip_and_metadata(backend):
    name = await backend.save("nested/test.txt", b"Hello")
    assert name == "nested/test.txt"
    assert await backend.read(name) == b"Hello"
    assert await backend.size(name) == 5
    meta = await backend.metadata(name)
    assert meta.name == name and meta.size == 5
    assert meta.modified_at.tzinfo is not None
    assert meta.modified_at.utcoffset().total_seconds() == 0
    assert await backend.exists(name)
    assert await backend.listdir("") == (["nested"], [])
    assert await backend.listdir("nested") == ([], ["test.txt"])
    assert await backend.delete(name)
    assert not await backend.exists(name)
    assert not await backend.delete(name)
    with pytest.raises(FileNotFound):
        await backend.delete(name, raise_on_missing=True)


@pytest.mark.asyncio
async def test_collision_modes(backend):
    await backend.save("image.jpg", b"first")
    with pytest.raises(FileAlreadyExists):
        await backend.save("image.jpg", b"second", on_conflict="error")
    alternative = await backend.save("image.jpg", b"second")
    assert alternative != "image.jpg"
    assert alternative.endswith(".jpg")
    assert await backend.read("image.jpg") == b"first"
    assert await backend.read(alternative) == b"second"
    assert (
        await backend.save("image.jpg", b"replaced", on_conflict="overwrite")
        == "image.jpg"
    )
    assert await backend.read("image.jpg") == b"replaced"


@pytest.mark.asyncio
async def test_binary_file_and_async_stream(backend):
    name = await backend.save("folder/test.bin", BytesIO(b"0123456789"))
    assert [chunk async for chunk in backend.iter_chunks(name, 3)] == [
        b"012",
        b"345",
        b"678",
        b"9",
    ]

    async def produce():
        yield b"a"
        yield b"bc"

    new_name = await backend.save("folder/stream.bin", produce())
    assert await backend.read(new_name) == b"abc"


@pytest.mark.asyncio
async def test_bad_stream_does_not_corrupt_prior_object(backend):
    await backend.save("important.bin", b"old")

    async def bad_source():
        yield b"partial"
        raise RuntimeError("failed upload")

    with pytest.raises(RuntimeError):
        await backend.save("important.bin", bad_source(), on_conflict="overwrite")
    assert await backend.read("important.bin") == b"old"


@pytest.mark.asyncio
async def test_bad_chunk_size(backend):
    await backend.save("x", b"abc")
    with pytest.raises(ValueError):
        async for _ in backend.iter_chunks("x", 0):
            pass


@pytest.mark.asyncio
async def test_missing_read(backend):
    with pytest.raises(FileNotFound):
        await backend.read("not-found")


@pytest.mark.asyncio
async def test_empty_file(backend):
    await backend.save("empty", b"")
    assert await backend.read("empty") == b""
    assert await backend.size("empty") == 0
