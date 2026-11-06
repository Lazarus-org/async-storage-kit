import pytest

from async_storage_kit import MemoryStorage, StorageRegistry
from async_storage_kit.integrations.fastapi import storage_dependency


def test_framework_adapter_does_not_require_fastapi():
    storage = MemoryStorage()
    registry = StorageRegistry({"default": storage})
    dependency = storage_dependency(registry)
    assert dependency() is storage
    assert dependency() is registry["default"]


@pytest.mark.asyncio
async def test_fastapi_dependency_and_streaming_route(tmp_path):
    import importlib.util
    from pathlib import Path

    import httpx

    path = Path(__file__).resolve().parents[1] / "examples/fastapi_s3.py"
    spec = importlib.util.spec_from_file_location("storage_example", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    storage = MemoryStorage()
    await storage.save("nested/example.txt", b"download")
    module.app.dependency_overrides[module.get_storage] = lambda: storage
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=module.app), base_url="http://test"
    ) as client:
        response = await client.get("/files/nested/example.txt")
        assert response.status_code == 200 and response.content == b"download"
        assert (await client.get("/files/missing")).status_code == 404
