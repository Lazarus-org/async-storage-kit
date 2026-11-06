"""Small, dependency-free I/O primitives shared by filesystem drivers."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Callable
from typing import TypeVar

from .._async import wait_for_completion
from ..streams import iter_content
from ..types import Content

_T = TypeVar("_T")


async def _fd_io(function: Callable[..., _T], *args: object) -> _T:
    """Wait for a threaded descriptor operation to finish even if cancelled.

    Closing a descriptor while its worker thread is still using it could allow
    a later, unrelated descriptor to be reused. This helper defers cancellation
    just until the outstanding read/write finishes, then propagates it.
    """
    return await wait_for_completion(asyncio.to_thread(function, *args))


async def write_content(fd: int, content: Content) -> None:
    """Write a one-shot content source using bounded, thread-offloaded calls."""
    async for chunk in iter_content(content):
        await _fd_io(_write_all, fd, chunk)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("Filesystem write made no progress.")
        view = view[written:]


async def read_chunks(fd: int, chunk_size: int) -> AsyncIterator[bytes]:
    while chunk := await _fd_io(os.read, fd, chunk_size):
        yield chunk


def close_fd(fd: int) -> None:
    os.close(fd)
