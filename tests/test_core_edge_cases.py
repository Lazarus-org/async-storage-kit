from __future__ import annotations

import asyncio
from io import BytesIO

import pytest

import async_storage_kit
import async_storage_kit.backends as backends
from async_storage_kit import (
    FileAlreadyExists,
    FileNotFound,
    InvalidStorageError,
    LazyStorageProxy,
    MemoryStorage,
    StorageBackend,
    StorageRegistry,
    SuspiciousFileOperation,
    UnsupportedOperation,
)
from async_storage_kit._async import wait_for_completion
from async_storage_kit.streams import iter_content
from async_storage_kit.utils import (
    alternative_name,
    get_random_string,
    get_valid_filename,
    normalize_name,
    safe_join,
    validate_file_name,
)


@pytest.mark.parametrize("module", [async_storage_kit, backends])
def test_lazy_exports_and_unknown_attributes(module):
    for name in ("FileSystemStorage", "S3Storage", "S3FileMetadata"):
        exported = module.__getattr__(name)
        assert exported.__name__ == name
    with pytest.raises(AttributeError, match="unknown"):
        module.__getattr__("unknown")


@pytest.mark.asyncio
async def test_base_helpers_and_unsupported_capabilities(monkeypatch):
    storage = MemoryStorage()
    content = BytesIO(b"named")
    content.name = "named.txt"
    assert await storage.save(None, content) == "named.txt"
    with pytest.raises(SuspiciousFileOperation, match="unnamed"):
        await storage.save(None, b"unnamed")
    with pytest.raises(ValueError, match="positive"):
        await storage.save("x", b"", max_length=0)
    assert storage.generate_filename("folder/café photo.txt") == "folder/cafe_photo.txt"
    assert storage.get_valid_name("hello world") == "hello_world"
    assert storage.get_alternative_name("x.txt").endswith(".txt")
    assert await storage.is_name_available("new", 3)
    assert not await storage.is_name_available("long", 3)
    assert await storage.get_available_name("new") == "new"
    assert await storage.get_available_name("named.txt") != "named.txt"
    with pytest.raises(SuspiciousFileOperation, match="max_length"):
        await storage.get_available_name("long", 1)
    monkeypatch.setattr(storage, "get_alternative_name", lambda *args: "named.txt")
    with pytest.raises(SuspiciousFileOperation, match="allocate"):
        await storage.get_available_name("named.txt")
    with pytest.raises(FileAlreadyExists, match="allocate"):
        await storage.save("named.txt", b"collision")
    assert await StorageBackend.read(storage, "named.txt") == b"named"
    for method in (StorageBackend.url, StorageBackend.path):
        with pytest.raises(UnsupportedOperation):
            method(storage, "named.txt")
    for method in (
        StorageBackend.get_presigned_url,
        StorageBackend.get_accessed_time,
        StorageBackend.get_created_time,
    ):
        with pytest.raises(UnsupportedOperation):
            await method(storage, "named.txt")
    assert await storage.get_created_time(
        "named.txt"
    ) == await storage.get_modified_time("named.txt")
    with pytest.raises(FileNotFound):
        await storage.get_modified_time("missing")
    await storage.save("other/file", b"")
    assert await storage.listdir("other") == ([], ["file"])


@pytest.mark.parametrize("name", [None, 42, ""])
def test_invalid_name_types_and_empty_names(name):
    with pytest.raises(SuspiciousFileOperation):
        normalize_name(name)


def test_filename_utilities_and_bounds(tmp_path):
    validate_file_name("file.txt")
    validate_file_name("folder/file.txt", allow_relative_path=True)
    with pytest.raises(SuspiciousFileOperation):
        validate_file_name("folder/file.txt")
    for name in ("", ".", "..", "!!!"):
        with pytest.raises(SuspiciousFileOperation):
            get_valid_filename(name)
    assert get_valid_filename(" café (1).txt ") == "cafe_1.txt"
    with pytest.raises(ValueError):
        get_random_string(0)
    assert len(get_random_string(2)) == 2
    candidate = alternative_name("folder/longname.tar.gz", max_length=25)
    assert candidate.startswith("folder/") and candidate.endswith(".tar.gz")
    assert len(candidate) == 25
    with pytest.raises(SuspiciousFileOperation, match="accommodate"):
        alternative_name("file.txt", max_length=4)
    assert safe_join(tmp_path, "", "folder/file") == str(tmp_path / "folder" / "file")


@pytest.mark.parametrize("definition", [None, 1, {}, {"backend": 1}])
def test_registry_rejects_invalid_definitions(definition):
    with pytest.raises(InvalidStorageError):
        StorageRegistry.create_storage(definition)


def test_proxy_attribute_access_and_repr():
    storage = MemoryStorage()
    proxy = LazyStorageProxy(StorageRegistry({"default": storage}))
    assert proxy.get_valid_name("a b") == "a_b"
    assert repr(proxy) == "<LazyStorageProxy alias='default'>"


@pytest.mark.asyncio
async def test_stream_empty_chunks_and_awaitable_read_results():
    async def source():
        yield b""
        yield memoryview(b"abcd")

    assert [chunk async for chunk in iter_content(source(), 2)] == [b"ab", b"cd"]

    class AwaitableReader:
        def __init__(self):
            self.data = iter([b"abc", b""])

        def read(self, size):
            data = next(self.data)

            async def result():
                return data

            return result()

    assert [chunk async for chunk in iter_content(AwaitableReader(), 2)] == [
        b"ab",
        b"c",
    ]
    with pytest.raises(TypeError, match="Content must"):
        _ = [chunk async for chunk in iter_content(object())]


@pytest.mark.asyncio
async def test_worker_failure_and_worker_cancellation_propagate():
    async def fail():
        raise RuntimeError("worker failed")

    async def cancel():
        raise asyncio.CancelledError

    with pytest.raises(RuntimeError, match="worker failed"):
        await wait_for_completion(fail())
    with pytest.raises(asyncio.CancelledError):
        await wait_for_completion(cancel())
