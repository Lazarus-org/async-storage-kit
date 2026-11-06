"""Exercise framework examples against real HTTP handlers and the CLI entry point."""

from __future__ import annotations

import importlib.util
import runpy
from pathlib import Path

import httpx
import pytest

from async_storage_kit import MemoryStorage

PROJECT = Path(__file__).resolve().parents[1]


def load_example(name: str):
    path = PROJECT / "examples" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"example_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_django_asgi_example_streams_objects_and_returns_404(monkeypatch):
    from django.conf import settings
    from django.core.asgi import get_asgi_application
    from django.urls import clear_url_caches

    example = load_example("django_storage")
    if not settings.configured:
        settings.configure(
            SECRET_KEY="example-test-only",
            ALLOWED_HOSTS=["testserver"],
            INSTALLED_APPS=[],
            MIDDLEWARE=[],
            ROOT_URLCONF=example,
        )
    monkeypatch.setattr(settings, "ROOT_URLCONF", example)
    clear_url_caches()
    payload = b"streamed-data" * 30_000
    await example.storage.save("nested/payload.bin", payload)
    await example.storage.save("empty.bin", b"")
    app = get_asgi_application()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.get("/files/nested/payload.bin")
            assert response.status_code == 200
            assert response.headers["content-type"] == "application/octet-stream"
            assert response.content == payload
            empty = await client.get("/files/empty.bin")
            assert empty.status_code == 200 and empty.content == b""
            assert (await client.get("/files/missing.bin")).status_code == 404
    finally:
        clear_url_caches()


@pytest.mark.asyncio
async def test_aiohttp_example_streams_objects_and_returns_404():
    from aiohttp.test_utils import TestClient, TestServer

    example = load_example("aiohttp_storage")
    storage = MemoryStorage()
    payload = b"streamed-data" * 30_000
    await storage.save("nested/payload.bin", payload)
    await storage.save("empty.bin", b"")
    async with TestClient(TestServer(example.create_app(storage))) as client:
        response = await client.get("/files/nested/payload.bin")
        assert response.status == 200
        assert response.headers["Content-Type"] == "application/octet-stream"
        assert await response.read() == payload
        empty = await client.get("/files/empty.bin")
        assert empty.status == 200 and await empty.read() == b""
        assert (await client.get("/files/missing.bin")).status == 404


def test_background_worker_example_runs(capsys):
    runpy.run_path(str(PROJECT / "examples/background_worker.py"), run_name="__main__")
    assert "reports/status.csv" in capsys.readouterr().out
