"""Optional example: install 'async-storage-kit[s3,fastapi]' to run."""

from __future__ import annotations

import os
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from async_storage_kit import S3Storage, StorageBackend, StorageRegistry
from async_storage_kit.integrations.fastapi import storage_dependency

app = FastAPI()
registry = StorageRegistry(
    {
        "default": lambda: S3Storage(
            bucket_name=os.environ["APP_STORAGE_BUCKET"],
            region_name=os.getenv("AWS_REGION"),
            # IAM role / workload identity / environment credentials by default.
        ),
    }
)

get_storage = storage_dependency(registry)


@app.get("/files/{filename:path}")
async def download(
    filename: str, storage: Annotated[StorageBackend, Depends(get_storage)]
):
    # Apply your own authorization policy before serving private file contents.
    if not await storage.exists(filename):
        raise HTTPException(404, "File not found")
    return StreamingResponse(
        storage.iter_chunks(filename), media_type="application/octet-stream"
    )
