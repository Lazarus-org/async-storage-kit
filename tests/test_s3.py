"""SDK-independent S3 contract tests with a strict in-process protocol fake.

These tests prove package behavior, not AWS/MinIO server compatibility. Real
service tests are maintained separately in test_s3_minio.py.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from io import BytesIO

import pytest

from async_storage_kit import (
    FileAlreadyExists,
    FileNotFound,
    S3Storage,
    StorageError,
    StorageRegistry,
    SuspiciousFileOperation,
    UnsupportedOperation,
)


class ApiError(Exception):
    def __init__(self, code: str):
        self.response = {"Error": {"Code": code}}
        super().__init__(code)


class StreamingBody:
    def __init__(self, content: bytes):
        self.reader = BytesIO(content)
        self.closed = False
        self.read_sizes: list[int] = []

    async def read(self, count: int):
        self.read_sizes.append(count)
        return self.reader.read(count)

    def close(self):
        self.closed = True
        self.reader.close()


class FakeS3Client:
    def __init__(self, store):
        self.store = store

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def head_bucket(self, *, Bucket):
        if not self.store.bucket_created:
            raise ApiError("NoSuchBucket")

    async def create_bucket(self, *, Bucket, **kwargs):
        self.store.bucket_created = True
        self.store.creates += 1
        self.store.create_params = kwargs

    async def head_object(self, *, Bucket, Key):
        if self.store.forbid_head:
            raise ApiError("AccessDenied")
        if Key not in self.store.objects:
            raise ApiError("404")
        content, params, modified = self.store.objects[Key]
        return {
            "ContentLength": len(content),
            "LastModified": modified,
            "ETag": '"abcd123"',
            "ContentType": params.get("ContentType", "binary/octet-stream"),
            "Metadata": params.get("Metadata", {}),
        }

    async def get_object(self, *, Bucket, Key):
        if Key not in self.store.objects:
            raise ApiError("NoSuchKey")
        body = StreamingBody(self.store.objects[Key][0])
        self.store.bodies.append(body)
        return {"Body": body}

    async def put_object(self, *, Bucket, Key, Body, IfNoneMatch=None, **params):
        self.store.puts += 1
        if IfNoneMatch == "*" and (
            Key in self.store.objects or self.store.force_conflict
        ):
            raise ApiError("PreconditionFailed")
        self.store.objects[Key] = (bytes(Body), params, datetime.now(timezone.utc))

    async def delete_object(self, *, Bucket, Key):
        self.store.objects.pop(Key, None)

    async def list_objects_v2(
        self, *, Bucket, Prefix, Delimiter, ContinuationToken=None
    ):
        files, dirs = [], set()
        for key in sorted(self.store.objects):
            if not key.startswith(Prefix):
                continue
            rest = key[len(Prefix) :]
            if "/" in rest:
                dirs.add(Prefix + rest.split("/", 1)[0] + "/")
            elif rest:
                files.append({"Key": key})
        items = [("dir", k) for k in sorted(dirs)] + [("file", k["Key"]) for k in files]
        offset = int(ContinuationToken or "0")
        page = items[offset : offset + 2]
        truncated = offset + len(page) < len(items)
        return {
            "IsTruncated": truncated,
            "NextContinuationToken": str(offset + len(page)) if truncated else None,
            "CommonPrefixes": [{"Prefix": key} for t, key in page if t == "dir"],
            "Contents": [{"Key": key} for t, key in page if t == "file"],
        }

    async def create_multipart_upload(self, *, Bucket, Key, **params):
        self.store.parts["upload-1"] = {"Key": Key, "params": params, "parts": {}}
        return {"UploadId": "upload-1"}

    async def upload_part(self, *, Bucket, Key, UploadId, PartNumber, Body):
        if self.store.fail_part:
            raise ApiError("InternalError")
        if self.store.block_part:
            self.store.upload_started.set()
            await asyncio.Event().wait()
        self.store.parts[UploadId]["parts"][PartNumber] = bytes(Body)
        return {"ETag": f'"part{PartNumber}"'}

    async def complete_multipart_upload(
        self, *, Bucket, Key, UploadId, MultipartUpload, IfNoneMatch=None
    ):
        if IfNoneMatch == "*" and (
            Key in self.store.objects or self.store.force_conflict
        ):
            raise ApiError("PreconditionFailed")
        upload = self.store.parts.pop(UploadId)
        data = b"".join(
            upload["parts"][p["PartNumber"]] for p in MultipartUpload["Parts"]
        )
        self.store.objects[Key] = (data, upload["params"], datetime.now(timezone.utc))
        self.store.completions += 1

    async def abort_multipart_upload(self, *, Bucket, Key, UploadId):
        self.store.parts.pop(UploadId, None)
        self.store.aborts += 1

    async def generate_presigned_url(self, operation, *, Params, ExpiresIn):
        return f"https://signed.test/{Params['Key']}?expires={ExpiresIn}"


class FakeSession:
    def __init__(self):
        self.objects: dict = {}
        self.parts: dict = {}
        self.bodies: list = []
        self.creates = 0
        self.completions = 0
        self.aborts = 0
        self.puts = 0
        self.bucket_created = True
        self.create_params: dict = {}
        self.last_options: dict = {}
        self.force_conflict = False
        self.fail_part = False
        self.block_part = False
        self.upload_started = asyncio.Event()
        self.forbid_head = False

    def client(self, service, **kwargs):
        assert service == "s3"
        self.last_options = kwargs
        return FakeS3Client(self)


@pytest.fixture
def s3():
    session = FakeSession()
    return S3Storage("test-bucket", location="users", session=session), session


@pytest.mark.asyncio
async def test_contract_and_list_pagination(s3):
    storage, session = s3
    for name in ("files/a.txt", "files/b.txt", "files/c.txt", "files/sub/z.txt"):
        assert await storage.save(name, b"hello") == name
    assert await storage.exists("files/a.txt")
    assert await storage.listdir("files") == (["sub"], ["a.txt", "b.txt", "c.txt"])
    assert await storage.listdir("") == (["files"], [])
    assert await storage.size("files/a.txt") == 5
    assert await storage.get_modified_time("files/a.txt")
    meta = await storage.metadata("files/a.txt")
    assert (meta.name, meta.size, meta.etag, meta.content_type) == (
        "files/a.txt",
        5,
        '"abcd123"',
        "text/plain",
    )
    assert [c async for c in storage.iter_chunks("files/a.txt", 2)] == [
        b"he",
        b"ll",
        b"o",
    ]
    assert session.bodies[-1].closed
    assert await storage.delete("files/a.txt")
    assert not await storage.delete("files/a.txt")
    assert not await storage.exists("files/a.txt")
    with pytest.raises(FileNotFound):
        await storage.read("files/a.txt")
    with pytest.raises(FileNotFound):
        await storage.delete("files/a.txt", raise_on_missing=True)
    assert session.creates == 0


@pytest.mark.asyncio
async def test_collision_renaming_and_overwrite(s3):
    storage, session = s3
    await storage.save("photo.jpg", b"a")
    with pytest.raises(FileAlreadyExists):
        await storage.save("photo.jpg", b"b", on_conflict="error")
    alt = await storage.save("photo.jpg", b"b")
    assert alt != "photo.jpg" and alt.endswith(".jpg")
    assert await storage.read(alt) == b"b"
    assert await storage.read("photo.jpg") == b"a"
    await storage.save("photo.jpg", b"c", on_conflict="overwrite")
    assert await storage.read("photo.jpg") == b"c"
    session.force_conflict = True
    with pytest.raises(FileAlreadyExists):
        await storage.save("new.bin", b"x", on_conflict="error")
    assert not await storage.exists("new.bin")


@pytest.mark.asyncio
async def test_multipart_streaming_cleanup_and_no_partial_overwrite(s3):
    storage, session = s3
    storage.multipart_threshold = 5 * 1024 * 1024
    storage.multipart_chunksize = 5 * 1024 * 1024
    payload = b"x" * (5 * 1024 * 1024 + 10)

    async def stream():
        for start in range(0, len(payload), 101_001):
            yield payload[start : start + 101_001]

    await storage.save("large.bin", stream())
    assert await storage.read("large.bin") == payload
    assert session.completions == 1
    assert session.parts == {}

    async def bad_stream():
        yield payload
        raise RuntimeError("source dropped")

    with pytest.raises(RuntimeError):
        await storage.save("large.bin", bad_stream(), on_conflict="overwrite")
    assert await storage.size("large.bin") == len(payload)
    assert session.aborts == 1 and session.parts == {}
    session.force_conflict = True
    with pytest.raises(FileAlreadyExists):
        await storage.save("new.bin", stream(), on_conflict="error")
    assert session.aborts == 2 and session.parts == {}


@pytest.mark.asyncio
async def test_multipart_upload_failure_aborts(s3):
    storage, session = s3
    storage.multipart_threshold = 5 * 1024 * 1024
    storage.multipart_chunksize = 5 * 1024 * 1024
    session.fail_part = True
    with pytest.raises(StorageError):
        await storage.save("large.bin", b"a" * (5 * 1024 * 1024))
    assert session.aborts == 1 and not session.parts


@pytest.mark.asyncio
async def test_error_handling_and_permissions(s3):
    storage, session = s3
    session.forbid_head = True
    with pytest.raises(StorageError):
        await storage.exists("private.txt")
    with pytest.raises(StorageError):
        await storage.save("private.txt", b"x")
    with pytest.raises(ValueError):
        await storage.get_presigned_url("x", 0)
    with pytest.raises(ValueError):
        await storage.get_presigned_url("x", 604_801)
    session.forbid_head = False
    assert await storage.get_presigned_url("a b.txt", 123) == (
        "https://signed.test/users/a b.txt?expires=123"
    )
    assert session.last_options["config"].signature_version == "s3v4"
    assert "aws_access_key_id" not in session.last_options


@pytest.mark.asyncio
async def test_bucket_provisioning_explicit(s3):
    storage, session = s3
    session.bucket_created = False
    assert not await storage.bucket_exists()
    await storage.save(
        "file.txt", b"works"
    )  # Stub allows writes to non-provisioned bucket.
    assert session.creates == 0
    await storage.create_bucket()
    assert session.creates == 1
    assert await storage.bucket_exists()


def test_public_urls_and_validation(s3):
    storage, _ = s3
    with pytest.raises(UnsupportedOperation):
        storage.url("file")
    public = S3Storage(
        "b",
        location="prefix",
        custom_domain="https://cdn.example.com/static",
        public_urls=True,
    )
    assert (
        public.url("folder/a b.txt")
        == "https://cdn.example.com/static/prefix/folder/a%20b.txt"
    )
    with pytest.raises(SuspiciousFileOperation):
        public.url("../bad")
    with pytest.raises(ValueError):
        S3Storage("b", custom_domain="https://test.com?secret=1")
    with pytest.raises(ValueError):
        S3Storage("b", object_parameters={"ACL": "public-read"})
    with pytest.raises(ValueError):
        S3Storage("b", access_key="partial")
    with pytest.raises(ValueError):
        S3Storage("b", multipart_chunksize=1)


def test_backend_lazily_constructed_by_registry():
    from async_storage_kit import S3Storage as PublicS3Storage

    registry = StorageRegistry(
        {
            "cloud": {
                "BACKEND": "async_storage_kit.backends.s3.S3Storage",
                "OPTIONS": {"bucket_name": "foo"},
            }
        }
    )
    assert isinstance(registry["cloud"], PublicS3Storage)
    assert registry["cloud"].bucket_name == "foo"


def test_s3_import_does_not_require_cloud_dependencies():
    import subprocess
    import sys

    command = "import sys; from async_storage_kit import S3Storage; S3Storage('b'); assert 'aioboto3' not in sys.modules"
    result = subprocess.run(
        [sys.executable, "-c", command], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.asyncio
async def test_cancelled_multipart_aborts_and_keeps_existing_object(s3):
    storage, session = s3
    storage.multipart_threshold = 5 * 1024 * 1024
    storage.multipart_chunksize = 5 * 1024 * 1024
    await storage.save("important", b"old")
    session.block_part = True
    task = asyncio.create_task(
        storage.save("important", b"x" * (5 * 1024 * 1024 + 2), on_conflict="overwrite")
    )
    await asyncio.wait_for(session.upload_started.wait(), timeout=3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert session.aborts == 1 and not session.parts
    assert await storage.read("important") == b"old"


@pytest.mark.asyncio
async def test_invalid_presigned_name_preserves_security_exception(s3):
    storage, _ = s3
    with pytest.raises(SuspiciousFileOperation):
        await storage.get_presigned_url("../private")


@pytest.mark.parametrize(
    "options",
    [
        {"multipart_chunksize": 5.5 * 1024 * 1024},
        {"multipart_threshold": True},
        {"multipart_threshold": 5 * 1024**3 + 1},
        {"endpoint_url": "file:///tmp/bucket"},
        {"endpoint_url": "https://user:password@example.com"},
        {"endpoint_url": "https://example.com?secret=value"},
    ],
)
def test_unsafe_s3_configuration_rejected(options):
    with pytest.raises(ValueError):
        S3Storage("bucket", **options)


@pytest.mark.asyncio
async def test_repeated_pagination_token_fails_instead_of_looping(s3, monkeypatch):
    storage, _session = s3

    async def page(self, **kwargs):
        return {"IsTruncated": True, "NextContinuationToken": "same"}

    monkeypatch.setattr(FakeS3Client, "list_objects_v2", page)
    with pytest.raises(StorageError, match="new continuation token"):
        await storage.listdir()


@pytest.mark.asyncio
async def test_transport_upload_failure_normalized(s3, monkeypatch):
    from botocore.exceptions import EndpointConnectionError

    storage, _session = s3

    async def fail(self, **kwargs):
        raise EndpointConnectionError(endpoint_url="https://unavailable.test")

    monkeypatch.setattr(FakeS3Client, "put_object", fail)
    with pytest.raises(StorageError) as error:
        await storage.save("file", b"bytes", on_conflict="error")
    assert isinstance(error.value.__cause__, EndpointConnectionError)


@pytest.mark.asyncio
async def test_multipart_abort_finishes_despite_repeated_cancellation(s3, monkeypatch):
    storage, session = s3
    storage.multipart_threshold = storage.multipart_chunksize = 5 * 1024 * 1024
    session.block_part = True
    abort_started = asyncio.Event()
    finish_abort = asyncio.Event()
    original = FakeS3Client.abort_multipart_upload

    async def abort(self, **kwargs):
        abort_started.set()
        await finish_abort.wait()
        await original(self, **kwargs)

    monkeypatch.setattr(FakeS3Client, "abort_multipart_upload", abort)
    task = asyncio.create_task(storage.save("large", b"x" * (5 * 1024 * 1024)))
    await asyncio.wait_for(session.upload_started.wait(), 5)
    task.cancel()
    await asyncio.wait_for(abort_started.wait(), 5)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    finish_abort.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert session.parts == {} and session.aborts == 1


def test_installed_sdk_operation_parameters():
    import botocore.session

    from async_storage_kit.backends.s3 import _ALLOWED_OBJECT_PARAMETERS

    model = botocore.session.get_session().get_service_model("s3")
    for name in ("PutObject", "CompleteMultipartUpload"):
        assert "IfNoneMatch" in model.operation_model(name).input_shape.members
    for name in ("PutObject", "CreateMultipartUpload"):
        assert (
            model.operation_model(name).input_shape.members.keys()
            >= _ALLOWED_OBJECT_PARAMETERS
        )
