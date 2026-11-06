"""Django async views: install django and include these URLs in an ASGI project."""

from __future__ import annotations

from django.http import Http404, HttpRequest, StreamingHttpResponse
from django.urls import path

from async_storage_kit import MemoryStorage, StorageBackend

# Keep storage application-scoped. Replace with FileSystemStorage or S3Storage
# for persistent data, and apply application authorization before reading files.
storage: StorageBackend = MemoryStorage()


async def download(request: HttpRequest, name: str) -> StreamingHttpResponse:
    if not await storage.exists(name):
        raise Http404("File not found")
    return StreamingHttpResponse(
        storage.iter_chunks(name), content_type="application/octet-stream"
    )


urlpatterns = [path("files/<path:name>", download, name="download")]
