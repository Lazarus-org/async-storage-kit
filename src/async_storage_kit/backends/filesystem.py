"""Framework-neutral asynchronous filesystem storage.

On supported POSIX systems, operations use directory-relative descriptors and
reject symbolic links. Other platforms use a compatibility driver that requires
storage directories to be exclusively controlled by the application.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urljoin

from ..base import StorageBackend
from ..exceptions import FileNotFound, UnsupportedOperation
from ..types import ConflictPolicy, Content
from ..utils import normalize_filesystem_name
from ._filesystem_portable import PortableFileIO, validated_path
from ._filesystem_posix import PosixFileIO, is_supported


class FileSystemStorage(StorageBackend):
    """Async file storage with collision-safe, staged writes.

    A successful save publishes a complete file, not an in-progress upload.
    When supported, dirfd/openat/no-follow APIs protect individual operations
    from symlink substitution. Storage root parent directories must be trusted.
    """

    def __init__(
        self,
        location: str | os.PathLike[str],
        base_url: str | None = None,
        file_permissions_mode: int | None = 0o644,
        directory_permissions_mode: int | None = 0o755,
    ) -> None:
        for label, mode in (
            ("file_permissions_mode", file_permissions_mode),
            ("directory_permissions_mode", directory_permissions_mode),
        ):
            if mode is not None and (
                not isinstance(mode, int) or not 0 <= mode <= 0o777
            ):
                raise ValueError(
                    f"{label} must be an integer between 0o000 and 0o777, or None."
                )

        self.location = str(Path(location).resolve())
        self.base_url = base_url.rstrip("/") + "/" if base_url is not None else None
        self.file_permissions_mode = file_permissions_mode
        self.directory_permissions_mode = directory_permissions_mode
        Path(self.location).mkdir(parents=True, exist_ok=True)
        driver = PosixFileIO if is_supported() else PortableFileIO
        self._io = driver(
            self.location, file_permissions_mode, directory_permissions_mode
        )

    @property
    def symlink_race_protection(self) -> bool:
        """Whether this host supports dirfd-anchored no-follow file operations."""
        return isinstance(self._io, PosixFileIO)

    def path(self, name: str) -> str:
        """Return a convenience path; not a race-safe access capability."""
        return validated_path(self.location, normalize_filesystem_name(name))

    async def _save(
        self,
        name: str,
        content: Content,
        *,
        max_length: int | None,
        on_conflict: ConflictPolicy,
    ) -> str:
        name = normalize_filesystem_name(name)
        return await self._io.save(
            name, content, max_length=max_length, on_conflict=on_conflict
        )

    async def iter_chunks(
        self, name: str, chunk_size: int = 256 * 1024
    ) -> AsyncIterator[bytes]:
        if chunk_size < 1:
            raise ValueError("chunk_size must be positive.")
        async for chunk in self._io.iter_chunks(
            normalize_filesystem_name(name), chunk_size
        ):
            yield chunk

    async def delete(self, name: str, raise_on_missing: bool = False) -> bool:
        name = normalize_filesystem_name(name)
        deleted = await self._io.delete(name)
        if not deleted and raise_on_missing:
            raise FileNotFound(name)
        return deleted

    async def exists(self, name: str) -> bool:
        return await self._io.exists(normalize_filesystem_name(name))

    async def listdir(self, path: str = "") -> tuple[list[str], list[str]]:
        return await self._io.listdir(normalize_filesystem_name(path, allow_empty=True))

    async def _stat(self, name: str) -> os.stat_result:
        return await self._io.file_stat(normalize_filesystem_name(name))

    async def size(self, name: str) -> int:
        return (await self._stat(name)).st_size

    async def get_modified_time(self, name: str) -> datetime:
        return datetime.fromtimestamp((await self._stat(name)).st_mtime, timezone.utc)

    async def get_accessed_time(self, name: str) -> datetime:
        return datetime.fromtimestamp((await self._stat(name)).st_atime, timezone.utc)

    async def get_created_time(self, name: str) -> datetime:
        stat = await self._stat(name)
        birth = getattr(stat, "st_birthtime", None)
        if birth is None:
            raise UnsupportedOperation(
                "This platform does not expose file birth times."
            )
        return datetime.fromtimestamp(birth, timezone.utc)

    def url(self, name: str) -> str:
        if self.base_url is None:
            raise UnsupportedOperation(
                "No base_url has been configured for this storage."
            )
        name = normalize_filesystem_name(name)
        encoded = "/".join(quote(segment, safe="") for segment in name.split("/"))
        return urljoin(self.base_url, encoded)
