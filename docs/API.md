# Public API reference

The supported baseline is Python 3.11+. All storage operations are async except
construction, URL/path construction, and the `iter_chunks` iterator factory.

## `StorageBackend`

The abstract base class defines a consistent common contract. The structural
`AsyncStorage` typing protocol may be used when implementing an adapter without
inheritance.

- `await save(name: str | None, content: Content, max_length: int | None = None, *, on_conflict="rename") -> str`
- `iter_chunks(name: str, chunk_size: int = 262144) -> AsyncIterator[bytes]`
- `await read(name: str) -> bytes`
- `await delete(name: str, raise_on_missing: bool = False) -> bool`
- `await exists(name: str) -> bool`
- `await listdir(path: str = "") -> tuple[list[str], list[str]]`
- `await size(name: str) -> int`
- `await get_modified_time(name: str) -> datetime`
- `await metadata(name: str) -> FileMetadata`

`FileMetadata` contains `name`, `size`, and timezone-aware UTC `modified_at`.

`Content` supports byte buffers, synchronous binary file-like objects, and
asynchronous iterables of bytes. The caller owns and closes file-like inputs;
the library consumes from their current position without rewinding. A
producer that throws an exception aborts the write; no partial key should be
published for new or overwritten objects on a supported backend.

`max_length` counts characters in logical storage names, **not** filesystem
encoded bytes. Filesystem-level filename/volume limits are still enforced by
operating systems, and errors can occur for overlong encoded names.

## `FileSystemStorage(location, base_url=None, file_permissions_mode=0o644, directory_permissions_mode=0o755)`

A local async backend implemented with standard-library offloading. Additional
capabilities are `path(name)`, `url(name)` when `base_url` exists,
`await get_accessed_time(name)`, and platform-dependent
`await get_created_time(name)`.

The `.path()` result is not a secure handle; use storage operations when
processing untrusted names. POSIX no-follow directory descriptor behavior is
reported by `storage.symlink_race_protection`. Portable fallback protections
are weaker: see [SECURITY.md](../SECURITY.md).

## `MemoryStorage()`

An ephemeral process-local store. It holds bytes in memory and is intended for
tests, demos, and temporary application state rather than durable uploads.

## `S3Storage(bucket_name, ...)`

Additional options include `location`, `region_name`, `endpoint_url`,
`access_key`, `secret_key`, `session_token`, `addressing_style`,
`custom_domain`, `public_urls`, `object_parameters`, `multipart_threshold`,
and `multipart_chunksize`. Both multipart settings are integer byte counts between
5 MiB and 5 GiB; buffering is bounded by those settings, so configure them with
available memory in mind. Endpoint URLs must be HTTP(S) without embedded
credentials, query strings, or fragments. A private cloud SDK context is
created lazily per operation. Optional `[s3]` is required to perform operations.

Additional methods:

- `await get_presigned_url(name, expire=3600)` — generate a temporary read URL.
- `await bucket_exists()` — check the configured bucket.
- `await create_bucket()` — **explicit administration only**, never implicit.

`S3FileMetadata` additionally exposes `etag`, `content_type`, and
`user_metadata`. ETag is **not** a universal MD5 checksum.

S3 has object-store consistency/transaction limitations; deletion is not an
atomic compare-and-delete and a concurrent name race may produce
`FileAlreadyExists` even when `on_conflict="rename"` is requested. Multipart
collision safety requires server support for conditional completion.

## `StorageRegistry`

`StorageRegistry({"alias": storage_or_factory_or_config, ...})` resolves named
backends lazily and caches them. It supports storage instances, zero-argument
factories, or mapping definitions with `backend` and `options` (also uppercase
`BACKEND` and `OPTIONS`). `registry.register(..., replace=True)` changes the
configuration and invalidates its cache entry; `clear_cache()` invalidates all
cached entries. Aliases must be non-empty strings. Recursive factories are
rejected; configuration errors omit constructor messages that may contain secrets.
Dotted imports and factories are trusted executable configuration, not an
untrusted-data boundary.

## Error classes

- `StorageError`: common base for storage errors.
- `SuspiciousFileOperation`: unsafe logical name or path, subclass of `ValueError`.
- `FileNotFound`: requested key missing, subclass of `FileNotFoundError`.
- `FileAlreadyExists`: collision, subclass of `FileExistsError`.
- `InvalidStorageError`: malformed registry configuration.
- `UnsupportedOperation`: optional capability unavailable.

Application content exceptions such as a failed input stream are generally
propagated rather than converted to storage exceptions. Provider errors that
have no portable representation are wrapped in `StorageError` with the original
exception accessible as `__cause__`.

## Platform and streaming details

Filesystem keys reject Windows reserved devices, alternate streams, forbidden
characters, and trailing dots/spaces on all hosts. Memory and S3 names retain
their broader object-key character set. Windows permission settings use Windows
chmod semantics rather than POSIX ACLs. Native operating-system permission and
I/O errors can propagate; input producer exceptions remain unchanged.

Close partially consumed download iterators explicitly with `await iterator.aclose()`
or `contextlib.aclosing` so response bodies and descriptors are released promptly.
Repeated cancellation defers completion until active descriptor/input I/O or
multipart abort attempts finish. Abrupt process termination cannot guarantee cleanup.

The structural `AsyncStorage` protocol includes `metadata()` as well as the shared
read/write/list operations. Listing detects a server repeating its continuation
token and fails with `StorageError` instead of looping indefinitely.
