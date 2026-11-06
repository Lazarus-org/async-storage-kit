from __future__ import annotations

import pytest

from async_storage_kit import MemoryStorage


@pytest.fixture(params=["memory", "filesystem"])
def backend(request: pytest.FixtureRequest, tmp_path):
    if request.param == "memory":
        return MemoryStorage()
    from async_storage_kit import FileSystemStorage

    return FileSystemStorage(tmp_path / "media", base_url="/media/")
