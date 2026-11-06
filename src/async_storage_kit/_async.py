"""Keep resources alive until an outstanding I/O operation finishes."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import TypeVar

_T = TypeVar("_T")


async def wait_for_completion(operation: Awaitable[_T]) -> _T:
    """Defer cancellation until I/O finishes, including repeated cancellation."""
    worker = asyncio.ensure_future(operation)
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(worker)
            if cancelled:
                raise asyncio.CancelledError
            return result
        except asyncio.CancelledError:
            cancelled = True
            if worker.done():
                if not worker.cancelled():
                    worker.exception()
                raise
        except Exception:
            if cancelled:
                raise asyncio.CancelledError from None
            raise
