"""Portable fallback for platforms lacking POSIX dir_fd/no-follow support.

This driver assumes only the application can mutate storage directories. Its
containment checks cannot defend against an adversary concurrently replacing
folders. See SECURITY.md for the platform-specific security boundary.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import stat
from collections.abc import AsyncIterator
from contextlib import suppress
from pathlib import Path

from ..exceptions import (
    FileAlreadyExists,
    FileNotFound,
    StorageError,
    SuspiciousFileOperation,
)
from ..types import ConflictPolicy, Content
from ..utils import alternative_name, safe_join
from ._filesystem_common import close_fd, read_chunks, write_content
from ._filesystem_posix import is_staging_name


def validated_path(location: str, name: str) -> str:
    """Best-effort preflight: reject links even when they resolve inside root."""
    current = Path(location)
    candidates = [current]
    for part in name.split("/") if name else []:
        current = current / part
        candidates.append(current)
    for current in candidates:
        try:
            is_reparse_point = (
                os.name == "nt"
                and current.exists()
                and bool(
                    getattr(os.lstat(current), "st_file_attributes", 0)
                    & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
                )
            )
            if current.is_symlink() or is_reparse_point:
                raise SuspiciousFileOperation(
                    "Links and reparse points cannot be storage paths."
                )
        except OSError as exc:
            raise SuspiciousFileOperation("Unable to validate storage path.") from exc
    return safe_join(location, name)


class PortableFileIO:
    def __init__(
        self,
        location: str,
        file_permissions_mode: int | None,
        directory_permissions_mode: int | None,
    ) -> None:
        self.location = location
        self.file_permissions_mode = file_permissions_mode
        self.directory_permissions_mode = directory_permissions_mode

    def _ensure_parent(self, name: str) -> Path:
        path = Path(validated_path(self.location, name))
        prefix = Path(self.location)
        for component in name.split("/")[:-1]:
            prefix = prefix / component
            if not prefix.exists():
                try:
                    prefix.mkdir()
                except FileExistsError:
                    pass
                else:
                    if self.directory_permissions_mode is not None:
                        prefix.chmod(self.directory_permissions_mode)
        validated_path(self.location, name)
        return path

    async def save(
        self,
        name: str,
        content: Content,
        *,
        max_length: int | None,
        on_conflict: ConflictPolicy,
    ) -> str:
        destination = await asyncio.to_thread(self._ensure_parent, name)
        for _ in range(10):
            temporary = str(
                destination.parent
                / (".async-storage-" + secrets.token_hex(16) + ".tmp")
            )
            try:
                fd = os.open(
                    temporary,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0),
                    0o600,
                )
                break
            except FileExistsError:
                continue
        else:
            raise StorageError("Unable to allocate an upload staging file.")
        try:
            try:
                await write_content(fd, content)
                if self.file_permissions_mode is not None:
                    os.chmod(temporary, self.file_permissions_mode)
            finally:
                close_fd(fd)
            validated_path(self.location, name)
            if on_conflict == "overwrite":
                if destination.exists() and not destination.is_file():
                    raise StorageError("Storage object must be a regular file.")
                os.replace(temporary, destination)
                return name
            candidate = name
            for _ in range(100):
                target = Path(validated_path(self.location, candidate))
                try:
                    if os.name == "nt":
                        # On Windows os.rename does not replace existing destinations.
                        os.rename(temporary, target)
                    else:
                        os.link(temporary, target)
                    return candidate
                except FileExistsError as exc:
                    if on_conflict == "error":
                        raise FileAlreadyExists(candidate) from exc
                    candidate = alternative_name(name, max_length=max_length)
            raise FileAlreadyExists("Unable to allocate a unique storage name.")
        finally:
            with suppress(FileNotFoundError):
                if os.name == "nt":
                    # Windows cannot unlink a staging file marked read-only.
                    os.chmod(temporary, 0o600)
                os.unlink(temporary)

    def _open_regular_file(self, name: str) -> int:
        path = validated_path(self.location, name)
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
        fd = os.open(path, flags)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise StorageError("Storage object must be a regular file.")
        except BaseException:
            close_fd(fd)
            raise
        return fd

    async def iter_chunks(self, name: str, chunk_size: int) -> AsyncIterator[bytes]:
        try:
            fd = self._open_regular_file(name)
        except FileNotFoundError as exc:
            raise FileNotFound(name) from exc
        try:
            async for chunk in read_chunks(fd, chunk_size):
                yield chunk
        finally:
            close_fd(fd)

    def _file_stat(self, name: str) -> os.stat_result:
        path = validated_path(self.location, name)
        info = os.lstat(path)
        if not stat.S_ISREG(info.st_mode):
            raise StorageError("Storage object must be a regular file.")
        return info

    async def file_stat(self, name: str) -> os.stat_result:
        try:
            return await asyncio.to_thread(self._file_stat, name)
        except FileNotFoundError as exc:
            raise FileNotFound(name) from exc

    async def exists(self, name: str) -> bool:
        try:
            await self.file_stat(name)
            return True
        except FileNotFound:
            return False

    def _delete(self, name: str) -> bool:
        try:
            path = validated_path(self.location, name)
            info = os.lstat(path)
        except FileNotFoundError:
            return False
        if not stat.S_ISREG(info.st_mode):
            raise StorageError("delete() only removes regular files.")
        try:
            os.unlink(path)
            return True
        except FileNotFoundError:
            return False

    async def delete(self, name: str) -> bool:
        return await asyncio.to_thread(self._delete, name)

    def _listdir(self, path: str) -> tuple[list[str], list[str]]:
        folder = validated_path(self.location, path)
        dirs: list[str] = []
        files: list[str] = []
        with os.scandir(folder) as entries:
            for entry in entries:
                if is_staging_name(entry.name):
                    continue
                if entry.is_symlink():
                    raise SuspiciousFileOperation(
                        "Storage directory contains a symbolic link."
                    )
                if entry.is_dir(follow_symlinks=False):
                    dirs.append(entry.name)
                elif entry.is_file(follow_symlinks=False):
                    files.append(entry.name)
                else:
                    raise StorageError("Storage directory contains a non-file object.")
        return sorted(dirs), sorted(files)

    async def listdir(self, path: str) -> tuple[list[str], list[str]]:
        try:
            return await asyncio.to_thread(self._listdir, path)
        except FileNotFoundError as exc:
            raise FileNotFound(path) from exc
