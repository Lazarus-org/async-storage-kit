"""FastAPI dependency factory, without importing FastAPI at module import time."""

from __future__ import annotations

from collections.abc import Callable

from ..base import StorageBackend
from ..registry import StorageRegistry


def storage_dependency(
    registry: StorageRegistry, alias: str = "default"
) -> Callable[[], StorageBackend]:
    """Return a callable suitable for ``Depends(storage_dependency(registry))``.

    The registry is application scoped; no global settings or singleton are used.
    """

    def get_storage() -> StorageBackend:
        return registry[alias]

    return get_storage
