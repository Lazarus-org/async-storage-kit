"""Deterministic in-memory storage suitable for tests and ephemeral data."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timezone

from ..base import StorageBackend
from ..exceptions import FileAlreadyExists, FileNotFound
from ..streams import iter_content
from ..types import ConflictPolicy, Content
from ..utils import normalize_name


class MemoryStorage(StorageBackend):
    def __init__(self) -> None:
        self._objects: dict[str, tuple[bytes, datetime]] = {}
        self._lock = asyncio.Lock()

    async def _save(
        self,
        name: str,
        content: Content,
        *,
        max_length: int | None,
        on_conflict: ConflictPolicy,
    ) -> str:
        # Memory is necessarily materialized in this backend, unlike the disk backend.
        data = b"".join([chunk async for chunk in iter_content(content)])
        async with self._lock:
            candidate = name
            if on_conflict == "error" and name in self._objects:
                raise FileAlreadyExists(name)
            if on_conflict == "rename":
                for _ in range(100):
                    if candidate not in self._objects:
                        break
                    candidate = self.get_alternative_name(name, max_length)
                else:
                    raise FileAlreadyExists("Unable to allocate a unique storage name.")
            self._objects[candidate] = (data, datetime.now(timezone.utc))
            return candidate

    async def read(self, name: str) -> bytes:
        name = normalize_name(name)
        try:
            return self._objects[name][0]
        except KeyError as exc:
            raise FileNotFound(name) from exc

    async def iter_chunks(
        self, name: str, chunk_size: int = 256 * 1024
    ) -> AsyncIterator[bytes]:
        if chunk_size < 1:
            raise ValueError("chunk_size must be positive.")
        data = await self.read(name)
        for offset in range(0, len(data), chunk_size):
            yield data[offset : offset + chunk_size]

    async def delete(self, name: str, raise_on_missing: bool = False) -> bool:
        name = normalize_name(name)
        async with self._lock:
            if name in self._objects:
                del self._objects[name]
                return True
        if raise_on_missing:
            raise FileNotFound(name)
        return False

    async def exists(self, name: str) -> bool:
        return normalize_name(name) in self._objects

    async def listdir(self, path: str = "") -> tuple[list[str], list[str]]:
        path = normalize_name(path, allow_empty=True)
        prefix = f"{path}/" if path else ""
        directories: set[str] = set()
        files: set[str] = set()
        for key in self._objects:
            if not key.startswith(prefix):
                continue
            tail = key[len(prefix) :]
            if "/" in tail:
                directories.add(tail.split("/", 1)[0])
            else:
                files.add(tail)
        return sorted(directories), sorted(files)

    async def size(self, name: str) -> int:
        return len(await self.read(name))

    async def get_modified_time(self, name: str) -> datetime:
        name = normalize_name(name)
        try:
            return self._objects[name][1]
        except KeyError as exc:
            raise FileNotFound(name) from exc

    async def get_created_time(self, name: str) -> datetime:
        return await self.get_modified_time(name)
