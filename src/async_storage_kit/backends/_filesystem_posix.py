"""POSIX filesystem operations anchored to directory descriptors.

Names are looked up relative to opened directory descriptors, never by joining
untrusted components into a path for a filesystem operation. Symlinks are
rejected at every component. Storage root *parents* and directory renames must
still be controlled by the service administrator (see SECURITY.md).
"""

from __future__ import annotations

import asyncio
import errno
import os
import re
import secrets
import stat
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Final

from ..exceptions import (
    FileAlreadyExists,
    FileNotFound,
    StorageError,
    SuspiciousFileOperation,
)
from ..types import ConflictPolicy, Content
from ..utils import alternative_name
from ._filesystem_common import close_fd, read_chunks, write_content

_REQUIRED_DIRFD: Final = (os.open, os.mkdir, os.stat, os.unlink, os.link, os.rename)
_DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
_FILE_READ_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NONBLOCK", 0)
)
_FILE_CREATE_FLAGS = (
    os.O_WRONLY
    | os.O_CREAT
    | os.O_EXCL
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)


_STAGING_PATTERN = re.compile(r"\.async-storage-[0-9a-f]{32}\.tmp\Z")


def is_staging_name(name: str) -> bool:
    return bool(_STAGING_PATTERN.fullmatch(name))


def is_supported() -> bool:
    return (
        os.name == "posix"
        and hasattr(os, "O_NOFOLLOW")
        and hasattr(os, "O_DIRECTORY")
        and all(operation in os.supports_dir_fd for operation in _REQUIRED_DIRFD)
    )


class PosixFileIO:
    """File operations anchored to a verified storage root identity."""

    def __init__(
        self,
        location: str,
        file_permissions_mode: int | None,
        directory_permissions_mode: int | None,
    ) -> None:
        self.location = location
        self.file_permissions_mode = file_permissions_mode
        self.directory_permissions_mode = directory_permissions_mode
        fd = os.open(location, _DIRECTORY_FLAGS)
        try:
            root_stat = os.fstat(fd)
            self._root_identity = (root_stat.st_dev, root_stat.st_ino)
        finally:
            close_fd(fd)

    def _root_fd(self) -> int:
        try:
            fd = os.open(self.location, _DIRECTORY_FLAGS)
        except OSError as exc:
            if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                raise SuspiciousFileOperation(
                    "Storage root is no longer a directory."
                ) from exc
            raise
        try:
            root_stat = os.fstat(fd)
            if (root_stat.st_dev, root_stat.st_ino) != self._root_identity:
                raise SuspiciousFileOperation("Storage root has been replaced.")
        except BaseException:
            close_fd(fd)
            raise
        return fd

    def _directory_fd(self, parts: list[str], *, create: bool) -> int:
        fd = self._root_fd()
        try:
            for part in parts:
                created = False
                try:
                    next_fd = os.open(part, _DIRECTORY_FLAGS, dir_fd=fd)
                except FileNotFoundError:
                    if not create:
                        raise
                    try:
                        os.mkdir(
                            part,
                            self.directory_permissions_mode
                            if self.directory_permissions_mode is not None
                            else 0o777,
                            dir_fd=fd,
                        )
                        created = True
                    except FileExistsError:
                        # Another process may have created this directory.
                        pass
                    try:
                        next_fd = os.open(part, _DIRECTORY_FLAGS, dir_fd=fd)
                    except OSError as exc:
                        self._handle_unsafe_component(exc)
                        raise
                    if created and self.directory_permissions_mode is not None:
                        try:
                            getattr(os, "fchmod")(  # noqa: B009 - API is absent from Windows stubs.
                                next_fd, self.directory_permissions_mode
                            )
                        except BaseException:
                            close_fd(next_fd)
                            raise
                except OSError as exc:
                    self._handle_unsafe_component(exc)
                    raise
                close_fd(fd)
                fd = next_fd
            return fd
        except BaseException:
            close_fd(fd)
            raise

    @staticmethod
    def _handle_unsafe_component(exc: OSError) -> None:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise SuspiciousFileOperation(
                "Storage path contains a symlink or non-directory component."
            ) from exc

    def _parent_fd(self, name: str, *, create: bool) -> int:
        return self._directory_fd(name.split("/")[:-1], create=create)

    @staticmethod
    def _verify_regular(info: os.stat_result) -> None:
        if stat.S_ISLNK(info.st_mode):
            raise SuspiciousFileOperation("Symbolic links cannot be storage objects.")
        if not stat.S_ISREG(info.st_mode):
            raise StorageError("Storage object must be a regular file.")

    @classmethod
    def _stat_at(cls, fd: int, name: str) -> os.stat_result:
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        cls._verify_regular(info)
        return info

    @staticmethod
    def _new_temporary(fd: int) -> tuple[str, int]:
        for _ in range(10):
            name = ".async-storage-" + secrets.token_hex(16) + ".tmp"
            try:
                handle = os.open(name, _FILE_CREATE_FLAGS, 0o600, dir_fd=fd)
                return name, handle
            except FileExistsError:
                continue
        raise StorageError("Unable to allocate an upload staging file.")

    async def save(
        self,
        name: str,
        content: Content,
        *,
        max_length: int | None,
        on_conflict: ConflictPolicy,
    ) -> str:
        parent_fd = self._parent_fd(name, create=True)
        temporary: str | None = None
        try:
            temporary, output_fd = self._new_temporary(parent_fd)
            try:
                await write_content(output_fd, content)
                if self.file_permissions_mode is not None:
                    getattr(os, "fchmod")(output_fd, self.file_permissions_mode)  # noqa: B009 - POSIX-only API, absent from Windows stubs.
            finally:
                close_fd(output_fd)

            leaf = name.rsplit("/", 1)[-1]
            if on_conflict == "overwrite":
                with suppress(FileNotFoundError):
                    self._stat_at(parent_fd, leaf)
                # Replaces a directory entry, not the object reached by a symlink.
                # The stat above rejects a preexisting symlink as unsafe.
                os.replace(temporary, leaf, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
                return name

            candidate = name
            for _ in range(100):
                leaf = candidate.rsplit("/", 1)[-1]
                try:
                    # Hard-linking a fully written staging file provides atomic
                    # no-clobber publication; a collision never consumes content twice.
                    os.link(
                        temporary,
                        leaf,
                        src_dir_fd=parent_fd,
                        dst_dir_fd=parent_fd,
                        follow_symlinks=False,
                    )
                    return candidate
                except FileExistsError as exc:
                    if on_conflict == "error":
                        raise FileAlreadyExists(candidate) from exc
                    candidate = alternative_name(name, max_length=max_length)
            raise FileAlreadyExists("Unable to allocate a unique storage name.")
        finally:
            try:
                if temporary is not None:
                    with suppress(FileNotFoundError):
                        os.unlink(temporary, dir_fd=parent_fd)
            finally:
                close_fd(parent_fd)

    def _open_regular_file(self, name: str) -> int:
        parent_fd = self._parent_fd(name, create=False)
        try:
            try:
                handle = os.open(
                    name.rsplit("/", 1)[-1], _FILE_READ_FLAGS, dir_fd=parent_fd
                )
            except OSError as exc:
                self._handle_unsafe_component(exc)
                raise
            try:
                self._verify_regular(os.fstat(handle))
            except BaseException:
                close_fd(handle)
                raise
            return handle
        finally:
            close_fd(parent_fd)

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
        fd = self._parent_fd(name, create=False)
        try:
            return self._stat_at(fd, name.rsplit("/", 1)[-1])
        finally:
            close_fd(fd)

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
            fd = self._parent_fd(name, create=False)
        except FileNotFoundError:
            return False
        try:
            leaf = name.rsplit("/", 1)[-1]
            try:
                self._stat_at(fd, leaf)
            except FileNotFoundError:
                return False
            try:
                os.unlink(leaf, dir_fd=fd)
            except FileNotFoundError:
                return False
            except IsADirectoryError as exc:
                raise StorageError("delete() only removes files.") from exc
            return True
        finally:
            close_fd(fd)

    async def delete(self, name: str) -> bool:
        return await asyncio.to_thread(self._delete, name)

    def _listdir(self, path: str) -> tuple[list[str], list[str]]:
        fd = self._directory_fd(path.split("/") if path else [], create=False)
        try:
            directories: list[str] = []
            files: list[str] = []
            for entry in os.listdir(fd):
                if is_staging_name(entry):
                    continue
                try:
                    info = os.stat(entry, dir_fd=fd, follow_symlinks=False)
                except FileNotFoundError:
                    # Entry was concurrently removed; no unsafe path is followed.
                    continue
                if stat.S_ISLNK(info.st_mode):
                    raise SuspiciousFileOperation(
                        "Storage directory contains a symbolic link."
                    )
                if stat.S_ISDIR(info.st_mode):
                    directories.append(entry)
                elif stat.S_ISREG(info.st_mode):
                    files.append(entry)
                else:
                    raise StorageError("Storage directory contains a non-file object.")
            return sorted(directories), sorted(files)
        finally:
            close_fd(fd)

    async def listdir(self, path: str) -> tuple[list[str], list[str]]:
        try:
            return await asyncio.to_thread(self._listdir, path)
        except FileNotFoundError as exc:
            raise FileNotFound(path) from exc
