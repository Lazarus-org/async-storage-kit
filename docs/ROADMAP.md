# Roadmap

## Design principles

- Preserve one typed async contract across filesystem, memory, and S3 storage.
- Keep filesystem and memory backends free of mandatory runtime dependencies.
- Keep optional SDK imports lazy and backend construction explicit.
- Prefer predictable errors, bounded streaming, and safe collision handling.
- Document provider and filesystem security boundaries precisely.

## Areas for future work

- Extend provider-specific contract and fault-injection coverage.
- Evaluate versioned-object deletion and additional provider capabilities.
- Improve guidance for filesystem crash durability and operator-managed cleanup.
- Expand performance measurements for large streamed objects.

Authorization, quotas, content validation, malware scanning, and backups remain
application responsibilities. Features should not introduce implicit global
settings, automatic bucket creation, or public ACL changes.

Changes follow [the contribution guide](../CONTRIBUTING.md). Release selection,
versioning, and publication follow [maintainer release workflow](../CODE_OF_CONDUCT.md#maintainer-release-workflow).
