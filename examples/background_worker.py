"""Framework-independent report job. Run: python examples/background_worker.py."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable
from tempfile import TemporaryDirectory

from async_storage_kit import AsyncStorage, FileSystemStorage


async def write_report(
    storage: AsyncStorage, name: str, rows: AsyncIterable[bytes]
) -> str:
    """Await this job from an existing asyncio worker or scheduled coroutine."""
    return await storage.save(name, rows, on_conflict="overwrite")


async def main() -> None:
    with TemporaryDirectory() as directory:
        storage = FileSystemStorage(directory)

        async def rows() -> AsyncIterable[bytes]:
            yield b"id,status\n"
            yield b"1,ready\n"

        name = await write_report(storage, "reports/status.csv", rows())
        assert await storage.read(name) == b"id,status\n1,ready\n"
        print(name)


if __name__ == "__main__":
    # Use asyncio.run only at a synchronous CLI/process entry point.
    asyncio.run(main())
