"""Run with: python examples/basic.py after pip install -e ."""

import asyncio
from io import BytesIO
from tempfile import TemporaryDirectory

from async_storage_kit import FileSystemStorage, MemoryStorage, StorageRegistry


async def main() -> None:
    with TemporaryDirectory() as location:
        registry = StorageRegistry(
            {
                "memory": MemoryStorage(),
                "files": lambda: FileSystemStorage(location, base_url="/media/"),
            }
        )
        uploaded = await registry["files"].save(
            "reports/example.txt", BytesIO(b"hello")
        )
        print(uploaded, await registry["files"].read(uploaded))
        in_memory = await registry["memory"].save("test.txt", b"another example")
        print(in_memory, await registry["memory"].size(in_memory))


if __name__ == "__main__":
    asyncio.run(main())
