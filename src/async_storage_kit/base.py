"""Framework-independent asynchronous file storage interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from datetime import datetime

from .exceptions import SuspiciousFileOperation, UnsupportedOperation
from .types import ConflictPolicy, Content, FileMetadata
from .utils import alternative_name, get_valid_filename, normalize_name


class StorageBackend(ABC):
    """Common async contract; subclasses own collision handling atomically."""

    async def save(
        self,
        name: str | None,
        content: Content,
        max_length: int | None = None,
        *,
        on_conflict: ConflictPolicy = "rename",
    ) -> str:
        if name is None:
            inferred = getattr(content, "name", None)
            if not isinstance(inferred, str):
                raise SuspiciousFileOperation(
                    "Supply a storage name for unnamed content."
                )
            name = inferred
        name = normalize_name(name)
        if max_length is not None and max_length < 1:
            raise ValueError("max_length must be positive.")
        if on_conflict not in {"rename", "overwrite", "error"}:
            raise ValueError("on_conflict must be 'rename', 'overwrite' or 'error'.")
        if max_length is not None and len(name) > max_length:
            raise SuspiciousFileOperation("Storage name exceeds max_length.")
        return await self._save(
            name, content, max_length=max_length, on_conflict=on_conflict
        )

    @abstractmethod
    async def _save(
        self,
        name: str,
        content: Content,
        *,
        max_length: int | None,
        on_conflict: ConflictPolicy,
    ) -> str:
        """Persist content and return the committed key."""

    @abstractmethod
    def iter_chunks(
        self, name: str, chunk_size: int = 256 * 1024
    ) -> AsyncIterator[bytes]:
        """Yield a stored object's bytes in bounded chunks."""

    async def read(self, name: str) -> bytes:
        """Read an entire object; use iter_chunks() for large files."""
        return b"".join([chunk async for chunk in self.iter_chunks(name)])

    @abstractmethod
    async def delete(self, name: str, raise_on_missing: bool = False) -> bool:
        """Delete an object; return False if it was already missing."""

    @abstractmethod
    async def exists(self, name: str) -> bool:
        """Return True when the object exists."""

    @abstractmethod
    async def listdir(self, path: str = "") -> tuple[list[str], list[str]]:
        """List immediate child directories and files, sorted by name."""

    @abstractmethod
    async def size(self, name: str) -> int:
        """Get stored size in bytes."""

    @abstractmethod
    async def get_modified_time(self, name: str) -> datetime:
        """Return a timezone-aware UTC modification time."""

    async def metadata(self, name: str) -> FileMetadata:
        name = normalize_name(name)
        return FileMetadata(
            name, await self.size(name), await self.get_modified_time(name)
        )

    def get_valid_name(self, name: str) -> str:
        return get_valid_filename(name)

    def generate_filename(self, name: str) -> str:
        name = normalize_name(name)
        prefix, sep, filename = name.rpartition("/")
        return prefix + sep + self.get_valid_name(filename)

    def get_alternative_name(self, name: str, max_length: int | None = None) -> str:
        return alternative_name(name, max_length=max_length)

    async def is_name_available(self, name: str, max_length: int | None = None) -> bool:
        name = normalize_name(name)
        return (
            max_length is None or len(name) <= max_length
        ) and not await self.exists(name)

    async def get_available_name(self, name: str, max_length: int | None = None) -> str:
        name = normalize_name(name)
        if max_length is not None and len(name) > max_length:
            raise SuspiciousFileOperation("Storage name exceeds max_length.")
        candidate = name
        for _ in range(100):
            if await self.is_name_available(candidate, max_length):
                return candidate
            candidate = self.get_alternative_name(name, max_length)
        raise SuspiciousFileOperation("Could not allocate an available filename.")

    def url(self, name: str) -> str:
        raise UnsupportedOperation("This backend does not provide public URLs.")

    async def get_presigned_url(self, name: str, expire: int = 3600) -> str:
        raise UnsupportedOperation("This backend does not provide signed URLs.")

    def path(self, name: str) -> str:
        raise UnsupportedOperation("This backend does not expose filesystem paths.")

    async def get_accessed_time(self, name: str) -> datetime:
        raise UnsupportedOperation("This backend does not track access time.")

    async def get_created_time(self, name: str) -> datetime:
        raise UnsupportedOperation("This backend does not track creation time.")
