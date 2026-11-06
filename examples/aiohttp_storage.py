"""Install aiohttp, then run: python examples/aiohttp_storage.py."""

from __future__ import annotations

from contextlib import aclosing

from aiohttp import web

from async_storage_kit import MemoryStorage, StorageBackend

STORAGE = web.AppKey("storage", StorageBackend)


async def download(request: web.Request) -> web.StreamResponse:
    storage = request.app[STORAGE]
    name = request.match_info["name"]
    # Apply application authorization before returning private content.
    if not await storage.exists(name):
        raise web.HTTPNotFound(text="File not found")
    response = web.StreamResponse(headers={"Content-Type": "application/octet-stream"})
    await response.prepare(request)
    async with aclosing(storage.iter_chunks(name)) as chunks:
        async for chunk in chunks:
            await response.write(chunk)
    await response.write_eof()
    return response


def create_app(storage: StorageBackend | None = None) -> web.Application:
    app = web.Application()
    app[STORAGE] = storage if storage is not None else MemoryStorage()
    app.router.add_get("/files/{name:.*}", download)
    return app


if __name__ == "__main__":
    # Seed demo content before accepting requests. Use a durable backend in an app.
    async def main() -> web.Application:
        storage = MemoryStorage()
        await storage.save("hello.txt", b"Hello from aiohttp!")
        return create_app(storage)

    web.run_app(main(), host="127.0.0.1", port=8080)
