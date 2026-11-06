"""Structural interface for dependency injection without inheriting StorageBackend."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Protocol

from .types import ConflictPolicy, Content, FileMetadata


class AsyncStorage(Protocol):
    async def save(
        self,
        name: str | None,
        content: Content,
        max_length: int | None = None,
        *,
        on_conflict: ConflictPolicy = "rename",
    ) -> str: ...

    def iter_chunks(
        self, name: str, chunk_size: int = 256 * 1024
    ) -> AsyncIterator[bytes]: ...

    async def read(self, name: str) -> bytes: ...

    async def delete(self, name: str, raise_on_missing: bool = False) -> bool: ...

    async def exists(self, name: str) -> bool: ...

    async def listdir(self, path: str = "") -> tuple[list[str], list[str]]: ...

    async def size(self, name: str) -> int: ...

    async def get_modified_time(self, name: str) -> datetime: ...

    async def metadata(self, name: str) -> FileMetadata: ...
