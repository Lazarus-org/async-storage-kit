"""Live contract tests for a disposable MinIO / S3-compatible test bucket.

These tests deliberately skip during normal offline development. Set
S3_TEST_ENDPOINT, S3_TEST_BUCKET, S3_TEST_ACCESS_KEY and S3_TEST_SECRET_KEY
for real integration execution. No bucket is automatically created.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from urllib.parse import urlsplit

import pytest

from async_storage_kit import FileAlreadyExists, FileNotFound, S3Storage

pytestmark = [pytest.mark.asyncio, pytest.mark.live_s3]


@pytest.fixture
def storage() -> S3Storage:
    endpoint = os.environ.get("S3_TEST_ENDPOINT")
    if "S3_TEST_ENDPOINT" not in os.environ:
        pytest.skip("Configure a disposable live S3 endpoint to run these tests")
    for key in (
        "S3_TEST_ENDPOINT",
        "S3_TEST_BUCKET",
        "S3_TEST_ACCESS_KEY",
        "S3_TEST_SECRET_KEY",
    ):
        if not os.environ.get(key, "").strip():
            pytest.fail(f"Configured live integration requires {key}")
    # Do not skip when a service is configured: missing SDK is a test failure.
    import aioboto3  # noqa: F401

    return S3Storage(
        os.environ["S3_TEST_BUCKET"],
        location="async-storage-kit-integration",
        endpoint_url=endpoint,
        access_key=os.environ["S3_TEST_ACCESS_KEY"],
        secret_key=os.environ["S3_TEST_SECRET_KEY"],
        region_name="us-east-1",
        addressing_style="path",
        multipart_threshold=5 * 1024 * 1024,
        multipart_chunksize=5 * 1024 * 1024,
    )


@pytest.fixture
def prefix() -> str:
    return f"test-{uuid.uuid4().hex}"


@pytest.mark.parametrize("data", [b"", b"small bytes", bytes(range(256)) * 22_000])
async def test_roundtrip_and_collision_contract(
    storage: S3Storage, prefix: str, data: bytes
) -> None:
    assert await storage.bucket_exists(), "Create the disposable test bucket first"
    name = f"{prefix}/payload.bin"
    try:
        assert await storage.save(name, data) == name
        assert await storage.read(name) == data
        assert await storage.size(name) == len(data)
        assert (await storage.metadata(name)).size == len(data)
        assert await storage.exists(name)
        assert await storage.listdir(prefix) == ([], ["payload.bin"])
        with pytest.raises(FileAlreadyExists):
            await storage.save(name, b"must not overwrite", on_conflict="error")
        assert await storage.read(name) == data
        renamed = await storage.save(name, b"renamed")
        try:
            assert renamed != name
            assert await storage.read(renamed) == b"renamed"
            assert await storage.read(name) == data
        finally:
            await storage.delete(renamed)
        signed = await storage.get_presigned_url(name, expire=60)
        assert urlsplit(signed).scheme in {"http", "https"}
        assert urlsplit(signed).query
        import httpx

        async with httpx.AsyncClient(trust_env=False) as client:
            response = await client.get(signed)
            assert response.status_code == 200
            assert response.content == data
        await storage.save(name, b"overwritten", on_conflict="overwrite")
        assert await storage.read(name) == b"overwritten"
    finally:
        await storage.delete(name)
    with pytest.raises(FileNotFound):
        await storage.read(name)


async def test_async_stream_multipart_and_listing(
    storage: S3Storage, prefix: str
) -> None:
    names = [f"{prefix}/child/{n}.dat" for n in range(3)]
    payload = b"x" * (5 * 1024 * 1024 + 12345)

    async def producer():
        for offset in range(0, len(payload), 131_071):
            yield payload[offset : offset + 131_071]

    try:
        await storage.save(names[0], producer())
        await storage.save(names[1], b"more")
        await storage.save(names[2], b"another")
        assert await storage.read(names[0]) == payload
        assert await storage.listdir(prefix) == (["child"], [])
        assert await storage.listdir(f"{prefix}/child") == (
            [],
            ["0.dat", "1.dat", "2.dat"],
        )
        assert (
            b"".join([chunk async for chunk in storage.iter_chunks(names[0], 32768)])
            == payload
        )
    finally:
        for name in names:
            await storage.delete(name)


async def test_concurrent_no_clobber(storage: S3Storage, prefix: str) -> None:
    name = f"{prefix}/collision.dat"
    try:
        results = await asyncio.gather(
            storage.save(name, b"writer-1", on_conflict="error"),
            storage.save(name, b"writer-2", on_conflict="error"),
            return_exceptions=True,
        )
        assert sum(isinstance(result, str) for result in results) == 1
        assert sum(isinstance(result, FileAlreadyExists) for result in results) == 1
        assert await storage.read(name) in {b"writer-1", b"writer-2"}
    finally:
        await storage.delete(name)


async def test_cancelled_multipart_cleans_up_and_preserves_object(
    storage: S3Storage, prefix: str
) -> None:
    name = f"{prefix}/cancelled.bin"
    started = asyncio.Event()

    async def producer():
        yield b"x" * (5 * 1024 * 1024)
        started.set()  # First upload_part has returned before requesting another chunk.
        await asyncio.Event().wait()
        yield b"unreachable"

    try:
        await storage.save(name, b"original")
        task = asyncio.create_task(
            storage.save(name, producer(), on_conflict="overwrite")
        )
        try:
            await asyncio.wait_for(started.wait(), 30)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert await storage.read(name) == b"original"
        async with storage._client() as client:
            result = await client.list_multipart_uploads(
                Bucket=storage.bucket_name, Prefix=storage._key(name)
            )
            assert result.get("Uploads", []) == []
    finally:
        await storage.delete(name)


async def test_multipart_collision_is_conditional(
    storage: S3Storage, prefix: str
) -> None:
    name = f"{prefix}/collision-large.bin"
    try:
        await storage.save(name, b"original")
        with pytest.raises(FileAlreadyExists):
            await storage.save(name, b"x" * (5 * 1024 * 1024 + 1), on_conflict="error")
        assert await storage.read(name) == b"original"
        async with storage._client() as client:
            result = await client.list_multipart_uploads(
                Bucket=storage.bucket_name, Prefix=storage._key(name)
            )
            assert result.get("Uploads", []) == []
    finally:
        await storage.delete(name)
