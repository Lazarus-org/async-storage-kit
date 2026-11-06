"""Bounded-size async adapter for common file content sources."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterable, AsyncIterator

from ._async import wait_for_completion
from .types import Content

DEFAULT_CHUNK_SIZE = 256 * 1024


async def iter_content(
    content: Content, chunk_size: int = DEFAULT_CHUNK_SIZE
) -> AsyncIterator[bytes]:
    """Yield bytes without reading a file-like object into memory all at once.

    File-like objects are consumed from their *current* position and are never
    closed or rewound by this helper. The caller retains ownership of inputs.
    """
    if chunk_size < 1:
        raise ValueError("chunk_size must be positive.")
    if isinstance(content, (bytes, bytearray, memoryview)):
        for offset in range(0, len(content), chunk_size):
            yield bytes(content[offset : offset + chunk_size])
        return
    if isinstance(content, AsyncIterable):
        async for chunk in content:
            if not isinstance(chunk, (bytes, bytearray, memoryview)):
                raise TypeError("Async content streams must yield bytes.")
            if chunk:
                # Split unexpectedly large producer chunks where feasible.
                for offset in range(0, len(chunk), chunk_size):
                    yield bytes(chunk[offset : offset + chunk_size])
        return
    reader = getattr(content, "read", None)
    if not callable(reader):
        raise TypeError(
            "Content must be bytes, a binary file, or an async byte iterable."
        )
    while True:
        if inspect.iscoroutinefunction(reader):
            data = await reader(chunk_size)
        else:
            data = await wait_for_completion(asyncio.to_thread(reader, chunk_size))
            if inspect.isawaitable(data):
                data = await data
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("Binary file reads must return bytes.")
        if not data:
            return
        for offset in range(0, len(data), chunk_size):
            yield bytes(data[offset : offset + chunk_size])
