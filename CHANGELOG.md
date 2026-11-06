# Changelog

Notable changes to async-storage-kit are recorded here. Versions follow
[Semantic Versioning](https://semver.org/).

## 1.0.0 — Initial release

- Framework-independent async file storage for Python 3.11 and newer, with no
  mandatory runtime dependencies.
- Filesystem and in-memory backends, plus optional S3-compatible storage.
- A shared typed interface for saving, reading, streaming, listing, deleting,
  and retrieving object metadata.
- Bytes, binary file-like objects, and async byte iterables as upload inputs.
- Rename, overwrite, and error policies for filename collisions.
- Staged filesystem writes, storage-key validation, and symlink rejection, with
  descriptor-relative operations on supported POSIX systems.
- S3 multipart uploads, conditional writes, configurable object metadata, and
  presigned download URLs.
- Explicit storage registries with lazy backend construction and named factories.
- An optional FastAPI dependency helper and runnable integration examples.
- Public API documentation, usage examples, and security guidance.
