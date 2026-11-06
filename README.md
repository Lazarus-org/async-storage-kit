# async-storage-kit

**One async interface for files on disk, in memory, and in S3-compatible storage.**

[![PyPI version](https://img.shields.io/pypi/v/async-storage-kit?style=flat-square&logo=pypi&logoColor=white)](https://pypi.org/project/async-storage-kit/)
[![Python versions](https://img.shields.io/pypi/pyversions/async-storage-kit?style=flat-square&logo=python&logoColor=white)](https://pypi.org/project/async-storage-kit/)
[![License](https://img.shields.io/github/license/Lazarus-org/async-storage-kit?style=flat-square&color=blue)](LICENSE)

[![CI](https://github.com/Lazarus-org/async-storage-kit/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Lazarus-org/async-storage-kit/actions/workflows/ci.yml)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-FAB040?style=flat-square&logo=precommit&logoColor=white)](.pre-commit-config.yaml)
[![Ruff](https://img.shields.io/badge/lint_%26_format-Ruff-D7FF64?style=flat-square&logo=ruff&logoColor=white)](https://docs.astral.sh/ruff/)
[![codecov](https://codecov.io/gh/lazarus-org/async-storage-kit/branch/main/graph/badge.svg)](https://codecov.io/gh/lazarus-org/async-storage-kit)
[![Typed](https://img.shields.io/badge/typing-py.typed-3776AB?style=flat-square)](src/async_storage_kit/py.typed)

[![API documentation](https://img.shields.io/badge/docs-API_reference-0078D4?style=flat-square&logo=readthedocs&logoColor=white)](docs/API.md)
[![Last commit](https://img.shields.io/github/last-commit/Lazarus-org/async-storage-kit?style=flat-square)](https://github.com/Lazarus-org/async-storage-kit/commits/main/)
[![Open issues](https://img.shields.io/github/issues/Lazarus-org/async-storage-kit?style=flat-square)](https://github.com/Lazarus-org/async-storage-kit/issues)

Use the same API in asyncio applications, background workers, Django, aiohttp,
and FastAPI.
The core package has **zero mandatory runtime dependencies**. Install S3 and
FastAPI support only when you need them.

## Contents

- [Why async-storage-kit?](#why-async-storage-kit)
- [Installation](#installation)
- [Quick start](#quick-start)
- [Choose a backend](#choose-a-backend)
- [Common operations](#common-operations)
- [Streaming](#streaming)
- [Conflicts and errors](#conflicts-and-errors)
- [Multiple backends](#multiple-backends)
- [Framework integrations](#framework-integrations)
  - [FastAPI](#fastapi)
  - [Django under ASGI](#django-under-asgi)
  - [aiohttp](#aiohttp)
  - [Background workers and CLIs](#background-workers-and-clis)
- [Security and operations](#security-and-operations)
- [Contributing and tests](#contributing-and-tests)
- [Documentation and community](#documentation-and-community)

## Why async-storage-kit?

- **A consistent API:** save, read, stream, list, delete, and inspect metadata
  without changing application code for each backend.
- **Explicit configuration:** pass backend instances, factories, or configuration
  mappings; no global settings or framework dependency is required.
- **Streaming support:** upload bytes, binary files, or async byte iterables;
  download in bounded chunks.
- **Predictable collisions:** choose rename, overwrite, or error behavior.
- **Optional integrations:** S3 SDKs and FastAPI stay separate from core imports.

## Installation

Requires **CPython 3.11 or later**. CI targets CPython 3.11–3.15 on Linux, Windows,
and macOS.

```bash
pip install async-storage-kit
```

| What you need | Install |
| --- | --- |
| Filesystem and memory storage | `pip install async-storage-kit` |
| S3-compatible storage | `pip install 'async-storage-kit[s3]'` |
| FastAPI integration dependency | `pip install 'async-storage-kit[fastapi]'` |
| S3 and FastAPI together | `pip install 'async-storage-kit[s3,fastapi]'` |

From a source checkout, use `python -m pip install .` or
`python -m pip install '.[s3,fastapi]'`.

## Quick start

Save, inspect, and delete a file with the filesystem backend:

```python
import asyncio
from tempfile import TemporaryDirectory

from async_storage_kit import FileSystemStorage

async def main():
    with TemporaryDirectory() as directory:
        storage = FileSystemStorage(directory, base_url="/media/")
        name = await storage.save("documents/report.txt", b"Hello!")

        assert await storage.read(name) == b"Hello!"
        assert (await storage.metadata(name)).size == 6
        assert await storage.listdir("documents") == ([], ["report.txt"])
        assert storage.url(name) == "/media/documents/report.txt"

        await storage.delete(name)

asyncio.run(main())
```

Use a persistent directory such as `"./media"` in your application. The temporary
directory above keeps the example self-contained. `base_url` constructs URL
strings; your application is responsible for mounting and authorizing HTTP access.

## Choose a backend

| Backend | Best suited to | Persistence | Optional extra |
| --- | --- | --- | --- |
| `FileSystemStorage` | Local uploads and application-managed directories | Local filesystem | None |
| `MemoryStorage` | Tests, demos, and temporary application data | None; data stays in RAM | None |
| `S3Storage` | Object storage and compatible services | Configured bucket | `[s3]` |

### Filesystem

`FileSystemStorage` stages complete files before publication and uses no-clobber
publication for new objects. Supported POSIX hosts use descriptor-relative
operations that reject symlinks. Windows and other portable fallback hosts require
exclusive application control of the root and its parent directories.

Filesystem names reject Windows devices, alternate streams, forbidden characters,
and trailing dots/spaces on all platforms. `path()` returns a convenience pathname,
not a secure descriptor. See [security and operations](#security-and-operations).

### Memory

`MemoryStorage()` implements the same storage contract, stores all content in RAM,
and keeps instances independent. It is useful for replacing durable storage in
tests. See the [streaming example](#streaming) for a complete runnable example.

### S3-compatible storage

Set `STORAGE_BUCKET` to an existing bucket. Credentials use the normal AWS SDK
provider chain, including workload roles and standard AWS environment variables.

```python
import asyncio
import os

from async_storage_kit import S3Storage

async def main():
    storage = S3Storage(
        bucket_name=os.environ["STORAGE_BUCKET"],
        region_name=os.getenv("AWS_REGION", "us-east-1"),
        endpoint_url=os.getenv("S3_ENDPOINT"),
        addressing_style="path",
        location="uploads",
    )
    name = await storage.save("reports/example.txt", b"Hello S3")
    assert await storage.read(name) == b"Hello S3"

    metadata = await storage.metadata(name)
    assert metadata.size == 8

    signed_url = await storage.get_presigned_url(name, expire=900)
    assert signed_url  # Share only with authorized callers; never log it.

    # Clean up the demo object. A signed URL needs the object to remain available.
    await storage.delete(name)

asyncio.run(main())
```

For MinIO or another compatible endpoint, set `S3_ENDPOINT`, use path-style
addressing, and provide the service's credentials through the standard AWS
variables or explicit `access_key`, `secret_key`, and optional `session_token`.
Use TLS and least-privilege workload credentials in production.

Bucket provisioning is explicit: uploads never create buckets. Provision your
bucket separately or call `await storage.create_bucket()` from administration
code. S3 metadata also includes content type, user metadata, and ETag; **ETag is
not always an MD5 checksum**.

Public `url()` generation requires both `public_urls=True` and `custom_domain`.
These options do not change bucket access policy. Use `get_presigned_url()` for
time-limited access to private objects and treat those URLs as credentials.

Conditional writes require provider support for both `PutObject` and
`CompleteMultipartUpload`. Test your provider's behavior before relying on
collision guarantees. CI exercises a pinned [PGSTY SILO](https://github.com/pgsty/silo)
service, a MinIO fork; that test alone does not establish AWS production compatibility.

## Common operations

| Operation | Result |
| --- | --- |
| `await storage.save(name, content)` | Save content and return the committed name |
| `await storage.read(name)` | Read the entire object as bytes |
| `storage.iter_chunks(name, chunk_size=262144)` | Iterate asynchronously over download chunks |
| `await storage.exists(name)` | Check whether an object exists |
| `await storage.delete(name)` | Delete an object; return whether it existed |
| `await storage.listdir(path="")` | Return sorted immediate `(directories, files)` |
| `await storage.size(name)` | Get size in bytes |
| `await storage.metadata(name)` | Get name, size, and UTC modification time |
| `storage.url(name)` | Build a public URL when explicitly supported/configured |
| `await storage.get_presigned_url(name, expire=3600)` | Generate a temporary S3 read URL |

Backend-specific capabilities are explicit. For example, `path()` is a filesystem
capability, while presigned URLs are an S3 capability. See the
[API reference](docs/API.md) for signatures, options, metadata types, and exceptions.

## Streaming

Disk and S3 backends accept bytes, bytearray, memoryview, binary file-like objects,
and async byte iterables. Here is a complete in-memory example of the same upload
and download interface:

```python
import asyncio

from async_storage_kit import MemoryStorage

async def main():
    storage = MemoryStorage()

    async def content():
        yield b"first-"
        yield b"second"

    name = await storage.save("stream.bin", content())
    chunks = [chunk async for chunk in storage.iter_chunks(name, chunk_size=4)]
    assert b"".join(chunks) == b"first-second"
    assert await storage.size(name) == 12

asyncio.run(main())
```

Use `iter_chunks()` for large downloads; `read()` buffers the complete object.
The example collects chunks only to check the output. In an application, process
or forward each chunk as it arrives. S3 upload memory usage depends on the
configured multipart threshold and part size.

The caller owns input files: storage consumes from their current position without
closing or rewinding them. Synchronous reads run in worker threads, and cancellation
waits for an outstanding read before returning ownership. Close a partially consumed
download iterator with `await iterator.aclose()` or `contextlib.aclosing` to release
its descriptor or response body promptly.

## Conflicts and errors

`save()` defaults to `on_conflict="rename"`. Select `"error"` to reject a collision
or `"overwrite"` to replace the existing object:

```python
import asyncio

from async_storage_kit import FileAlreadyExists, FileNotFound, MemoryStorage

async def main():
    storage = MemoryStorage()
    await storage.save("avatar.png", b"first")

    renamed = await storage.save("avatar.png", b"second")
    assert renamed != "avatar.png"

    try:
        await storage.save("avatar.png", b"third", on_conflict="error")
    except FileAlreadyExists:
        pass  # Handle the conflict in your application.

    await storage.save("avatar.png", b"replacement", on_conflict="overwrite")

    try:
        await storage.read("missing")
    except FileNotFound:
        pass  # Handle a missing object in your application.

asyncio.run(main())
```

Keys are relative POSIX-style names. Absolute paths, traversal, empty/dot
components, control characters, and reserved staging names are rejected.
Backslashes become separators. Input producer exceptions abort writes and propagate.

An S3 writer that loses a rename race raises `FileAlreadyExists`: a one-shot input
cannot safely be replayed. Deletion can race with replacement and is not an atomic
compare-and-delete.

## Multiple backends

Register instances, factories, or mappings with `backend` and `options`:

```python
from async_storage_kit import MemoryStorage, StorageRegistry

registry = StorageRegistry({
    "default": MemoryStorage(),
    "archive": {
        "backend": "async_storage_kit.backends.s3.S3Storage",
        "options": {"bucket_name": "private-archive"},
    },
})

assert registry["default"] is registry["default"]
```

Construction is lazy and cached per registry. Legacy `BACKEND`/`OPTIONS` keys are
also accepted. Dotted imports and factories execute trusted application code;
never accept registry definitions directly from untrusted request data.

## Framework integrations

Storage backends are framework-independent. Call the same `save()`, `read()`, and
`iter_chunks()` methods from any async view, handler, worker, or scheduled job.
Only FastAPI has an optional dependency helper; other frameworks can use backend
instances directly.

| Application | Pattern | Complete example |
| --- | --- | --- |
| Plain asyncio / CLI | Run an async entry point | [Basic storage](examples/basic.py) |
| FastAPI | Inject a backend with `Depends` | [S3 streaming route](examples/fastapi_s3.py) |
| Django (ASGI) | Return an async `StreamingHttpResponse` | [Async view and URLs](examples/django_storage.py) |
| aiohttp | Write chunks into a `StreamResponse` | [Runnable server](examples/aiohttp_storage.py) |
| Async worker / scheduler | Pass storage into a job and await it | [Background report job](examples/background_worker.py) |

The web snippets use process-local `MemoryStorage` for clarity. Replace it with
`FileSystemStorage` or `S3Storage` for persistence, and keep the configured backend
application-scoped. Install your chosen framework separately; Django and aiohttp
are not required by the core package.

### FastAPI

Install `[fastapi]`, create a registry, and inject storage with `Depends`:

```python
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from async_storage_kit import MemoryStorage, StorageBackend, StorageRegistry
from async_storage_kit.integrations.fastapi import storage_dependency

app = FastAPI()
registry = StorageRegistry({"default": MemoryStorage()})
get_storage = storage_dependency(registry)

@app.get("/files/{name:path}")
async def download(name: str, storage: Annotated[StorageBackend, Depends(get_storage)]):
    # Apply your application's authorization policy before returning private data.
    if not await storage.exists(name):
        raise HTTPException(status_code=404, detail="File not found")
    return StreamingResponse(storage.iter_chunks(name), media_type="application/octet-stream")
```

The example registry is empty until your application saves content. Replace its
backend with `FileSystemStorage` or `S3Storage` as needed. Core imports and the
dependency helper do not import FastAPI or the S3 SDK eagerly. See the
[complete S3 streaming route](examples/fastapi_s3.py) for another example.

### Django under ASGI

Use this in a Django project's views/URL configuration (`pip install django`).
Serve the project under **ASGI** so Django consumes the download asynchronously:

```python
from django.http import Http404, StreamingHttpResponse
from django.urls import path

from async_storage_kit import MemoryStorage

storage = MemoryStorage()

async def download(request, name):
    # Apply the application's authorization policy before reading private content.
    if not await storage.exists(name):
        raise Http404("File not found")
    return StreamingHttpResponse(
        storage.iter_chunks(name), content_type="application/octet-stream"
    )

urlpatterns = [path("files/<path:name>", download)]
```

This is an async view example, not a drop-in adapter for Django's synchronous
`Storage`/`FileField` API. Django's WSGI streaming path expects a synchronous
iterator; use ASGI for this example. See the
[Django streaming documentation](https://docs.djangoproject.com/en/6.0/ref/request-response/#streaminghttpresponse-objects).

### aiohttp

Install `aiohttp` and stream chunks into its response:

```python
from contextlib import aclosing

from aiohttp import web
from async_storage_kit import MemoryStorage

storage = MemoryStorage()

async def download(request):
    name = request.match_info["name"]
    # Apply the application's authorization policy before reading private content.
    if not await storage.exists(name):
        raise web.HTTPNotFound(text="File not found")
    response = web.StreamResponse(headers={"Content-Type": "application/octet-stream"})
    await response.prepare(request)
    async with aclosing(storage.iter_chunks(name)) as chunks:
        async for chunk in chunks:
            await response.write(chunk)
    await response.write_eof()
    return response

app = web.Application()
app.router.add_get("/files/{name:.*}", download)
```

For a runnable server with seeded demo content, use
[`examples/aiohttp_storage.py`](examples/aiohttp_storage.py). It listens on
`127.0.0.1:8080`; request `/files/hello.txt`. See the
[aiohttp streaming API](https://docs.aiohttp.org/en/stable/web_reference.html#streamresponse).

### Background workers and CLIs

Pass a backend into an async job; no web framework or dependency helper is needed:

```python
import asyncio
from collections.abc import AsyncIterable

from async_storage_kit import AsyncStorage, MemoryStorage

async def write_report(storage: AsyncStorage, rows: AsyncIterable[bytes]) -> str:
    return await storage.save("reports/status.csv", rows, on_conflict="overwrite")

async def main():
    storage = MemoryStorage()

    async def rows():
        yield b"id,status\n"
        yield b"1,ready\n"

    name = await write_report(storage, rows())
    assert await storage.read(name) == b"id,status\n1,ready\n"

asyncio.run(main())
```

`asyncio.run()` belongs at a synchronous CLI/process entry point. Inside an
existing event loop—an async worker, view, or scheduler—await the job directly.
See the [filesystem-backed report example](examples/background_worker.py) and
[example setup instructions](examples/README.md).

## Security and operations

- **Authorize access in your application.** Storage does not provide authentication,
  quotas, malware scanning, content validation, backups, or HTTP access control.
- **Protect filesystem roots and ancestors.** POSIX descriptor operations do not
  defend against every hostile directory rename. Portable fallback checks are not
  race-resistant against untrusted local users.
- **Choose suitable permissions.** Windows chmod semantics do not implement POSIX ACLs.
- **Plan for interrupted writes.** Atomic visibility is not crash durability. Configure
  bucket lifecycle cleanup for incomplete multipart uploads; abrupt termination or
  abort failure can leave uploads behind.
- **Bound resource use.** MemoryStorage and `read()` materialize content. Large multipart
  settings still consume substantial memory. Deferred cancellation can wait for I/O;
  configure provider timeouts and monitor cleanup failures.

Read [SECURITY.md](SECURITY.md) for the full threat model and private reporting contacts.

## Contributing and tests

Install the locked tools and run the offline checks:

```bash
uv sync --locked
uv run --locked pre-commit install
uv run --locked pytest --cov=async_storage_kit --cov-branch -m "not live_s3"
uv run --locked ruff check src tests examples .github/scripts
uv run --locked mypy src/
```

Once dependencies are installed, offline tests need no endpoint, Docker, network,
or cloud credentials. CI requires **100% statement and branch coverage** after
combining the platform matrix, including both filesystem drivers.

For live integration tests, provision a **disposable** bucket and configure
`S3_TEST_ENDPOINT`, `S3_TEST_BUCKET`, `S3_TEST_ACCESS_KEY`, and `S3_TEST_SECRET_KEY`:

```bash
uv run --locked pytest -q -m live_s3
```

These tests write/delete objects and inspect incomplete multipart uploads.
A configured unavailable endpoint fails rather than skips. Follow the
[contribution guide](CONTRIBUTING.md) for checks and pull requests, and the
[step-by-step workflow](CODE_OF_CONDUCT.md#contribution-workflow) for commits.
Release versioning, tags, and publishing are reserved for authorized maintainers.

## Documentation and community

| Resource | Find |
| --- | --- |
| [API reference](docs/API.md) | Parameters, types, backend capabilities, and errors |
| [Runnable examples](examples/README.md) | Asyncio, FastAPI, Django, aiohttp, and background jobs |
| [Security policy](SECURITY.md) | Threat model and private vulnerability reporting |
| [Changelog](CHANGELOG.md) | Release notes |
| [Contributing](CONTRIBUTING.md) | Setup, checks, commit conventions, and pull requests |
| [Code of conduct and workflow](CODE_OF_CONDUCT.md) | Community expectations and step-by-step instructions |
| [Issue tracker](https://github.com/Lazarus-org/async-storage-kit/issues) | Bug reports and feature requests |

Licensed under the [MIT License](LICENSE). See [AUTHORS.md](AUTHORS.md) and
[CONTRIBUTORS.md](CONTRIBUTORS.md) for the people behind the project.
