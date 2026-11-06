# Storage examples

Each example uses the same async storage API. Install async-storage-kit first
(`python -m pip install -e .` from a checkout), then install any framework you use.

| Example | Requirements | Run or integrate |
| --- | --- | --- |
| [Basic asyncio](basic.py) | Core package | `python examples/basic.py` |
| [Background report job](background_worker.py) | Core package | `python examples/background_worker.py`; await `write_report()` in an existing async worker |
| [FastAPI with S3](fastapi_s3.py) | `[s3,fastapi]` and an ASGI server | Load `app` with your ASGI server; configure `APP_STORAGE_BUCKET` and AWS credentials |
| [Django async views](django_storage.py) | `django>=5.2` | Include `urlpatterns` in your Django project and serve the project under ASGI |
| [aiohttp streaming](aiohttp_storage.py) | `aiohttp>=3.9` | `python examples/aiohttp_storage.py`, then visit `http://127.0.0.1:8080/files/hello.txt` |

## Choosing storage

The Django and aiohttp examples use MemoryStorage to keep setup small. Data is
process-local and disappears when the process exits. Replace it with a configured
FileSystemStorage or S3Storage for persistent data. Share a backend within the
application's event loop instead of creating a new memory store per request.

Django receives an async iterator and must serve this streaming view under ASGI.
This package's backend is not a replacement for Django's synchronous Storage API
or a drop-in `FileField` storage adapter.

Authorization and access policy belong in the application. Upload parsing,
request limits, and content validation are framework/application concerns.
The HTTP examples demonstrate downloads; seed data through `await storage.save()`
or your application's upload/job flow. S3 bucket provisioning is explicit.

`asyncio.run()` is only for a synchronous process entry point. Inside a running
async view, worker, or scheduler, await storage operations or `write_report()`
directly. Frameworks in these examples are independent dependencies; the core
package does not require Django, aiohttp, or FastAPI.

## Framework references

- [Django async streaming responses](https://docs.djangoproject.com/en/6.0/ref/request-response/#streaminghttpresponse-objects)
- [aiohttp streaming responses](https://docs.aiohttp.org/en/stable/web_reference.html#streamresponse)
