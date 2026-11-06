from __future__ import annotations

import builtins
from datetime import datetime, timezone

import pytest
from test_s3 import ApiError, FakeS3Client, FakeSession, StreamingBody

from async_storage_kit import FileAlreadyExists, FileNotFound, S3Storage, StorageError
from async_storage_kit.backends import s3 as s3_module


@pytest.mark.parametrize(
    "options",
    [
        {"bucket_name": ""},
        {"bucket_name": 123},
        {"session_token": "token"},
        {"addressing_style": "invalid"},
        {"endpoint_url": "https://s3.example.test"},
    ],
)
def test_connection_configuration(options):
    if "endpoint_url" in options:
        assert S3Storage("bucket", **options).endpoint_url == options["endpoint_url"]
    else:
        with pytest.raises(ValueError):
            S3Storage(**({"bucket_name": "bucket"} | options))


@pytest.mark.parametrize("response", [None, {}, {"Error": "bad"}, {"Error": {}}])
def test_missing_or_malformed_error_codes(response):
    error = RuntimeError("failure")
    error.response = response
    assert s3_module._error_code(error) is None


def test_lazy_sdk_construction_and_explicit_connection_options(monkeypatch):
    import aioboto3

    session = FakeSession()
    monkeypatch.setattr(aioboto3, "Session", lambda: session)
    storage = S3Storage(
        "bucket",
        region_name="eu-west-1",
        endpoint_url="https://s3.example.test",
        access_key="test-key",
        secret_key="test-secret",
        session_token="test-token",
    )
    assert isinstance(storage._client(), FakeS3Client)
    assert storage._session is session
    assert session.last_options["aws_session_token"] == "test-token"
    assert session.last_options["aws_access_key_id"] == "test-key"
    assert session.last_options["aws_secret_access_key"] == "test-secret"
    assert session.last_options["endpoint_url"] == "https://s3.example.test"
    assert session.last_options["region_name"] == "eu-west-1"


@pytest.mark.parametrize("missing", ["aioboto3", "botocore.config"])
def test_optional_sdk_import_errors(monkeypatch, missing):
    original = builtins.__import__

    def import_module(name, *args, **kwargs):
        if name == missing:
            raise ImportError("unavailable")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_module)
    storage = S3Storage(
        "bucket", session=None if missing == "aioboto3" else FakeSession()
    )
    with pytest.raises(ImportError, match="async-storage-kit\\[s3\\]"):
        storage._client()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("client_method", "operation"),
    [
        ("head_bucket", "bucket_exists"),
        ("create_bucket", "create_bucket"),
        ("delete_object", "delete"),
        ("list_objects_v2", "listdir"),
        ("head_object", "metadata"),
        ("generate_presigned_url", "get_presigned_url"),
    ],
)
async def test_service_errors_keep_operation_context(
    monkeypatch, client_method, operation
):
    storage = S3Storage("bucket", session=FakeSession())
    await storage.save("file", b"data")

    async def fail(*args, **kwargs):
        raise ApiError("AccessDenied")

    monkeypatch.setattr(FakeS3Client, client_method, fail)
    args = (
        () if operation in {"bucket_exists", "create_bucket", "listdir"} else ("file",)
    )
    with pytest.raises(StorageError, match=client_method):
        await getattr(storage, operation)(*args)


@pytest.mark.asyncio
async def test_default_region_bucket_and_timestamp_validation(monkeypatch):
    session = FakeSession()
    storage = S3Storage("bucket", session=session)
    await storage.create_bucket()
    assert session.create_params == {}
    with pytest.raises(FileNotFound):
        await storage.metadata("missing")
    session.objects["file"] = (b"x", {}, "invalid")
    with pytest.raises(StorageError, match="LastModified"):
        await storage.metadata("file")
    session.objects["file"] = (b"x", {}, datetime(2025, 1, 2))
    assert (await storage.metadata("file")).modified_at == datetime(
        2025, 1, 2, tzinfo=timezone.utc
    )
    monkeypatch.setattr(
        FakeS3Client, "generate_presigned_url", lambda *a, **kw: "signed"
    )
    assert await storage.get_presigned_url("file") == "signed"


@pytest.mark.asyncio
@pytest.mark.parametrize("region", ["us-east-1", "eu-west-1"])
async def test_bucket_creation_region_constraint(region):
    session = FakeSession()
    storage = S3Storage("bucket", session=session, region_name=region)
    await storage.create_bucket()
    expected = (
        {}
        if region == "us-east-1"
        else {"CreateBucketConfiguration": {"LocationConstraint": region}}
    )
    assert session.create_params == expected


@pytest.mark.asyncio
async def test_stream_read_failure_closes_async_body(monkeypatch):
    session = FakeSession()
    storage = S3Storage("bucket", session=session)
    await storage.save("file", b"data")
    closed = []

    async def fail(self, count):
        raise ApiError("InternalError")

    async def close(self):
        closed.append(self)
        self.reader.close()

    monkeypatch.setattr(StreamingBody, "read", fail)
    monkeypatch.setattr(StreamingBody, "close", close)
    with pytest.raises(StorageError, match="stream read"):
        await storage.read("file")
    assert closed == session.bodies
    with pytest.raises(ValueError, match="positive"):
        _ = [chunk async for chunk in storage.iter_chunks("file", 0)]


@pytest.mark.asyncio
async def test_collision_exhaustion_does_not_consume_content(monkeypatch):
    storage = S3Storage("bucket", session=FakeSession())
    await storage.save("file", b"old")
    monkeypatch.setattr(storage, "get_alternative_name", lambda *args: "file")

    async def source():
        pytest.fail("Collision exhaustion must happen before consuming the source")
        yield b""

    with pytest.raises(FileAlreadyExists, match="allocate"):
        await storage.save("file", source())
    assert await storage.read("file") == b"old"


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [0, 1, 5 * 1024 * 1024])
async def test_multipart_part_limit_and_exact_part_boundary(monkeypatch, extra):
    size = 5 * 1024 * 1024
    session = FakeSession()
    storage = S3Storage(
        "bucket", session=session, multipart_threshold=size, multipart_chunksize=size
    )
    # A small injected quota exercises both quota checks without a 50 GiB fixture.
    monkeypatch.setattr(s3_module, "_MAX_PARTS", 1)
    if extra:
        with pytest.raises(StorageError, match="10,000 parts"):
            await storage.save("file", b"a" * (size + extra), on_conflict="error")
        assert session.aborts == 1 and session.parts == {}
        assert not await storage.exists("file")
    else:
        await storage.save("file", b"a" * size, on_conflict="error")
        assert await storage.size("file") == size
        assert session.completions == 1


@pytest.mark.asyncio
async def test_directory_marker_and_nested_listing_entries_are_filtered(monkeypatch):
    async def listing(*args, **kwargs):
        return {
            "CommonPrefixes": [{"Prefix": "root/"}, {"Prefix": "root/sub/"}],
            "Contents": [
                {"Key": "root/"},
                {"Key": "root/sub/deep"},
                {"Key": "root/file"},
            ],
        }

    monkeypatch.setattr(FakeS3Client, "list_objects_v2", listing)
    storage = S3Storage("bucket", session=FakeSession())
    assert await storage.listdir("root") == (["sub"], ["file"])
